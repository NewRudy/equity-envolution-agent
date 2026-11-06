from __future__ import annotations

import io
import json
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from openpyxl import load_workbook

from equity_agent.demo import demo_project
from equity_agent.financials import parse_financial_file
from equity_agent.model_review import ModelConfig, suggest
from equity_agent.opc_package import read_opc_package
from equity_agent.project import Profile, dump_project, restore_project
from equity_agent.screening import Candidate, Pool, removed_by_rule, run_screen
from equity_agent.workflow import CHECKS, compute, draft, inspect, workbench_details
from equity_agent.workpaper import create_workpaper_excel


class WorkbenchTests(unittest.TestCase):
    def test_rule_first_hit_is_unique_and_missing_evidence_stays_in_queue(self):
        rows = (
            Candidate("200001", "*ST合成", ("器械",), market="B股", listed_on="2026-08-01"),
            Candidate("000002", "待证公司", ("器械",), business="代理植入设备"),
            Candidate("000003", "边界公司", ("器械",), market="深", listed_on="2024-11-04", products=("植入器械",)),
            Candidate("000004", "其他行业", ("软件",), market="深"),
        )
        pool = Pool("行业测试", rows, "历史快照", "2026-10-28")
        cfg = dict(tags=("器械",), terms=("植入",), caution_terms=("代理",))
        screen = run_screen(pool, as_of="2026-11-04", **cfg)
        self.assertEqual((screen.scope, screen.hard_removed, removed_by_rule(screen)), (3, 1, {"H01": 1}))
        states = {r.candidate.code: (r.gate, r.suggestion) for r in screen.rows}
        self.assertEqual(states["000002"], ("待核验", "差异提示"))
        self.assertEqual(states["000003"], ("通过", "优先核对"))
        with self.assertRaisesRegex(ValueError, "晚于基准日"):
            run_screen(pool, as_of="2025-11-04", **cfg)

    def test_restoring_keeps_decisions_but_changing_scope_requires_review(self):
        p = demo_project()
        compute(p, dict.fromkeys(CHECKS, True), "演示")
        reopened = restore_project(dump_project(p))
        self.assertEqual(len(reopened.selected()), 4)
        self.assertIsNone(reopened.result)
        reopened.save_profile(replace(reopened.profile, benchmark="2026-11-05"))
        self.assertEqual(reopened.selected(), [])
        self.assertTrue(any(item["action"] == "三态复核" for item in reopened.audit))
        self.assertTrue(any("重新生成" in item for item in inspect(reopened)["blockers"]))

    def test_pe_blocks_future_quote_and_mismatched_basis(self):
        p = demo_project()
        p.policy["method"] = "P/E"
        p.peers["DEMO-01"]["pricing_date"] = "2026-11-05"
        p.peers["DEMO-02"]["earnings_basis"] = "扣非归母净利润"
        result = compute(p, dict.fromkeys(CHECKS, True), "演示")
        problems = " ".join(result["market_method"]["blockers"])
        self.assertIn("不晚于基准日", problems)
        self.assertIn("一致的净利润口径", problems)
        self.assertIsNone(p.result)
        p.peers["DEMO-01"]["pricing_date"] = "2026-11-04"
        p.peers["DEMO-02"]["earnings_basis"] = "归母净利润"
        out = compute(p, dict.fromkeys(CHECKS, True), "演示")["market_method"]["result"]
        expected = 0.9 * (18 / .9 + 24 / 1.08 + 15.5 / .81 + 28 / 1.12) / 4
        self.assertAlmostEqual(out["equity_value"], expected)

    def test_dcf_remains_separate_and_checks_assumptions(self):
        p = demo_project()
        p.policy.update(include_dcf=True, wacc_pct=2.0)
        result = compute(p, dict.fromkeys(CHECKS, True), "演示")
        self.assertTrue(result["market_method"]["blockers"])
        p.policy["wacc_pct"] = 12.0
        result = compute(p, dict.fromkeys(CHECKS, True), "演示")
        self.assertIn("dcf", result["valuations"])
        self.assertAlmostEqual(result["market_method"]["result"]["equity_value"], 17.175)

    def test_financials_refuse_quarterly_values_as_full_year(self):
        payload = "报告期,净利润（万元）\n2025-09-30,18000\n".encode("utf-8")
        with self.assertRaisesRegex(ValueError, "完整会计年度"):
            parse_financial_file("季报.csv", payload)

    def test_screening_workpaper_can_be_exported_without_value_and_preserves_literal_text(self):
        p = demo_project()
        p.note("备注", "=1+1", actor="测试")
        content = create_workpaper_excel(p.profile.name, p.records, draft(p), workbench_details(p))
        wb = load_workbook(io.BytesIO(content), data_only=False)
        self.assertIn("候选与三态复核", wb.sheetnames)
        self.assertEqual(wb["候选与三态复核"].max_row, 9)
        self.assertIsNone(wb["估值计算"]["C2"].value)
        note = wb["操作记录"].cell(wb["操作记录"].max_row, 5)
        self.assertEqual((note.value, note.data_type), ("=1+1", "s"))

    def test_opc_ingest_preserves_actual_snapshot_date_and_keeps_model_separate(self):
        company = {"code": "000001", "name": "虚构候选", "industry": "工业软件", "main_biz": "软件订阅"}
        metadata = {"snapshot_date": "2026-10-28", "report_date": "2026-09-30"}
        archive = {"id": "17", "label": "测试案例", "profile": {"rows": [{"q": "主营产品", "a": "软件"}]},
                   "items": [{"code": "000001", "verdict": "是", "reason": "订阅模式相似", "products": ["软件"]}],
                   "stats": {"pool": 2, "llm": 1, "yes": 1, "no": 0, "doubt": 0}}
        doc = "\n".join(f"const {key} = {json.dumps(value, ensure_ascii=False)};" for key, value in {
            "SNAPSHOT": {"meta": metadata, "nodes": {"工业软件": ["000001"]}, "companies": [company, {"code": "000002", "name": "测试B"}]},
            "LLM20": [archive], "RULESET": {"meta": {"version": "2.0"}},
        }.items())
        file = io.BytesIO()
        with ZipFile(file, "w") as z:
            z.writestr("01_前端/工作台v4_测试.html", doc)
            z.writestr("03_配置/.env", "DO_NOT_READ=fixture")
        package = read_opc_package("演练.zip", file.getvalue())
        s = package.cases[0].screening
        self.assertEqual((s.snapshot_date, s.as_of, s.scope, len(s.rows)), ("2026-10-28", "2026-11-04", 2, 1))
        self.assertEqual(s.rows[0].suggestion, "模型建议纳入")

    def test_optional_model_requires_quotes_from_real_candidate_text(self):
        config = ModelConfig("https://example.invalid/v1/chat/completions", "fixture-model", "fixture-token", "example.invalid")
        response = MagicMock()
        response.__enter__.return_value = response
        reply = {"verdict": "业务相近", "reason": "虚构依据", "evidence": ["它提供长期工厂订阅"]}
        response.read.return_value = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
        with patch("equity_agent.model_review.build_opener") as opener:
            opener.return_value.open.return_value = response
            answer = suggest(config, Profile(name="不发送的标的名", products="排产软件"), Candidate("01", "候选", business="一次性设备代理"))
            request = opener.return_value.open.call_args.args[0]
            self.assertNotIn("不发送的标的名", request.data.decode())
        self.assertEqual(answer["verdict"], "证据不足")
        self.assertEqual(answer["evidence"], [])
