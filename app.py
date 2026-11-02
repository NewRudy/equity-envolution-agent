from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from equity_agent.agent import run_agent
from equity_agent.demo import demo_records
from equity_agent.financials import FinancialRecord, parse_financial_file, template_bytes
from equity_agent.market_method import (
    comparable_template_bytes,
    empty_comparables_frame,
    parse_comparable_file,
)
from equity_agent.valuation import ValuationAssumptions
from equity_agent.workpaper import create_workpaper_excel


st.set_page_config(page_title="股权估值工作台", page_icon="📊", layout="wide")


def _require_access() -> None:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or st.session_state.get("authenticated"):
        return
    st.title("股权估值工作台")
    provided = st.text_input("访问口令", type="password")
    if st.button("进入", type="primary"):
        if hmac.compare_digest(provided, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("口令不正确。")
    st.stop()


def _money(value: float | None) -> str:
    return "待补充" if value is None else f"¥ {value:,.2f} 亿元"


def _records_table(records: list[FinancialRecord]) -> pd.DataFrame:
    fields = [
        ("revenue", "营业收入"),
        ("ebitda", "EBITDA"),
        ("net_income", "净利润"),
        ("operating_cash_flow", "经营现金流"),
        ("capex", "资本开支"),
        ("net_debt", "净债务"),
        ("book_equity", "账面权益"),
    ]
    rows = []
    for record in records:
        row = {"年度": record.year}
        row.update({label: record.metrics.get(field) for field, label in fields})
        row["来源"] = record.source
        row["字段定位"] = "; ".join(f"{field}:{address}" for field, address in record.provenance.items())
        rows.append(row)
    return pd.DataFrame(rows)


def _display_result(company: str, records: list[FinancialRecord], result: dict) -> None:
    st.markdown("## 本次结果")
    st.caption(f"运行时间：{result.get('run_at', '本会话')} · {result.get('scope_note', '')}")
    valuations = result.get("valuations", {})
    dcf = valuations.get("dcf", {})
    market = result.get("market_method", {})
    market_result = market.get("result") or {}
    cards = st.columns(4)
    cards[0].metric("DCF 基准股权价值", _money(dcf.get("base", {}).get("equity_value")))
    cards[1].metric(f"{market.get('method', '市场法')} 基准值", _money(market_result.get("equity_value")))
    cards[2].metric("加权修正倍数", f"{market_result.get('weighted_multiple', 0):.2f}x" if market_result else "待补充")
    cards[3].metric("纳入可比公司", f"{sum(bool(row.get('selected')) for row in market.get('peer_analysis', []))} 家")

    if market_result:
        st.success("市场法已计算，仍需估值人员复核。")
        st.write(
            f"样本观察范围：{_money(market_result.get('low_equity_value'))} 至 "
            f"{_money(market_result.get('high_equity_value'))}。{market_result.get('range_note', '')}"
        )
        st.caption(
            f"公式：{market.get('method')}；流动性折扣 {market.get('dlom_pct', 0):.2f}%；"
            f"其他股权调整 {_money(market.get('bridge_adjustment', 0))}。"
        )
    else:
        st.warning("市场法尚未出数。先完成以下门槛；DCF 参照不会替代市场法结论。")
        for blocker in market.get("blockers", []):
            st.markdown(f"- {blocker}")

    if dcf:
        st.markdown("### DCF 参照情景")
        dcf_rows = [
            {"情景": label, "股权价值（亿元）": dcf.get(key, {}).get("equity_value")}
            for key, label in (("bear", "偏低"), ("base", "基准"), ("bull", "偏高"))
            if dcf.get(key)
        ]
        st.dataframe(pd.DataFrame(dcf_rows), width="stretch", hide_index=True)
        st.caption("DCF 仅作独立敏感性参照，不与市场法机械加权。")

    with st.expander("可比公司计算明细", expanded=bool(market_result)):
        peer_rows = market.get("peer_analysis", [])
        if peer_rows:
            st.dataframe(pd.DataFrame(peer_rows), width="stretch", hide_index=True)
        else:
            st.info("本次没有提交可比公司。")
    with st.expander("目标公司财务数据与来源"):
        st.dataframe(_records_table(records), width="stretch", hide_index=True)
    warnings = result.get("warnings", [])
    if warnings:
        with st.expander("数据预警与复核待办", expanded=True):
            st.dataframe(pd.DataFrame(warnings), width="stretch", hide_index=True)
            for action in result.get("next_actions", []):
                st.markdown(f"- {action}")

    output = create_workpaper_excel(company, records, result)
    st.download_button(
        "下载本次估值工作底稿（Excel）",
        data=output,
        file_name=f"估值工作底稿_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


_require_access()

st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">EQUITY REVIEW WORKBENCH</div>
      <h1>股权估值工作台</h1>
      <p>导入目标公司数据，人工审阅可比公司，生成有来源、可复核的估值草稿。</p>
    </div>
    <style>
    .stApp { background: #f4f7fa; }
    .block-container { max-width: 1320px; padding-top: 1.6rem; }
    .hero { padding: 1.5rem 1.8rem; border-radius: 16px; background: linear-gradient(120deg,#102c43,#1f5263); color: #fff; margin-bottom: 1rem; }
    .hero h1 { color: #fff; font-size: 2rem; margin: .2rem 0 .45rem; }
    .hero p { color: #d7e7ed; font-size: 1rem; margin: 0; }
    .eyebrow { color: #8fe0d4; font-size: .74rem; letter-spacing: .14em; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)
st.caption("不自动选同行、不抓取行情、不替人下结论。缺少复核或来源时，市场法不会输出目标股权价值。")

with st.sidebar:
    st.markdown("### DCF 参照假设")
    st.caption("仅用于敏感性参照；市场法以你提交并确认的可比数据为准。")
    use_demo = st.toggle("使用合成演示财务数据", value=False)
    revenue_growth_pct = st.number_input("预测收入增长率（%）", min_value=-50.0, max_value=150.0, value=15.0, step=1.0)
    fcf_margin_pct = st.number_input("预测 FCF 率（%）", min_value=-50.0, max_value=90.0, value=8.0, step=1.0)
    wacc_pct = st.number_input("WACC（%）", min_value=1.0, max_value=50.0, value=12.0, step=0.5)
    terminal_pct = st.number_input("永续增长率（%）", min_value=0.0, max_value=15.0, value=3.0, step=0.5)
    years = st.slider("预测年数", min_value=3, max_value=10, value=5)

st.markdown("### 1. 导入目标公司财务数据")
financial_col, company_col = st.columns([1.25, 0.75])
with financial_col:
    uploaded_financials = st.file_uploader("财务表（一个目标公司；金额单位按列名识别）", type=["xlsx", "xlsm", "csv"], key="financial-upload")
    st.download_button(
        "下载财务表模板",
        data=template_bytes(),
        file_name="股权估值_财务数据模板.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with company_col:
    company_name_input = st.text_input("目标公司名称", placeholder="例如：某某科技有限公司")
    company_name = company_name_input.strip() or ("合成演示公司" if use_demo and not uploaded_financials else "")

records: list[FinancialRecord] = []
financial_error = None
if uploaded_financials:
    try:
        records = parse_financial_file(uploaded_financials.name, uploaded_financials.getvalue())
    except Exception as exc:
        financial_error = str(exc)
elif use_demo:
    records = demo_records()
if financial_error:
    st.error(f"财务表解析失败：{financial_error}")
elif records:
    if use_demo and not uploaded_financials:
        st.info("当前是合成演示数据，不对应真实企业。")
    st.dataframe(_records_table(records), width="stretch", hide_index=True)
else:
    st.info("上传财务表后可计算 DCF 参照和市场法；也可在侧边栏打开合成样例查看流程。")

st.markdown("### 2. 导入并审阅可比公司")
method = st.radio("市场法", ["EV/EBITDA", "P/B"], horizontal=True)
peer_upload_col, template_col = st.columns([1.25, 0.75])
with peer_upload_col:
    uploaded_peers = st.file_uploader("可比公司表（CSV / XLSX）", type=["xlsx", "xlsm", "csv"], key="peer-upload")
with template_col:
    st.download_button(
        "下载可比公司模板",
        data=comparable_template_bytes(),
        file_name="股权估值_可比公司模板.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

peer_frame = empty_comparables_frame()
peer_error = None
if uploaded_peers:
    try:
        peer_frame = parse_comparable_file(uploaded_peers.name, uploaded_peers.getvalue())
    except Exception as exc:
        peer_error = str(exc)
if peer_error:
    st.error(f"可比公司表解析失败：{peer_error}")
else:
    st.caption("选择 3–8 家。逐行填写来源、报告期、取价依据、入选理由、修正系数、权重（合计 100%）和修正理由。金额单位均为人民币亿元；EV 法还需明确其他 EV 调整，无则填 0。")
    peer_key = hashlib.sha256(uploaded_peers.getvalue()).hexdigest()[:12] if uploaded_peers else "manual"
    peer_column_config = {
        "selected": st.column_config.CheckboxColumn("纳入", help="仅纳入已人工审阅的公司。"),
        "company": st.column_config.TextColumn("公司", required=True),
        "ticker": st.column_config.TextColumn("代码"),
        "period": st.column_config.TextColumn("报告期", help="如 2025A。"),
        "price_basis": st.column_config.TextColumn("取价依据（日期/方式）", help="如 2026-10-31 收盘价。"),
        "market_cap": st.column_config.NumberColumn("股权市值（亿元）", min_value=0.0, format="%.4f"),
        "net_debt": st.column_config.NumberColumn("净债务（亿元）", format="%.4f", help="净现金填负数。"),
        "other_ev_adjustment": st.column_config.NumberColumn("其他 EV 调整（亿元）", format="%.4f", help="少数股东权益、优先股等融资项目；无则明确填 0。"),
        "ev_adjustment_reason": st.column_config.TextColumn("EV 调整说明"),
        "ebitda": st.column_config.NumberColumn("EBITDA（亿元）", format="%.4f"),
        "book_equity": st.column_config.NumberColumn("账面权益（亿元）", format="%.4f"),
        "book_equity_basis": st.column_config.TextColumn("账面权益口径"),
        "source": st.column_config.TextColumn("数据来源", help="年报/公告/行情等来源，需可回查。"),
        "selection_reason": st.column_config.TextColumn("入选理由"),
        "adjustment_factor": st.column_config.NumberColumn("修正系数", min_value=0.1, max_value=3.0, step=0.05, format="%.2f"),
        "weight_pct": st.column_config.NumberColumn("权重（%）", min_value=0.0, max_value=100.0, step=1.0, format="%.2f"),
        "adjustment_reason": st.column_config.TextColumn("修正理由"),
        "source_location": st.column_config.TextColumn("原文件位置", disabled=True),
    }
    edited_peers = st.data_editor(
        peer_frame,
        key=f"peer-editor-{peer_key}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config=peer_column_config,
    )

latest = sorted(records, key=lambda record: int(record.year))[-1] if records else None
target_book_equity_basis = ""
if method == "P/B":
    target_book_equity_basis = st.text_input("目标公司账面权益口径", placeholder="例如：归属于母公司股东权益，合并报表")

st.markdown("### 3. 确认口径并计算")
review_col, bridge_col = st.columns(2)
with review_col:
    peer_review = st.checkbox("我已复核所选公司的业务可比性、报告期、取价依据、数据来源和入选理由。")
    target_review = st.checkbox("我已核实目标公司本法所用指标的单位、期间与合并口径。")
    reviewer = st.text_input("本次复核人")
with bridge_col:
    bridge_label = "我已确认 EV 桥接包含净债务及少数股东/优先股等重大项目，并核对其他股权调整。" if method == "EV/EBITDA" else "我已确认账面权益口径和其他股权调整。"
    bridge_review = st.checkbox(bridge_label)
    discount_review = st.checkbox("我已确认流动性折扣取值；填 0% 表示本次不采用折扣。")
    dlom_pct = st.number_input("流动性折扣（%）", min_value=0.0, max_value=80.0, value=0.0, step=1.0)

with st.expander("其他股权调整和依据（没有则留零）"):
    bridge_adjustment = st.number_input("其他股权价值调整（亿元）", value=0.0, step=0.1, format="%.4f", help="先应用流动性折扣，再加减此项。")
    bridge_reason = st.text_input("调整理由")
    bridge_source = st.text_input("调整依据来源")
    dlom_reason = st.text_input("流动性折扣理由", disabled=dlom_pct == 0)
    dlom_source = st.text_input("流动性折扣依据来源", disabled=dlom_pct == 0)
    if method == "EV/EBITDA":
        st.caption("公式：可比 EV = 股权市值 + 净债务 + 其他 EV 调整；目标股权价值 = (目标 EBITDA × 加权修正倍数 - 目标净债务) × (1 - 折扣) + 其他调整。")
    else:
        st.caption("公式：可比 P/B = 股权市值 ÷ 账面权益；目标股权价值 = 目标账面权益 × 加权修正倍数 × (1 - 折扣) + 其他调整。")

if records and not company_name:
    st.caption("请填写目标公司名称。")
run_disabled = not records or not company_name or bool(financial_error) or bool(peer_error)
if st.button("生成估值草稿", type="primary", disabled=run_disabled, width="stretch"):
    assumptions = ValuationAssumptions(
        revenue_growth=revenue_growth_pct / 100,
        fcf_margin=fcf_margin_pct / 100,
        wacc=wacc_pct / 100,
        terminal_growth=terminal_pct / 100,
        forecast_years=years,
    )
    duplicate_periods = len({record.year for record in records}) != len(records)
    unit_issues = any("未标单位" in location for record in records for location in record.provenance.values())
    quality_blockers = []
    if duplicate_periods:
        quality_blockers.append("目标公司财务表存在重复年度，须先核实口径或重报。")
    if unit_issues:
        quality_blockers.append("目标公司财务表有未标单位金额，请在列名标注单位后重新上传。")
    target = {
        "year": latest.year if latest else None,
        "ebitda": latest.metrics.get("ebitda") if latest else None,
        "net_debt": latest.metrics.get("net_debt") if latest else None,
        "book_equity": latest.metrics.get("book_equity") if latest else None,
        "book_equity_basis": target_book_equity_basis,
        "source": latest.source if latest else "",
        "provenance": latest.provenance if latest else {},
        "data_quality_blockers": quality_blockers,
    }
    confirmations = {
        "peer_review": peer_review,
        "target_review": target_review,
        "bridge_review": bridge_review,
        "discount_review": discount_review,
    }
    market_request = {
        "method": method,
        "peer_rows": edited_peers.to_dict(orient="records"),
        "target": target,
        "confirmations": confirmations,
        "reviewer": reviewer,
        "dlom_pct": dlom_pct,
        "dlom_reason": dlom_reason,
        "dlom_source": dlom_source,
        "bridge_adjustment": bridge_adjustment,
        "bridge_reason": bridge_reason,
        "bridge_source": bridge_source,
    }
    with st.spinner("正在检查输入并生成可复核底稿…"):
        analysis = run_agent(records, assumptions, market_request)
    analysis["run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    analysis["warnings"].extend({"severity": "高", "message": item} for item in quality_blockers)
    st.session_state["review_result"] = analysis
    st.session_state["review_records"] = records
    st.session_state["review_company"] = company_name

if "review_result" in st.session_state:
    st.divider()
    st.caption("结果对应上一次点击“生成估值草稿”时的输入。修改财务表、可比公司或假设后，请重新生成。")
    _display_result(
        st.session_state["review_company"],
        st.session_state["review_records"],
        st.session_state["review_result"],
    )

st.divider()
st.caption("该工具只生成分析草稿；所有目标数据、可比选择、估值口径和结果均须由专业人员复核。不得作为正式评估报告或唯一投资决策依据。")
