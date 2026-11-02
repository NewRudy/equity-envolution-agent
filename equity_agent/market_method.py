from __future__ import annotations

import csv
import io
import math
import re
from typing import Any

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


PEER_FIELDS = [
    ("selected", "纳入"),
    ("company", "公司"),
    ("ticker", "代码"),
    ("period", "报告期"),
    ("price_basis", "取价依据（日期/方式）"),
    ("market_cap", "股权市值（亿元）"),
    ("net_debt", "净债务（亿元）"),
    ("other_ev_adjustment", "其他 EV 调整（亿元）"),
    ("ev_adjustment_reason", "EV 调整说明"),
    ("ebitda", "EBITDA（亿元）"),
    ("book_equity", "账面权益（亿元）"),
    ("book_equity_basis", "账面权益口径"),
    ("source", "数据来源"),
    ("selection_reason", "入选理由"),
    ("adjustment_factor", "修正系数"),
    ("weight_pct", "权重（%）"),
    ("adjustment_reason", "修正理由"),
    ("source_location", "原文件位置"),
]

PEER_KEYS = [key for key, _ in PEER_FIELDS]
PEER_LABELS = dict(PEER_FIELDS)
NUMERIC_FIELDS = {
    "market_cap", "net_debt", "other_ev_adjustment", "ebitda", "book_equity", "adjustment_factor", "weight_pct"
}

_HEADER_ALIASES = {
    "selected": ("纳入", "是否纳入", "选择", "selected", "include"),
    "company": ("公司", "公司名称", "证券名称", "可比公司", "company", "name"),
    "ticker": ("代码", "证券代码", "股票代码", "ticker", "symbol"),
    "period": ("报告期", "财报期", "财务报告期", "period", "reportingperiod"),
    "price_basis": ("取价依据", "取价日期方式", "取价日期", "价格日期", "pricingbasis"),
    "market_cap": ("股权市值", "市值", "总市值", "marketcap", "equityvalue"),
    "net_debt": ("净债务", "净负债", "净债务亿元", "netdebt"),
    "other_ev_adjustment": ("其他ev调整", "ev调整", "少数股东及其他融资调整", "evadjustment"),
    "ev_adjustment_reason": ("ev调整说明", "ev调整理由", "ev桥接说明", "evadjustmentreason"),
    "ebitda": ("ebitda", "息税折旧摊销前利润"),
    "book_equity": ("账面权益", "净资产", "归母净资产", "所有者权益", "bookequity"),
    "book_equity_basis": ("账面权益口径", "净资产口径", "权益口径", "bookequitybasis"),
    "source": ("数据来源", "来源", "数据来源定位", "source", "datasource"),
    "selection_reason": ("入选理由", "可比理由", "选择理由", "selectionreason"),
    "adjustment_factor": ("修正系数", "调整系数", "factor", "adjustmentfactor"),
    "weight_pct": ("权重", "权重%", "weight", "weightpct"),
    "adjustment_reason": ("修正理由", "调整理由", "factorreason", "adjustmentreason"),
}


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s（）()【】\[\]：:，,._\-—/%]+", "", text)
    text = re.sub(r"(亿元|万元|百万元|千元|元)$", "", text)
    return text


_NORMALIZED_ALIASES = {
    key: {_normalize_header(alias) for alias in aliases}
    for key, aliases in _HEADER_ALIASES.items()
}


def _canonical_headers(values: list[Any]) -> list[str | None]:
    output = []
    for value in values:
        normalized = _normalize_header(value)
        field = next((key for key, aliases in _NORMALIZED_ALIASES.items() if normalized in aliases), None)
        output.append(field)
    return output


def _empty_row() -> dict[str, Any]:
    row: dict[str, Any] = {key: None for key in PEER_KEYS}
    row["selected"] = False
    row["adjustment_factor"] = 1.0
    row["weight_pct"] = None
    return row


def empty_comparables_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=PEER_KEYS)


def parse_comparable_file(filename: str, data: bytes) -> pd.DataFrame:
    """Read a comparable-company file and preserve its sheet/row location."""
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030")
        rows = list(csv.reader(io.StringIO(text)))
        return _rows_to_frame(filename, "CSV", rows)
    if suffix in {"xlsx", "xlsm"}:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                rows = [list(row) for row in sheet.iter_rows(values_only=True)]
                if _find_header(rows) is not None:
                    return _rows_to_frame(filename, sheet.title, rows)
        finally:
            workbook.close()
        raise ValueError("没有找到可比公司表头。请使用模板，至少包含“公司”和“股权市值（亿元）”。")
    raise ValueError("可比公司文件只支持 .xlsx、.xlsm 或 .csv。")


def _find_header(rows: list[list[Any]]) -> tuple[int, list[str | None]] | None:
    for index, row in enumerate(rows[:30]):
        headers = _canonical_headers(row)
        if "company" in headers and "market_cap" in headers:
            return index, headers
    return None


def _rows_to_frame(filename: str, sheet_name: str, rows: list[list[Any]]) -> pd.DataFrame:
    found = _find_header(rows)
    if found is None:
        raise ValueError("没有找到可比公司表头。请使用模板，至少包含“公司”和“股权市值（亿元）”。")
    header_index, headers = found
    output = []
    for row_index, source_row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        record = _empty_row()
        has_value = False
        for column, field in enumerate(headers):
            if field is None or column >= len(source_row):
                continue
            value = source_row[column]
            if value is not None and str(value).strip() != "":
                has_value = True
            if field == "selected":
                record[field] = _as_bool(value)
            else:
                record[field] = value
        if not has_value:
            continue
        record["source_location"] = f"{filename} / {sheet_name} / 第 {row_index} 行"
        output.append(record)
    frame = pd.DataFrame(output, columns=PEER_KEYS)
    if frame.empty:
        return empty_comparables_frame()
    for key in NUMERIC_FIELDS:
        frame[key] = pd.to_numeric(frame[key], errors="coerce")
    frame["selected"] = frame["selected"].map(_as_bool).astype(bool)
    return frame


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是", "纳入", "已选"}


def comparable_template_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Comparables"
    sheet.append([label for key, label in PEER_FIELDS if key != "source_location"])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="15324B")
    widths = [10, 22, 14, 14, 24, 20, 18, 22, 24, 18, 20, 20, 36, 30, 16, 14, 30]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    notes = workbook.create_sheet("Instructions")
    notes.append(["字段", "填写说明"])
    notes.append(["金额单位", "股权市值、净债务、其他 EV 调整、EBITDA、账面权益均按人民币亿元填写；负净债务代表净现金。"])
    notes.append(["EV 桥接", "EV = 股权市值 + 净债务 + 其他 EV 调整。其他 EV 调整用于少数股东权益、优先股或其他融资项目；无调整也请明确填 0。"])
    notes.append(["市场数据", "请人工收集并注明来源、价格日期/方式和财报期。本工具不抓取行情、不自动选择可比公司。"])
    notes.append(["纳入", "只把经人工审阅的公司标为 TRUE/是；至少选择 3 家、最多 8 家。"])
    notes.append(["权重", "对纳入公司填写权重百分数，合计必须为 100。修正系数填写正数，1.00 表示不修正。"])
    notes.append(["理由", "每家纳入公司均需填写入选理由、修正理由和可回查的数据来源。"])
    notes.append(["EV 调整", "其他 EV 调整非零时，补充 EV 调整说明；确保来源数据覆盖所有重大融资性项目。"])
    for cell in notes[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="15324B")
    notes.column_dimensions["A"].width = 20
    notes.column_dimensions["B"].width = 105
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def calculate_market_method(
    method: str,
    peer_rows: list[dict[str, Any]],
    target: dict[str, Any],
    confirmations: dict[str, bool],
    reviewer: str,
    dlom_pct: float,
    dlom_reason: str,
    dlom_source: str,
    bridge_adjustment: float,
    bridge_reason: str,
    bridge_source: str,
) -> dict[str, Any]:
    """Calculate a reviewer-gated market method. No target value is returned until all gates pass."""
    blockers: list[str] = []
    blockers.extend(str(item) for item in target.get("data_quality_blockers", []))
    selected = [row for row in peer_rows if _as_bool(row.get("selected"))]
    if not 3 <= len(selected) <= 8:
        blockers.append(f"纳入的可比公司须为 3–8 家；当前 {len(selected)} 家。")
    if not reviewer.strip():
        blockers.append("请填写本次复核人姓名。")
    for key, label in (
        ("peer_review", "尚未确认已复核所选公司的业务可比性、报告期、取价依据、来源和入选理由。"),
        ("target_review", "尚未确认目标公司本法所用财务指标、单位和合并口径。"),
        ("bridge_review", "尚未确认净债务/账面权益桥接及其他股权调整口径。"),
        ("discount_review", "尚未确认流动性折扣；如不采用请确认折扣为 0%。"),
    ):
        if not confirmations.get(key):
            blockers.append(label)

    dlom = _number(dlom_pct)
    if dlom is None or not 0 <= dlom <= 80:
        blockers.append("流动性折扣须在 0%–80% 之间。")
        dlom = 0.0
    if dlom > 0:
        if not dlom_reason.strip():
            blockers.append("采用流动性折扣时，请填写理由。")
        if not dlom_source.strip():
            blockers.append("采用流动性折扣时，请填写依据来源。")
    adjustment = _number(bridge_adjustment)
    if adjustment is None:
        blockers.append("其他股权价值调整必须是数值。")
        adjustment = 0.0
    if not math.isclose(adjustment, 0.0, abs_tol=1e-12):
        if not bridge_reason.strip():
            blockers.append("其他股权价值调整非零时，请填写理由。")
        if not bridge_source.strip():
            blockers.append("其他股权价值调整非零时，请填写来源。")

    target_metric = "ebitda" if method == "EV/EBITDA" else "book_equity"
    target_value = _number(target.get(target_metric))
    if target_value is None or target_value <= 0:
        label = "最新年度 EBITDA" if method == "EV/EBITDA" else "目标公司账面权益"
        blockers.append(f"目标公司{label}缺失或非正数。")
    target_net_debt = _number(target.get("net_debt"))
    if method == "EV/EBITDA" and target_net_debt is None:
        blockers.append("EV/EBITDA 桥接缺少目标公司净债务。")
    if method == "P/B" and not str(target.get("book_equity_basis") or "").strip():
        blockers.append("P/B 法请说明目标公司账面权益的合并/归母口径。")

    peer_analysis: list[dict[str, Any]] = []
    weights: list[float] = []
    for index, peer in enumerate(selected, start=1):
        company = _text(peer.get("company"))
        missing = []
        if not company:
            missing.append("公司名称")
            company = f"第 {index} 家可比公司"
        period = _text(peer.get("period"))
        price_basis = _text(peer.get("price_basis"))
        source = _text(peer.get("source"))
        selection_reason = _text(peer.get("selection_reason"))
        adjustment_reason = _text(peer.get("adjustment_reason"))
        if not period:
            missing.append("报告期")
        if not price_basis:
            missing.append("取价依据")
        if not source:
            missing.append("数据来源")
        if not selection_reason:
            missing.append("入选理由")
        if not adjustment_reason:
            missing.append("修正理由")
        market_cap = _number(peer.get("market_cap"))
        factor = _number(peer.get("adjustment_factor"))
        weight_pct = _number(peer.get("weight_pct"))
        if market_cap is None or market_cap <= 0:
            missing.append("正数股权市值")
        if factor is None or not 0.1 <= factor <= 3.0:
            missing.append("0.10–3.00 修正系数")
        if weight_pct is None or not 0 < weight_pct <= 100:
            missing.append("0–100 之间的正权重")
        if method == "EV/EBITDA":
            ebitda = _number(peer.get("ebitda"))
            net_debt = _number(peer.get("net_debt"))
            other_ev_adjustment = _number(peer.get("other_ev_adjustment"))
            ev_adjustment_reason = _text(peer.get("ev_adjustment_reason"))
            if ebitda is None or ebitda <= 0:
                missing.append("正数 EBITDA")
            if net_debt is None:
                missing.append("净债务（净现金填负数）")
            if other_ev_adjustment is None:
                missing.append("其他 EV 调整（无则填 0）")
                other_ev_adjustment = 0.0
            elif not math.isclose(other_ev_adjustment, 0.0, abs_tol=1e-12) and not ev_adjustment_reason:
                missing.append("其他 EV 调整说明")
            raw = ((market_cap + net_debt + other_ev_adjustment) / ebitda) if ebitda and ebitda > 0 and market_cap is not None and market_cap > 0 and net_debt is not None else None
            if raw is not None and raw <= 0:
                missing.append("正数 EV/EBITDA")
        else:
            book_equity = _number(peer.get("book_equity"))
            basis = _text(peer.get("book_equity_basis"))
            if book_equity is None or book_equity <= 0:
                missing.append("正数账面权益")
            if not basis:
                missing.append("账面权益口径")
            raw = (market_cap / book_equity) if book_equity and book_equity > 0 and market_cap is not None and market_cap > 0 else None
        adjusted = raw * factor if raw is not None and factor is not None else None
        if missing:
            blockers.append(f"{company}：补齐或核对" + "、".join(dict.fromkeys(missing)) + "。")
        if weight_pct is not None and weight_pct > 0:
            weights.append(weight_pct)
        peer_analysis.append({
            "company": company,
            "period": period,
            "price_basis": price_basis,
            "market_cap": market_cap,
            "net_debt": _number(peer.get("net_debt")),
            "other_ev_adjustment": _number(peer.get("other_ev_adjustment")),
            "ev_adjustment_reason": _text(peer.get("ev_adjustment_reason")),
            "ebitda": _number(peer.get("ebitda")),
            "book_equity": _number(peer.get("book_equity")),
            "book_equity_basis": _text(peer.get("book_equity_basis")),
            "source": source,
            "source_location": _text(peer.get("source_location")),
            "selection_reason": selection_reason,
            "adjustment_factor": factor,
            "weight_pct": weight_pct,
            "adjustment_reason": adjustment_reason,
            "raw_multiple": raw,
            "adjusted_multiple": adjusted,
        })

    weight_total = sum(weights)
    if selected and not math.isclose(weight_total, 100.0, rel_tol=0, abs_tol=0.05):
        blockers.append(f"纳入公司的权重合计须为 100%；当前为 {weight_total:.2f}%。")
    valid_peers = [row for row in peer_analysis if row["adjusted_multiple"] is not None]
    weighted_multiple = None
    if len(valid_peers) == len(selected) and math.isclose(weight_total, 100.0, rel_tol=0, abs_tol=0.05):
        weighted_multiple = sum(row["adjusted_multiple"] * row["weight_pct"] / 100 for row in valid_peers)

    output: dict[str, Any] = {
        "method": method,
        "status": "待补充" if blockers else "待人工复核",
        "blockers": list(dict.fromkeys(blockers)),
        "confirmations": dict(confirmations),
        "reviewer": reviewer.strip(),
        "peer_analysis": peer_analysis,
        "weighted_multiple": weighted_multiple,
        "dlom_pct": dlom,
        "dlom_reason": dlom_reason.strip(),
        "dlom_source": dlom_source.strip(),
        "bridge_adjustment": adjustment,
        "bridge_reason": bridge_reason.strip(),
        "bridge_source": bridge_source.strip(),
        "target_year": target.get("year"),
        "target_value": target_value,
        "target_net_debt": target_net_debt,
        "target_book_equity_basis": _text(target.get("book_equity_basis")),
        "target_source": _text(target.get("source")),
        "target_provenance": dict(target.get("provenance", {})),
    }
    if blockers or weighted_multiple is None or not valid_peers:
        return output

    def equity_at(multiple: float) -> float:
        if method == "EV/EBITDA":
            pre_discount = target_value * multiple - target_net_debt
        else:
            pre_discount = target_value * multiple
        return pre_discount * (1 - dlom / 100) + adjustment

    multiples = [row["adjusted_multiple"] for row in valid_peers]
    output["status"] = "已计算，待人工复核"
    output["result"] = {
        "weighted_multiple": weighted_multiple,
        "low_multiple": min(multiples),
        "high_multiple": max(multiples),
        "equity_value": equity_at(weighted_multiple),
        "low_equity_value": equity_at(min(multiples)),
        "high_equity_value": equity_at(max(multiples)),
        "range_note": "低/高值仅为所选可比公司的修正倍数观察范围，不是统计置信区间。",
    }
    return output


def _number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        number = float(str(value).replace(",", "").replace("，", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()
