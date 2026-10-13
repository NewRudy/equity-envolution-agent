from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .financials import FinancialRecord


@dataclass
class ValuationAssumptions:
    revenue_growth: float = 0.15
    fcf_margin: float = 0.08
    wacc: float = 0.12
    terminal_growth: float = 0.03
    forecast_years: int = 5
    multiple_low: float = 6.0
    multiple_high: float = 10.0


def _fcf(record: FinancialRecord) -> float | None:
    operating_cash_flow = record.metrics.get("operating_cash_flow")
    capex = record.metrics.get("capex")
    if operating_cash_flow is None or capex is None:
        return None
    return operating_cash_flow - abs(capex)


def _dcf(
    revenue: float,
    fcf_margin: float,
    growth: float,
    wacc: float,
    terminal_growth: float,
    forecast_years: int,
    net_debt: float,
) -> dict[str, float]:
    forecast_revenue = revenue
    present_value = 0.0
    final_fcf = 0.0
    for year in range(1, forecast_years + 1):
        forecast_revenue *= 1.0 + growth
        final_fcf = forecast_revenue * fcf_margin
        present_value += final_fcf / ((1.0 + wacc) ** year)
    terminal_value = final_fcf * (1.0 + terminal_growth) / (wacc - terminal_growth)
    terminal_present_value = terminal_value / ((1.0 + wacc) ** forecast_years)
    enterprise_value = present_value + terminal_present_value
    return {
        "enterprise_value": enterprise_value,
        "equity_value": enterprise_value - net_debt,
        "pv_explicit_forecast": present_value,
        "pv_terminal_value": terminal_present_value,
    }


def run_valuation_review(
    records: list[FinancialRecord],
    assumptions: ValuationAssumptions,
) -> dict[str, Any]:
    """Run fixed, explainable checks and valuation tools; never make an investment decision."""
    rows = sorted(records, key=lambda record: int(record.year))
    warnings: list[dict[str, str]] = []
    if not rows:
        return {"status": "无法估值", "warnings": [{"severity": "高", "message": "没有可用财务数据。"}], "valuations": {}}

    years = [row.year for row in rows]
    if len(set(years)) != len(years):
        warnings.append({"severity": "高", "message": "存在重复年度，尚未自动合并；请核对是否为不同口径或重报数据。"})
    if len(rows) < 3:
        warnings.append({"severity": "中", "message": "历史年度少于 3 年，趋势判断和预测可信度有限。"})
    if any("未标单位" in location for row in rows for location in row.provenance.values()):
        warnings.append({
            "severity": "高",
            "message": "至少一个金额列未标注单位，系统暂按人民币亿元读取；请明确单位并重新上传后再采用任何估值结果。",
        })

    latest = rows[-1]
    revenue = latest.metrics.get("revenue")
    ebitda = latest.metrics.get("ebitda")
    net_debt = latest.metrics.get("net_debt")
    if revenue is None or revenue <= 0:
        warnings.append({"severity": "高", "message": "最新年度营业收入缺失或非正数，不能执行估值。"})
    if ebitda is None:
        warnings.append({"severity": "中", "message": "缺少最新年度 EBITDA，EV/EBITDA 可比法无法计算；DCF 仍可单独展示。"})
    if net_debt is None:
        warnings.append({"severity": "高", "message": "缺少最新年度净债务，无法从企业价值转换为股权价值。"})
    if assumptions.wacc <= assumptions.terminal_growth:
        warnings.append({"severity": "高", "message": "WACC 必须大于永续增长率，否则 DCF 终值无效。"})
    if not (0.0 < assumptions.wacc <= 0.50):
        warnings.append({"severity": "高", "message": "WACC 应在 0% 到 50% 之间。"})
    if assumptions.multiple_low <= 0 or assumptions.multiple_high < assumptions.multiple_low:
        warnings.append({"severity": "高", "message": "EV/EBITDA 倍数区间设置无效。"})

    for prior, current in zip(rows, rows[1:]):
        prior_revenue = prior.metrics.get("revenue")
        current_revenue = current.metrics.get("revenue")
        if prior_revenue and current_revenue is not None:
            growth = current_revenue / prior_revenue - 1.0
            if growth < -0.30 or growth > 1.00:
                warnings.append({
                    "severity": "中",
                    "message": f"{prior.year}→{current.year} 收入变动 {growth:.0%}，超出常规预审阈值，请核对并购、口径或异常事项。",
                })
    for row in rows:
        row_revenue = row.metrics.get("revenue")
        row_ebitda = row.metrics.get("ebitda")
        if row_revenue and row_ebitda is not None:
            margin = row_ebitda / row_revenue
            if margin > 0.60 or margin < -0.30:
                warnings.append({"severity": "中", "message": f"{row.year} EBITDA 利润率为 {margin:.0%}，建议核验收入和成本口径。"})
        fcf = _fcf(row)
        if fcf is not None and row_revenue and row.metrics.get("net_income") is not None:
            if row.metrics["net_income"] > 0 and fcf < 0:
                warnings.append({"severity": "中", "message": f"{row.year} 净利润为正但自由现金流为负，需检查应收、存货、资本开支或现金流质量。"})

    invalid = any(item["severity"] == "高" for item in warnings)
    valuations: dict[str, Any] = {}
    if not invalid and revenue is not None and net_debt is not None:
        historical_fcf = _fcf(latest)
        observed_margin = historical_fcf / revenue if historical_fcf is not None else None
        margin = assumptions.fcf_margin if assumptions.fcf_margin is not None else observed_margin
        if margin is None:
            warnings.append({"severity": "高", "message": "缺少经营现金流或资本开支，且没有可复核的 FCF 利润率假设。"})
            invalid = True
        else:
            wacc = assumptions.wacc
            terminal_growth = assumptions.terminal_growth
            if terminal_growth >= wacc:
                invalid = True
            else:
                dcf_scenarios = {
                    "bear": _dcf(revenue, margin - 0.02, assumptions.revenue_growth - 0.05, wacc + 0.01, max(0.0, terminal_growth - 0.005), assumptions.forecast_years, net_debt),
                    "base": _dcf(revenue, margin, assumptions.revenue_growth, wacc, terminal_growth, assumptions.forecast_years, net_debt),
                    "bull": _dcf(revenue, margin + 0.02, assumptions.revenue_growth + 0.05, max(0.01, wacc - 0.01), min(terminal_growth + 0.005, max(0.0, wacc - 0.02)), assumptions.forecast_years, net_debt),
                }
                multiples = None
                if ebitda is not None and ebitda > 0:
                    multiples = {
                        "low": {"multiple": assumptions.multiple_low, "enterprise_value": ebitda * assumptions.multiple_low, "equity_value": ebitda * assumptions.multiple_low - net_debt},
                        "high": {"multiple": assumptions.multiple_high, "enterprise_value": ebitda * assumptions.multiple_high, "equity_value": ebitda * assumptions.multiple_high - net_debt},
                    }
                elif ebitda is not None:
                    warnings.append({"severity": "中", "message": "最新年度 EBITDA 非正，EV/EBITDA 可比法不展示；DCF 仅作单一方法的敏感性观察。"})
                if historical_fcf is None:
                    warnings.append({"severity": "中", "message": "缺少最新年度经营现金流或资本开支；DCF 的 FCF 率完全依赖使用者输入，需重点复核。"})
                valuations = {
                    "dcf": dcf_scenarios,
                    "multiples": multiples,
                    "observed_fcf_margin": observed_margin,
                    "fcf_margin_used": margin,
                    "net_debt_used": net_debt,
                    "latest_year": latest.year,
                    "latest_revenue": revenue,
                    "latest_ebitda": ebitda,
                }

    status = "需优先复核" if any(item["severity"] == "高" for item in warnings) else ("有预警，待人工复核" if warnings else "初步分析完成，待人工复核")
    return {
        "status": status,
        "warnings": warnings,
        "valuations": valuations,
        "assumptions": assumptions,
        "records": rows,
        "next_actions": _next_actions(warnings, valuations),
    }


def _next_actions(warnings: list[dict[str, str]], valuations: dict[str, Any]) -> list[str]:
    actions = []
    if any("净债务" in item["message"] for item in warnings):
        actions.append("补齐有息负债、现金及等价物，并核实净债务计算口径。")
    if any("EBITDA" in item["message"] for item in warnings):
        actions.append("核对 EBITDA 的调整项、一次性项目及审计/管理口径。")
    if any("现金流" in item["message"] or "FCF" in item["message"] for item in warnings):
        actions.append("复核经营现金流、资本开支和营运资金变化，确认 FCF 假设。")
    if any("收入" in item["message"] or "重复年度" in item["message"] for item in warnings):
        actions.append("回到原始报表核验期间、合并范围、单位和重述情况。")
    if valuations:
        actions.append("由估值人员确认增长、FCF 利润率、WACC、永续增长率和倍数来源后再使用区间。")
    if not actions:
        actions.append("检查原始材料与每项数据来源；当前仅为预审线索，不是正式评估结论。")
    return list(dict.fromkeys(actions))
