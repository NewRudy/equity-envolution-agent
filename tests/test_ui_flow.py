from __future__ import annotations

import io
import unittest
from pathlib import Path

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from equity_agent.demo import demo_comparables, demo_records
from equity_agent.workpaper import create_workpaper_excel


APP = Path(__file__).resolve().parents[1] / "app.py"


def button(app: AppTest, label: str):
    return next(item for item in app.button if item.label == label)


def checkbox(app: AppTest, label: str):
    return next(item for item in app.checkbox if item.label == label)


class GuidedWorkflowTests(unittest.TestCase):
    def assert_no_exception(self, app: AppTest) -> None:
        self.assertEqual([str(item.value) for item in app.exception], [])

    def ready_peers_step(self) -> AppTest:
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        button(app, "开始我的估值").click().run()
        app.session_state["ui_records"] = demo_records()
        app.session_state["ui_financial_name"] = "uploaded-fixture.csv"
        next(item for item in app.text_input if item.label == "你要估值的公司叫什么？").set_value("流程验证公司").run()
        button(app, "下一步：选择可比公司").click().run()
        app.session_state["ui_peers"] = demo_comparables()
        app.session_state["ui_peer_revision"] += 1
        app.run()
        self.assert_no_exception(app)
        return app

    def confirm_inputs(self, app: AppTest) -> None:
        next(item for item in app.text_input if item.label == "本次由谁复核？").set_value("验证人员").run()
        for label in (
            "目标公司的数据已核对",
            "可比公司的业务、期间和取价依据已核对",
            "净债务、权益与其他调整口径已核对",
            "本次流动性折扣与股权调整已确认",
        ):
            checkbox(app, label).check().run()

    def test_complete_demo_has_explicit_fictional_review(self) -> None:
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertEqual([item.label for item in app.button], ["先看完整示例", "开始我的估值"])
        button(app, "先看完整示例").click().run()
        self.assert_no_exception(app)
        result = app.session_state["ui_result"]
        self.assertTrue(result["is_demo"])
        self.assertFalse(result["include_dcf"])
        self.assertAlmostEqual(result["market_method"]["result"]["equity_value"], 17.175)
        workbook = load_workbook(io.BytesIO(create_workpaper_excel("示例", demo_records(), result)), data_only=True)
        self.assertIn("合成示例", workbook["结论摘要"]["A1"].value)
        confirmation_states = [row[1] for row in workbook["假设与口径"].iter_rows(values_only=True) if row[0] == "确认"]
        self.assertEqual(confirmation_states, ["演示预设，非人工复核"] * 4)
        button(app, "开始我的真实项目").click().run()
        self.assertEqual(app.session_state["ui_records"], [])
        self.assertEqual(app.session_state["ui_peers"], [])
        self.assertFalse(app.session_state["ui_policy"]["peer_review"])

    def test_real_flow_preserves_edits_and_requires_confirmation(self) -> None:
        app = self.ready_peers_step()
        next(item for item in app.number_input if item.label == "本公司权重（%）").set_value(40.0).run()
        self.assertEqual(app.session_state["ui_peers"][0]["weight_pct"], 40.0)
        self.assertTrue(button(app, "下一步：确认并计算").disabled)
        button(app, "所选公司采用等权重").click().run()
        self.assertEqual([peer["weight_pct"] for peer in app.session_state["ui_peers"]], [25.0] * 4)
        button(app, "下一步：确认并计算").click().run()
        self.assertTrue(button(app, "生成估值结果").disabled)
        self.confirm_inputs(app)
        self.assertFalse(button(app, "生成估值结果").disabled)
        button(app, "生成估值结果").click().run()
        self.assert_no_exception(app)
        result = app.session_state["ui_result"]
        self.assertFalse(result["is_demo"])
        self.assertFalse(result["include_dcf"])
        self.assertNotIn("dcf", result["valuations"])
        self.assertEqual(result["market_method"]["reviewer"], "验证人员")
        button(app, "返回修改输入").click().run()
        self.assertEqual(next(item for item in app.text_input if item.label == "本次由谁复核？").value, "验证人员")
        button(app, "上一步：可比公司").click().run()
        next(item for item in app.radio if item.label == "选择本次估值方法").set_value("P/B").run()
        self.assertFalse(app.session_state["ui_policy"]["peer_review"])
        self.assertFalse(app.session_state["ui_policy"]["target_review"])
        self.assertIsNone(app.session_state["ui_result"])

    def test_missing_peer_source_cannot_generate_value(self) -> None:
        app = self.ready_peers_step()
        peers = app.session_state["ui_peers"]
        peers[1]["source"] = ""
        app.session_state["ui_peers"] = peers
        button(app, "下一步：确认并计算").click().run()
        self.confirm_inputs(app)
        self.assertTrue(button(app, "生成估值结果").disabled)
        self.assertIsNone(app.session_state["ui_result"])
        self.assert_no_exception(app)

    def test_dcf_is_optional_and_invalid_discount_rate_blocks_submission(self) -> None:
        app = self.ready_peers_step()
        button(app, "下一步：确认并计算").click().run()
        self.confirm_inputs(app)
        checkbox(app, "同时计算 DCF 参照").check().run()
        next(item for item in app.number_input if item.label == "折现率 WACC（%）").set_value(2.0).run()
        self.assertTrue(button(app, "生成估值结果").disabled)
        next(item for item in app.number_input if item.label == "折现率 WACC（%）").set_value(12.0).run()
        self.assertFalse(button(app, "生成估值结果").disabled)
        button(app, "生成估值结果").click().run()
        self.assert_no_exception(app)
        self.assertTrue(app.session_state["ui_result"]["include_dcf"])
        self.assertIn("base", app.session_state["ui_result"]["valuations"]["dcf"])


if __name__ == "__main__":
    unittest.main()
