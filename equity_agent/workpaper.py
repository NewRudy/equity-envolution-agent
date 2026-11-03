from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .financials import FinancialRecord


def create_workpaper_excel(
    company_name: str,
    records: list[FinancialRecord],
    result: dict[str, Any],
) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "结论摘要"
    is_demo = result.get("is_demo", False)
    summary.append(["合成示例底稿（不可用于真实估值）" if is_demo else "股权估值工作底稿（草稿）"])
    summary.append(["目标公司", company_name])
    summary.append(["分析状态", result.get("status", "待复核")])
    summary.append(["市场法复核人", result.get("market_method", {}).get("reviewer", "未填写")])
    summary.append(["市场法状态", result.get("market_method", {}).get("status", "待补充")])
    summary.append(["运行时间", result.get("run_at", "未记录")])
    summary.append(["范围边界", result.get("scope_note", "估值分析草稿，须由专业人员复核。")])
    summary.append([])
    summary.append(["方法", "低值（亿元）", "基准值（亿元）", "高值（亿元）", "说明"])
    valuations = result.get("valuations", {})
    dcf = valuations.get("dcf", {})
    dcf_values = [row.get("equity_value") for row in (dcf.get("bear"), dcf.get("base"), dcf.get("bull")) if row]
    if dcf_values:
        summary.append(["DCF 参照", min(dcf_values), dcf.get("base", {}).get("equity_value"), max(dcf_values), "敏感性观察，不是统计置信区间"])
    elif result.get("include_dcf", True):
        summary.append(["DCF 参照", None, None, None, "未执行或关键数据/假设未通过检查"])
    market = result.get("market_method", {})
    market_result = market.get("result")
    if market_result:
        summary.append([
            market.get("method"),
            market_result.get("low_equity_value"),
            market_result.get("equity_value"),
            market_result.get("high_equity_value"),
            market_result.get("range_note"),
        ])
    else:
        summary.append([market.get("method", "市场法"), None, None, None, "未满足市场法计算/复核条件；请查看 Review_Queue"])
    summary.append([])
    summary.append(["市场法公式", _market_formula(market.get("method", "EV/EBITDA"))])
    summary.append(["市场倍数", market.get("weighted_multiple"), "经修正、按人工权重加权；倍数来源见 Comparable_Analysis"])

    assumptions = workbook.create_sheet("假设与口径")
    assumptions.append(["假设项", "取值", "说明"])
    assumption_obj = result.get("assumptions")
    assumption_values = vars(assumption_obj) if assumption_obj and result.get("include_dcf", True) else {}
    for key, label, unit in (
        ("revenue_growth", "预测收入年增长率", "%"),
        ("fcf_margin", "预测自由现金流率", "%"),
        ("wacc", "WACC", "%"),
        ("terminal_growth", "永续增长率", "%"),
        ("forecast_years", "预测年数", "年"),
    ):
        value = assumption_values.get(key)
        if value is not None:
            display = value * 100 if unit == "%" else value
            assumptions.append([label, display, f"{unit}；使用者输入，需结合原始依据复核"])
    assumptions.append(["目标公司净债务", market.get("target_net_debt"), "最新年度财务表；EV/EBITDA 法使用"])
    assumptions.append(["目标账面权益口径", market.get("target_book_equity_basis"), "P/B 法使用"])
    assumptions.append(["目标数据来源", market.get("target_source"), "所选最新年度目标公司记录"])
    assumptions.append(["目标指标定位", "; ".join(f"{key}={value}" for key, value in market.get("target_provenance", {}).items()), "回到财务数据来源页核验"])
    assumptions.append(["流动性折扣", market.get("dlom_pct"), "百分数；理由和来源由复核人填写"])
    assumptions.append(["流动性折扣理由", market.get("dlom_reason"), market.get("dlom_source")])
    assumptions.append(["其他股权价值调整", market.get("bridge_adjustment"), "亿元；按折扣后加减，理由和来源由复核人填写"])
    assumptions.append(["其他调整理由", market.get("bridge_reason"), market.get("bridge_source")])
    assumptions.append(["复核人", market.get("reviewer"), "人工确认，不代表正式评估结论"])
    confirmations = market.get("confirmations", {})
    assumptions.append(["复核确认", "状态", "确认项"])
    for key, label in (
        ("peer_review", "已复核可比范围、报告期、取价依据、来源和入选理由"),
        ("target_review", "已核实目标公司指标、单位、期间和合并口径"),
        ("bridge_review", "已确认桥接和其他股权调整口径"),
        ("discount_review", "已确认流动性折扣取值"),
    ):
        state = "演示预设，非人工复核" if is_demo else ("是" if confirmations.get(key) else "否")
        assumptions.append(["确认", state, label])

    financials = workbook.create_sheet("财务数据来源")
    financials.append(["年度", "营业收入（亿元）", "EBITDA（亿元）", "净利润（亿元）", "经营现金流（亿元）", "资本开支（亿元）", "净债务（亿元）", "账面权益（亿元）", "来源", "字段定位"])
    fields = ["revenue", "ebitda", "net_income", "operating_cash_flow", "capex", "net_debt", "book_equity"]
    for record in records:
        financials.append([
            record.year,
            *[record.metrics.get(field) for field in fields],
            record.source,
            "; ".join(f"{field}={record.provenance.get(field, '未提供')}" for field in fields),
        ])

    peer_sheet = workbook.create_sheet("Comparable_Analysis")
    peer_sheet.append([
        "纳入公司", "报告期", "取价依据", "股权市值（亿元）", "净债务（亿元）", "其他 EV 调整（亿元）", "EV 调整说明", "EBITDA（亿元）",
        "账面权益（亿元）", "账面权益口径", "未修正倍数", "修正系数", "修正后倍数", "权重（%）",
        "入选理由", "修正理由", "数据来源", "原文件位置",
    ])
    for peer in market.get("peer_analysis", []):
        peer_sheet.append([
            peer.get("company"), peer.get("period"), peer.get("price_basis"), peer.get("market_cap"),
            peer.get("net_debt"), peer.get("other_ev_adjustment"), peer.get("ev_adjustment_reason"),
            peer.get("ebitda"), peer.get("book_equity"), peer.get("book_equity_basis"),
            peer.get("raw_multiple"), peer.get("adjustment_factor"), peer.get("adjusted_multiple"),
            peer.get("weight_pct"), peer.get("selection_reason"), peer.get("adjustment_reason"),
            peer.get("source"), peer.get("source_location"),
        ])
    peer_sheet.append([])
    peer_sheet.append(["选用倍数", market.get("weighted_multiple")])

    valuation_sheet = workbook.create_sheet("估值计算")
    valuation_sheet.append(["方法/情景", "企业价值（亿元）", "股权价值（亿元）", "计算信息"])
    for scenario, row in dcf.items():
        valuation_sheet.append([f"DCF / {scenario}", row.get("enterprise_value"), row.get("equity_value"), "现金流折现，固定公式计算"])
    if market_result:
        valuation_sheet.append([
            market.get("method"), None, market_result.get("equity_value"),
            f"加权修正倍数 {market_result.get('weighted_multiple'):.4g}x；DLOM {market.get('dlom_pct')}%",
        ])
        valuation_sheet.append(["市场法低端", None, market_result.get("low_equity_value"), "所选样本修正倍数最小值"])
        valuation_sheet.append(["市场法高端", None, market_result.get("high_equity_value"), "所选样本修正倍数最大值"])
    valuation_sheet.append([])
    valuation_sheet.append(["EV/EBITDA 公式", "可比 EV = 股权市值 + 净债务 + 其他 EV 调整；目标股权价值 = (目标 EBITDA × 加权修正倍数 - 目标净债务) × (1 - DLOM) + 其他股权调整。"])
    valuation_sheet.append(["P/B 公式", "可比 P/B = 股权市值 / 账面权益；目标股权价值 = 目标账面权益 × 加权修正倍数 × (1 - DLOM) + 其他股权调整。"])
    if result.get("include_dcf", True):
        valuation_sheet.append(["DCF 公式", "逐年预测收入和自由现金流并折现；股权价值 = 企业价值 - 最新年度净债务。"])

    review = workbook.create_sheet("Review_Queue")
    review.append(["级别", "待处理事项"])
    for item in result.get("warnings", []):
        review.append([item.get("severity"), item.get("message")])
    for blocker in market.get("blockers", []):
        review.append(["市场法门槛", blocker])
    review.append([])
    review.append(["建议动作"])
    for action in result.get("next_actions", []):
        review.append([action])

    _format_workbook(workbook)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _market_formula(method: str) -> str:
    if method == "P/B":
        return "目标账面权益 × 加权修正 P/B × (1 - 流动性折扣) + 其他股权调整"
    return "(目标 EBITDA × 加权修正 EV/EBITDA - 目标净债务) × (1 - 流动性折扣) + 其他股权调整"


def _format_workbook(workbook: Workbook) -> None:
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions if sheet.max_row > 1 else "A1"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="15324B")
        for column in sheet.columns:
            column_letter = get_column_letter(column[0].column)
            max_width = max((len(str(cell.value or "")) for cell in column[:50]), default=10)
            sheet.column_dimensions[column_letter].width = min(max(max_width + 2, 12), 60)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
