from __future__ import annotations

import hmac
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from equity_agent.agent import run_agent
from equity_agent.demo import demo_records
from equity_agent.financials import FinancialRecord, parse_financial_file, template_bytes
from equity_agent.valuation import ValuationAssumptions
from equity_agent.workpaper import create_workpaper_excel


st.set_page_config(page_title="股权投资估值预审智能体", page_icon="📊", layout="wide")


def _require_access() -> None:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return
    if st.session_state.get("authenticated"):
        return
    st.markdown("<div class='hero'><div class='eyebrow'>PRIVATE ACCESS</div><h1>股权投资估值预审智能体</h1><p>请输入项目访问口令。</p></div>", unsafe_allow_html=True)
    provided = st.text_input("访问口令", type="password")
    if st.button("进入", type="primary"):
        if hmac.compare_digest(provided, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("口令不正确。")
    st.stop()


def _money(value: float | None) -> str:
    return "—" if value is None else f"¥ {value:,.2f} 亿元"


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _records_table(records: list[FinancialRecord]) -> pd.DataFrame:
    fields = [
        ("revenue", "营业收入"),
        ("ebitda", "EBITDA"),
        ("net_income", "净利润"),
        ("operating_cash_flow", "经营现金流"),
        ("capex", "资本开支"),
        ("net_debt", "净债务"),
    ]
    data = []
    for record in records:
        row = {"年度": record.year}
        row.update({label: record.metrics.get(field) for field, label in fields})
        row["来源"] = record.source
        row["定位"] = "; ".join(f"{field}:{address}" for field, address in record.provenance.items())
        data.append(row)
    return pd.DataFrame(data)


def _default_fcf_margin(records: list[FinancialRecord]) -> float:
    if records:
        latest = records[-1]
        revenue = latest.metrics.get("revenue")
        ocf = latest.metrics.get("operating_cash_flow")
        capex = latest.metrics.get("capex")
        if revenue and revenue > 0 and ocf is not None and capex is not None:
            return max(-0.5, min(0.9, (ocf - abs(capex)) / revenue))
    return 0.08


def _display_result(company: str, records: list[FinancialRecord], result: dict) -> None:
    st.markdown("## 预审结果")
    status = result.get("status", "待复核")
    if status == "初步分析完成，待人工复核":
        st.success(status)
    elif status == "无法估值":
        st.error(status)
    else:
        st.warning(status)
    st.caption(result.get("scope_note", "仅供估值预审，不构成正式评估报告或投资建议。"))

    valuations = result.get("valuations", {})
    dcf = valuations.get("dcf", {})
    comps = valuations.get("multiples", {})
    dcf_base = dcf.get("base", {}).get("equity_value")
    comp_mid = None
    if comps:
        comp_mid = (comps["low"]["equity_value"] + comps["high"]["equity_value"]) / 2

    cards = st.columns(4)
    cards[0].metric("DCF 基准股权价值", _money(dcf_base))
    cards[1].metric("EV/EBITDA 低端", _money(comps.get("low", {}).get("equity_value") if comps else None))
    cards[2].metric("EV/EBITDA 高端", _money(comps.get("high", {}).get("equity_value") if comps else None))
    cards[3].metric("采用 FCF 率", _percent(valuations.get("fcf_margin_used")))

    if dcf:
        chart_data = {
            "DCF Bear": dcf.get("bear", {}).get("equity_value"),
            "DCF Base": dcf.get("base", {}).get("equity_value"),
            "DCF Bull": dcf.get("bull", {}).get("equity_value"),
        }
        if comps:
            chart_data["倍数低端"] = comps["low"]["equity_value"]
            chart_data["倍数高端"] = comps["high"]["equity_value"]
        st.bar_chart(pd.DataFrame({"股权价值（亿元）": chart_data}))
        st.caption("不同估值方法并列呈现，不做未披露权重的机械加权；所有假设须由估值人员复核。")

    left, right = st.columns([1.15, 0.85])
    with left:
        st.markdown("### 财务数据与来源")
        st.dataframe(_records_table(records), width="stretch", hide_index=True)
    with right:
        st.markdown("### 估值假设")
        assumption = result.get("assumptions")
        if assumption:
            st.dataframe(pd.DataFrame([
                ["收入增长率", f"{assumption.revenue_growth:.1%}"],
                ["预测 FCF 率", f"{assumption.fcf_margin:.1%}"],
                ["WACC", f"{assumption.wacc:.1%}"],
                ["永续增长率", f"{assumption.terminal_growth:.1%}"],
                ["预测年数", f"{assumption.forecast_years} 年"],
                ["EV/EBITDA 倍数", f"{assumption.multiple_low:.1f}x – {assumption.multiple_high:.1f}x"],
                ["最新净债务", _money(valuations.get("net_debt_used"))],
                ["FCF 历史观测率", _percent(valuations.get("observed_fcf_margin"))],
            ], columns=["项目", "本次取值"]), width="stretch", hide_index=True)

    st.markdown("### 异常与待办")
    warnings = result.get("warnings", [])
    if warnings:
        st.dataframe(pd.DataFrame(warnings), width="stretch", hide_index=True)
    else:
        st.info("暂未触发内置预警；这不代表数据或估值已经验证。")
    for action in result.get("next_actions", []):
        st.markdown(f"- {action}")

    documents = result.get("documents", [])
    if documents:
        st.markdown("### PDF 证据")
        for index, item in enumerate(documents):
            with st.expander(f"{item.get('name')} · {item.get('method')}"):
                if item.get("warning"):
                    st.warning(item["warning"])
                if item.get("text"):
                    st.text_area("识别文本（仅供核对，不自动覆盖财务表）", item["text"], height=260, key=f"pdf-{index}-{item.get('name')}")

    with st.expander("单智能体执行轨迹"):
        for step in result.get("agent_steps", []):
            st.markdown(f"- {step}")

    output = create_workpaper_excel(company, records, result)
    st.download_button(
        "下载估值预审工作底稿（Excel）",
        data=output,
        file_name=f"估值预审工作底稿_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


_require_access()

st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">EQUITY REVIEW · SINGLE AGENT</div>
      <h1>股权投资估值预审智能体</h1>
      <p>把财务数据、估值假设、异常线索和来源证据放进一份可复核的初稿。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    .stApp { background: #f4f7fa; }
    .block-container { max-width: 1240px; padding-top: 2rem; }
    .hero { padding: 1.6rem 1.8rem; border-radius: 18px; background: linear-gradient(120deg,#102c43,#1f5263); color: #fff; margin-bottom: 1rem; }
    .hero h1 { color: #fff; font-size: 2.05rem; margin: .2rem 0 .5rem; }
    .hero p { color: #d7e7ed; font-size: 1.03rem; margin: 0; }
    .eyebrow { color: #8fe0d4; font-size: .75rem; letter-spacing: .14em; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.write("**单智能体 · 规则化工具调用 · 估值公式可审计 · 人工保留最终判断**")
st.info(
    "隐私边界：结构化数据在当前服务中处理；本应用代码不把上传文件写入持久存储。扫描 PDF 只有在配置 PaddleOCR API Token 后才会发送到 PaddleOCR 官方托管服务。"
)

with st.sidebar:
    st.markdown("### 估值假设（可编辑）")
    st.caption("以下均为演示起点，不代表市场数据或适用于任何企业的标准参数。")
    uploaded_financials = st.file_uploader("财务表（一个企业一份）", type=["xlsx", "xlsm", "csv"])
    use_demo = st.toggle("未上传财务表时使用合成演示数据", value=True)

records: list[FinancialRecord] = []
parse_error = None
if uploaded_financials:
    try:
        records = parse_financial_file(uploaded_financials.name, uploaded_financials.getvalue())
    except Exception as exc:
        parse_error = str(exc)
elif use_demo:
    records = demo_records()

with st.sidebar:
    fcf_default = _default_fcf_margin(records)
    revenue_growth_pct = st.number_input("预测收入增长率（%）", min_value=-50.0, max_value=150.0, value=15.0, step=1.0)
    fcf_margin_pct = st.number_input("预测 FCF 率（%）", min_value=-50.0, max_value=90.0, value=round(fcf_default * 100, 1), step=1.0, help="默认取最新年度（经营现金流 - 资本开支）/收入；缺失时用 8% 演示值。请核对并调整。")
    wacc_pct = st.number_input("WACC（%）", min_value=1.0, max_value=50.0, value=12.0, step=0.5)
    terminal_pct = st.number_input("永续增长率（%）", min_value=0.0, max_value=15.0, value=3.0, step=0.5)
    years = st.slider("预测年数", min_value=3, max_value=10, value=5)
    multiple_low = st.number_input("EV/EBITDA 倍数下限（x）", min_value=0.1, max_value=100.0, value=6.0, step=0.5)
    multiple_high = st.number_input("EV/EBITDA 倍数上限（x）", min_value=0.1, max_value=100.0, value=10.0, step=0.5)
    paddle_model = os.getenv("PADDLEOCR_MODEL", "PaddleOCR-VL-1.6")
    st.caption(f"PDF 扫描件 OCR：{'已配置 API' if os.getenv('PADDLEOCR_ACCESS_TOKEN') else '未配置 Token'}")
    st.caption(f"模型设置：{paddle_model}")

st.markdown("### 1 · 导入材料")
company_name_input = st.text_input("企业名称", value="", placeholder="例如：某某科技有限公司；演示时可留空")
company_name = company_name_input.strip() or ("示例科技有限公司" if not uploaded_financials and use_demo else "")
pdf_uploads = st.file_uploader("PDF 报表/审计材料（可选）", type=["pdf"], accept_multiple_files=True)
has_paddle_token = bool(os.getenv("PADDLEOCR_ACCESS_TOKEN"))
paddle_api_consent = st.checkbox(
    "我授权本次将无文本层的扫描 PDF 发送到 PaddleOCR 官方托管 API（可能消耗配额或产生费用）",
    value=False,
    disabled=not (pdf_uploads and has_paddle_token),
    help="仅当已配置 API Token 且勾选后，扫描件才会发往远端。可读文本 PDF 始终在本地提取。",
)
if pdf_uploads and not has_paddle_token:
    st.caption("未配置 PaddleOCR API Token；PDF 文本层仍可本地读取，扫描件不会发送。")
st.download_button(
    "下载财务数据模板（含合成样例）",
    data=template_bytes(),
    file_name="equity_valuation_financial_template.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

if parse_error:
    st.error(f"财务表解析失败：{parse_error}")
elif records:
    if not uploaded_financials:
        st.caption("当前使用合成演示数据，不对应任何真实企业。")
    st.markdown("### 数据预览（金额单位：人民币亿元）")
    st.dataframe(_records_table(records), width="stretch", hide_index=True)
else:
    st.warning("请上传一份财务表，或开启合成演示案例。")

if records and not company_name:
    st.caption("请填写企业名称后开始预审。")
run_disabled = not records or not company_name or bool(parse_error)
if st.button("开始估值预审", type="primary", disabled=run_disabled, width="stretch"):
    assumptions = ValuationAssumptions(
        revenue_growth=revenue_growth_pct / 100,
        fcf_margin=fcf_margin_pct / 100,
        wacc=wacc_pct / 100,
        terminal_growth=terminal_pct / 100,
        forecast_years=years,
        multiple_low=multiple_low,
        multiple_high=multiple_high,
    )
    files = [(item.name, item.getvalue()) for item in (pdf_uploads or [])]
    with st.spinner("智能体正在解析证据、运行数据检查并计算估值敏感性…"):
        analysis = run_agent(
            records,
            assumptions,
            files,
            paddle_token=os.getenv("PADDLEOCR_ACCESS_TOKEN") if paddle_api_consent else None,
            paddle_model=paddle_model,
        )
    st.session_state["review_result"] = analysis
    st.session_state["review_records"] = records
    st.session_state["review_company"] = company_name.strip()

if "review_result" in st.session_state:
    st.divider()
    st.caption("以下为本会话上一次运行的结果；如果刚调整了数据或假设，请重新点击“开始估值预审”。")
    _display_result(
        st.session_state["review_company"],
        st.session_state["review_records"],
        st.session_state["review_result"],
    )

st.divider()
st.caption("仅供估值预审和工作底稿起草；结果依赖输入数据与使用者假设，必须由专业人员复核。不得作为正式资产评估报告、投资承诺或唯一投资决策依据。")
st.markdown("[PaddleOCR 官方 API SDK 文档](https://www.paddleocr.ai/main/version3.x/inference_deployment/serving/paddleocr_official_api/python.html) · 扫描件 OCR 使用官方托管服务；本项目不在服务器本地加载 PaddleOCR 大模型。")
