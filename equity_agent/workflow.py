"""Translate a reviewed workbench project into explicit valuation inputs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .agent import run_agent
from .market_method import PEER_KEYS, calculate_market_method
from .project import AGGREGATIONS, LISTED, METHODS, PRICE_POLICIES, VALUE_DRIVERS, Project, stamp
from .screening import clean, iso_date
from .valuation import ValuationAssumptions


CHECKS = {
    "peer_review": "已核对可比公司的业务、报告期、取价与数据来源",
    "target_review": "已核对标的年度数据、单位及基准日前可取得性",
    "bridge_review": "已核对分母口径、净债务桥接与调整项目",
    "discount_review": "已确认修正、权重、流动性折扣和股权调整",
}


def available_records(project: Project):
    benchmark = iso_date(project.profile.benchmark)
    if not benchmark:
        return []
    return sorted(
        (r for r in project.records if (period := iso_date(f"{r.year}-12-31")) and period <= benchmark),
        key=lambda record: record.year,
    )


def calculation_records(project: Project, year: str | None = None):
    records = available_records(project)
    selected = project.policy["target_year"] if year is None else year
    if not selected:
        return records
    return [r for r in records if r.year <= selected] if any(r.year == selected for r in records) else []


def target_input(project: Project, year: str | None = None) -> dict:
    records = calculation_records(project, year)
    latest = records[-1] if records else None
    blockers = []
    if project.mode == "archive":
        blockers.append("历史回放仅可复核候选并导出，不能用于本次取价估值。")
    if project.profile.route != LISTED:
        blockers.append("当前路径仅做资料归集和留痕，请另行核实案例、交易条件或底层资产。")
    if not project.ready_profile:
        blockers.append("请保存标的名称、主营产品和估值基准日。")
    if not project.profile.source:
        blockers.append("请在标的画像填写业务资料来源。")
    if project.screen and not project.active_screen:
        blockers.append("标的或口径已改动，请重新生成候选池并复核。")
    if (project.policy["target_year"] if year is None else year) and not records:
        blockers.append("已选财报年度不在当前可用报表中。请补齐资料或重新选择年度。")
    if len({record.year for record in records}) != len(records):
        blockers.append("标的财务表有重复年度，请核实后重新上传。")
    if any("未标单位" in location for record in records for location in record.provenance.values()):
        blockers.append("标的财务表存在未标单位的金额，请在列名注明金额单位。")
    return {
        "benchmark": project.profile.benchmark, "enforce_dates": True,
        "year": latest.year if latest else None, "report_end": f"{latest.year}-12-31" if latest else "",
        **{key: latest.metrics.get(key) if latest else None for key in ("ebitda", "net_debt", "book_equity", "net_income")},
        **{key: project.policy[key] for key in ("book_equity_basis", "earnings_basis", "method_reason")},
        "source": latest.source if latest else "", "provenance": latest.provenance if latest else {},
        "data_quality_blockers": blockers,
    }


def method_indicators(project: Project, year: str | None = None, driver: str | None = None) -> dict[str, dict]:
    """Apply the calculator's checks to each candidate without approving a valuation."""
    adopted = project.adopted()
    target = target_input(project, year)
    request = market_request(project, {}, "")
    request.update(target=target, peer_rows=list(adopted.values()), aggregation="equal")
    options = {}
    for method in METHODS:
        check = calculate_market_method(**(request | {"method": method}))
        aligned = []
        gaps = {}
        for key, peer in zip(adopted, check["peer_analysis"]):
            if peer["data_gaps"]:
                gaps[key] = peer["data_gaps"]
            else:
                aligned.append(key)
        target_ready = check["denominator_ready"] and not target["data_quality_blockers"]
        options[method] = {
            "target_ready": target_ready,
            "aligned": aligned if target_ready else [],
            "gaps": gaps,
            "recommended": False,
        }
    driver = project.policy["value_driver"] if driver is None else driver
    if driver in options and len(options[driver]["aligned"]) >= 3:
        options[driver]["recommended"] = True
        return options
    best = max(len(item["aligned"]) for item in options.values())
    if best >= 3:
        for option in options.values():
            option["recommended"] = option["target_ready"] and len(option["aligned"]) == best
    return options


def market_request(project: Project, confirmed: dict[str, bool], reviewer: str) -> dict:
    return {
        "method": project.policy["method"],
        "peer_rows": project.selected(project.policy["method"]),
        "target": target_input(project),
        "confirmations": {key: bool(confirmed.get(key)) for key in CHECKS},
        "reviewer": clean(reviewer, 80),
        **{key: project.policy[key] for key in (
            "dlom_pct", "dlom_reason", "dlom_source", "bridge_adjustment", "bridge_reason", "bridge_source",
            "aggregation", "aggregation_reason", "price_policy", "price_lookback",
        )},
    }


def inspect(project: Project) -> dict:
    return calculate_market_method(**market_request(project, dict.fromkeys(CHECKS, True), "输入预检查"))


def dcf_blockers(project: Project) -> list[str]:
    if not project.policy["include_dcf"]:
        return []
    blockers = []
    records = calculation_records(project)
    revenue = records[-1].metrics.get("revenue") if records else None
    if revenue is None or revenue <= 0:
        blockers.append("选用 DCF 时，请提供正的标的营业收入。")
    if not records or records[-1].metrics.get("net_debt") is None:
        blockers.append("选用 DCF 时，请填写标的净债务。")
    if not 0 < project.policy["wacc_pct"] <= 50 or project.policy["wacc_pct"] <= project.policy["terminal_pct"]:
        blockers.append("DCF 折现率须为 0–50% 内的正值，且大于永续增长率。")
    return blockers


def compute(project: Project, confirmed: dict[str, bool], reviewer: str) -> dict:
    policy = project.policy
    request = market_request(project, confirmed, reviewer)
    blockers = dcf_blockers(project)
    if blockers:
        return {"market_method": {"blockers": blockers}}
    assumptions = ValuationAssumptions(
        revenue_growth=policy["growth_pct"] / 100, fcf_margin=policy["fcf_pct"] / 100,
        wacc=policy["wacc_pct"] / 100, terminal_growth=policy["terminal_pct"] / 100, forecast_years=policy["years"],
    )
    result = run_agent(calculation_records(project), assumptions, request, include_dcf=policy["include_dcf"])
    if not result["market_method"].get("result"):
        return result
    project.reviewer = request["reviewer"]
    project.note("生成估值草稿", f"{policy['method']} · {AGGREGATIONS[policy['aggregation']]} · {len(request['peer_rows'])} 家 · {project.profile.benchmark}",
                 actor=project.reviewer, evidence=policy["method_reason"])
    result.update(run_at=stamp(), is_demo=project.mode == "demo")
    if project.mode == "demo":
        result["status"] = "合成示例"
        result["market_method"]["status"] = "合成示例，非人工复核"
        result["scope_note"] = "公司、取价、财务和复核条件均为合成演示预设。"
    project.result = result
    return result


def draft(project: Project) -> dict:
    if project.result:
        return project.result
    request = market_request(project, {}, project.reviewer)
    market = (
        {"status": "未执行", "reviewer": project.reviewer,
         "blockers": target_input(project)["data_quality_blockers"][:1]}
        if project.mode == "archive" or project.profile.route != LISTED
        else calculate_market_method(**request)
    )
    return {
        "status": {"archive": "历史归档复核", "demo": "合成示例，未测算"}.get(project.mode, "工作草稿，未出值"),
        "run_at": stamp(), "is_demo": project.mode == "demo", "include_dcf": False,
        "scope_note": "候选筛选、人工复核与数据口径留痕；本底稿没有已确认的估值数值。",
        "market_method": market,
    }


def workbench_details(project: Project) -> dict[str, Any]:
    screen = project.active_screen
    selected = project.roster()
    method = project.policy["method"]
    active_samples = set(project.sample_sets.get(method, project.adopted())) if project.profile.route == LISTED and project.mode != "archive" else set()
    rows = []
    for row in selected.values():
        candidate = row.candidate
        decision = project.decision(row)
        model = project.model_advice.get(candidate.key, {})
        if model and model.get("basis") != project.decision_basis(row):
            model = {}
        rows.append({
            "code": candidate.code, "company": candidate.name, "tags": " / ".join(candidate.tags),
            "gate": row.gate, "rule": row.rule, "suggestion": row.suggestion, "suggestion_reason": row.reason,
            "evidence": " / ".join(row.evidence), "source": candidate.source,
            "period": candidate.period, "review_state": decision.state if decision else "待复核" if row.gate != "剔除" else "",
            "review_reason": decision.reason if decision else "", "actor": decision.reviewer if decision else "",
            "review_evidence": decision.evidence if decision else "",
            "review_dimensions": " / ".join(f"{k} {v}" for k, v in decision.dimensions.items()) if decision else "",
            "method_sample": "是" if candidate.key in active_samples and decision and decision.state == "采纳" else "",
            "at": decision.at if decision else "",
            "live_model": model.get("model"),
            "live_advice": " / ".join(filter(None, [model.get("verdict"), model.get("reason"), *model.get("evidence", [])])),
        })
    choices = [
        ("工作路径", project.profile.route, project.route_reason),
        ("已有资料", " / ".join(project.route_materials), ""),
    ]
    for label, value in project.route_documents.get(project.profile.route, {}).items():
        choices.append(("路径取证", label, value))
    if project.profile.route == LISTED and project.mode != "archive":
        policy = project.policy
        choices.extend((
            ("主比率", method, policy["method_reason"]),
            ("价值驱动", VALUE_DRIVERS[policy["value_driver"]], ""),
            ("财报年度", target_input(project)["year"] or "待补", ""),
            ("本法样本", f"{len(project.selected(method))} 家", project.sample_reasons.get(method, "")),
            ("倍数汇总", AGGREGATIONS[policy["aggregation"]], policy["aggregation_reason"]),
            ("取价规则", PRICE_POLICIES[policy["price_policy"]],
             f"{policy['price_lookback']} 个自然日" if policy["price_policy"] == "recent" else ""),
        ))
    return {
        "mode": project.mode,
        "profile": asdict(project.profile),
        "reference_profile": project.reference_profile,
        "scope": {
            "候选来源": screen.source if screen else "人工提交",
            "规则版本": screen.version if screen else "人工提交",
            "快照日期": screen.snapshot_date if screen else "",
            "筛选基准": screen.as_of if screen else "",
            "行业入池": screen.scope if screen else None,
            "规则剔除": screen.hard_removed if screen else None,
            "候选说明": screen.note if screen else "",
            "人工提交": len(project.manual),
            "有效采纳": len(project.selected()),
            "旧筛选是否失效": "是" if project.screen and not screen else "否",
            **(screen.archive_counts if screen else {}),
        },
        "rules": {key: value for key, value in project.rules.items() if key != "run"},
        "choices": choices, "rows": rows, "audit": project.audit,
    }


def blank_peer() -> dict:
    return dict.fromkeys(PEER_KEYS) | {"adjustment_factor": 1.0, "selected": False}
