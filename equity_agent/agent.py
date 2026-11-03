from __future__ import annotations

from typing import Any

from .financials import FinancialRecord
from .market_method import calculate_market_method
from .valuation import ValuationAssumptions, run_valuation_review


def run_agent(
    records: list[FinancialRecord],
    assumptions: ValuationAssumptions,
    market_request: dict[str, Any],
    include_dcf: bool = True,
) -> dict[str, Any]:
    """Run deterministic financial checks, DCF reference, and gated market method."""
    analysis = run_valuation_review(records, assumptions, include_dcf=include_dcf)
    analysis["market_method"] = calculate_market_method(**market_request)
    analysis["agent_steps"] = [
        "读取目标公司财务数据和可比公司输入，保留文件与单元格/行定位。",
        "运行数据质量检查；只有满足可比数量、来源、复核和口径条件时才输出市场法股权价值。",
        "用固定公式计算 DCF 参照与市场法，不联网抓行情、不生成投资判断，并输出可复核底稿。",
    ]
    analysis["scope_note"] = "本工作台生成估值分析草稿，不自动抓取或验证市场数据，不代替专业复核、正式评估或投资决策。"
    return analysis
