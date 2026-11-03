from __future__ import annotations

import hashlib
import hmac
import math
import os
from copy import deepcopy
from datetime import datetime
from secrets import token_hex
from zoneinfo import ZoneInfo

import streamlit as st

from equity_agent.agent import run_agent
from equity_agent.demo import demo_comparables, demo_records
from equity_agent.financials import parse_financial_file, template_bytes
from equity_agent.market_method import calculate_market_method, comparable_template_bytes, parse_comparable_file
from equity_agent.presentation import (
    install_styles, money, number, scroll_to_top, show_financials, show_peer_summary, show_result, stats, stepper, text,
)
from equity_agent.valuation import ValuationAssumptions


st.set_page_config(page_title="股权估值工作台", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")
install_styles()


def _require_access() -> None:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or st.session_state.get("authenticated"):
        return
    _, center, _ = st.columns([0.5, 1.5, 0.5])
    with center:
        st.markdown("## 股权估值工作台")
        st.write("输入访问口令，进入估值工作台。")
        with st.form("access-form"):
            provided = st.text_input("访问口令", type="password", placeholder="请输入工作台访问口令")
            submitted = st.form_submit_button("进入工作台", type="primary", width="stretch")
        if submitted:
            if hmac.compare_digest(provided, expected):
                st.session_state["authenticated"] = True
                st.rerun()
            st.error("口令不正确，请重新输入。")
    st.stop()


def _initialize() -> None:
    defaults = {
        "ui_started": False, "ui_step": 1, "ui_mode": "real", "ui_project_id": token_hex(4),
        "ui_company": "", "ui_records": [], "ui_peers": [], "ui_peer_revision": 0,
        "ui_active_peer": 0, "ui_result": None, "ui_financial_signature": "",
        "ui_peer_signature": "", "ui_financial_name": "", "ui_financial_error": "",
        "ui_peer_error": "", "ui_scroll_top": False,
        "ui_policy": {
            "reviewer": "", "book_equity_basis": "", "peer_review": False, "target_review": False,
            "bridge_review": False, "discount_review": False, "dlom_pct": 0.0, "dlom_reason": "",
            "dlom_source": "", "bridge_adjustment": 0.0, "bridge_reason": "", "bridge_source": "",
            "include_dcf": False, "growth_pct": 15.0, "fcf_pct": 8.0, "wacc_pct": 12.0,
            "terminal_pct": 3.0, "years": 5,
        },
        "ui_method": "EV/EBITDA",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = deepcopy(value)


def _new_project() -> None:
    for key in list(st.session_state):
        if key.startswith(("ui_", "field_")):
            del st.session_state[key]
    _initialize()
    st.session_state["ui_started"] = True
    st.session_state["ui_scroll_top"] = True


def _go(step: int) -> None:
    st.session_state["ui_step"] = step
    st.session_state["ui_scroll_top"] = True
    st.rerun()


def _clear_confirmations(fields: tuple[str, ...]) -> None:
    for field in fields:
        st.session_state["ui_policy"][field] = False
        key = f"field_policy_{st.session_state['ui_project_id']}_{field}"
        if key in st.session_state:
            st.session_state[key] = False


def _invalidate_result(reset_confirmations: bool = False) -> None:
    st.session_state["ui_result"] = None
    if reset_confirmations:
        _clear_confirmations(("peer_review", "target_review", "bridge_review", "discount_review"))


def _stamp() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M（洛杉矶时间）")


def _target() -> dict:
    records = st.session_state["ui_records"]
    latest = sorted(records, key=lambda record: int(record.year))[-1] if records else None
    blockers = []
    if len({record.year for record in records}) != len(records):
        blockers.append("目标公司有重复年度，请核实口径后重新上传。")
    if any("未标单位" in location for record in records for location in record.provenance.values()):
        blockers.append("财务表中有未标单位的金额，请在列名写明亿元、万元或元后重新上传。")
    return {
        "year": latest.year if latest else None,
        "ebitda": latest.metrics.get("ebitda") if latest else None,
        "net_debt": latest.metrics.get("net_debt") if latest else None,
        "book_equity": latest.metrics.get("book_equity") if latest else None,
        "book_equity_basis": st.session_state["ui_policy"]["book_equity_basis"],
        "source": latest.source if latest else "",
        "provenance": latest.provenance if latest else {},
        "data_quality_blockers": blockers,
    }


def _market_request() -> dict:
    policy = st.session_state["ui_policy"]
    return {
        "method": st.session_state["ui_method"],
        "peer_rows": st.session_state["ui_peers"],
        "target": _target(),
        "confirmations": {key: policy[key] for key in ("peer_review", "target_review", "bridge_review", "discount_review")},
        "reviewer": policy["reviewer"],
        **{key: policy[key] for key in ("dlom_pct", "dlom_reason", "dlom_source", "bridge_adjustment", "bridge_reason", "bridge_source")},
    }


def _assumptions() -> ValuationAssumptions:
    policy = st.session_state["ui_policy"]
    return ValuationAssumptions(
        revenue_growth=policy["growth_pct"] / 100, fcf_margin=policy["fcf_pct"] / 100,
        wacc=policy["wacc_pct"] / 100, terminal_growth=policy["terminal_pct"] / 100,
        forecast_years=policy["years"],
    )


def _load_demo() -> None:
    _new_project()
    st.session_state["ui_mode"] = "demo"
    st.session_state["ui_step"] = 4
    st.session_state["ui_company"] = "示例科技有限公司"
    st.session_state["ui_records"] = demo_records()
    st.session_state["ui_peers"] = demo_comparables()
    request = _market_request()
    request["reviewer"] = "合成示例预设（非人工复核）"
    request["confirmations"] = {key: True for key in request["confirmations"]}
    analysis = run_agent(st.session_state["ui_records"], _assumptions(), request, include_dcf=False)
    analysis.update({
        "is_demo": True, "run_at": _stamp(), "status": "合成示例（复核条件为预设）",
        "scope_note": "所有数据与复核条件均为合成演示预设，不代表真实企业、市场数据或人工复核。",
    })
    analysis["market_method"]["status"] = "合成示例，非人工复核"
    st.session_state["ui_result"] = analysis


def _set_policy(field: str, widget_key: str) -> None:
    st.session_state["ui_policy"][field] = st.session_state[widget_key]
    _invalidate_result()
    if field == "book_equity_basis":
        _clear_confirmations(("target_review", "bridge_review"))
    elif field in {"dlom_pct", "dlom_reason", "dlom_source"}:
        _clear_confirmations(("discount_review",))
    elif field in {"bridge_adjustment", "bridge_reason", "bridge_source"}:
        _clear_confirmations(("bridge_review", "discount_review"))


def _policy_text(field: str, label: str, **kwargs) -> None:
    key = f"field_policy_{st.session_state['ui_project_id']}_{field}"
    st.text_input(label, value=text(st.session_state["ui_policy"][field]), key=key,
                  on_change=_set_policy, args=(field, key), **kwargs)


def _policy_number(field: str, label: str, **kwargs) -> None:
    key = f"field_policy_{st.session_state['ui_project_id']}_{field}"
    st.number_input(label, value=st.session_state["ui_policy"][field], key=key,
                    on_change=_set_policy, args=(field, key), **kwargs)


def _policy_check(field: str, label: str, **kwargs) -> None:
    key = f"field_policy_{st.session_state['ui_project_id']}_{field}"
    st.checkbox(label, value=bool(st.session_state["ui_policy"][field]), key=key,
                on_change=_set_policy, args=(field, key), **kwargs)


def _peer_changed(index: int, field: str, widget_key: str) -> None:
    st.session_state["ui_peers"][index][field] = st.session_state[widget_key]
    _invalidate_result(reset_confirmations=True)


def _peer_widget_key(index: int, field: str) -> str:
    return f"field_peer_{st.session_state['ui_project_id']}_{st.session_state['ui_peer_revision']}_{index}_{field}"


def _peer_text(index: int, field: str, label: str, area: bool = False, **kwargs) -> None:
    key = _peer_widget_key(index, field)
    widget = st.text_area if area else st.text_input
    widget(label, value=text(st.session_state["ui_peers"][index].get(field)), key=key,
           on_change=_peer_changed, args=(index, field, key), **kwargs)


def _peer_number(index: int, field: str, label: str, **kwargs) -> None:
    key = _peer_widget_key(index, field)
    st.number_input(label, value=number(st.session_state["ui_peers"][index].get(field)), key=key,
                    format="%.4f", on_change=_peer_changed, args=(index, field, key), **kwargs)


def _selected() -> list[dict]:
    return [peer for peer in st.session_state["ui_peers"] if peer.get("selected")]


def _add_peer() -> None:
    st.session_state["ui_peers"].append({
        "selected": False, "company": "", "ticker": "", "period": "", "price_basis": "",
        "market_cap": None, "net_debt": None, "other_ev_adjustment": 0.0, "ev_adjustment_reason": "",
        "ebitda": None, "book_equity": None, "book_equity_basis": "", "source": "",
        "selection_reason": "", "adjustment_factor": 1.0, "weight_pct": 0.0,
        "adjustment_reason": "", "source_location": "页面手工录入",
    })
    st.session_state["ui_active_peer"] = len(st.session_state["ui_peers"]) - 1
    st.session_state["ui_peer_revision"] += 1
    _invalidate_result(reset_confirmations=True)
    st.rerun()


def _equal_weights() -> None:
    selected = _selected()
    if not selected:
        return
    weight = round(100 / len(selected), 2)
    for peer in selected:
        peer["weight_pct"] = weight
    selected[-1]["weight_pct"] = round(100 - weight * (len(selected) - 1), 2)
    st.session_state["ui_peer_revision"] += 1
    _invalidate_result(reset_confirmations=True)
    st.rerun()


def _home() -> None:
    st.markdown(
        '<div class="hero"><span class="kicker">从数据到估值底稿</span>'
        '<h1>把一家公司的估值，做清楚。</h1>'
        '<p>导入财务数据，核对几家可比公司，得到一份带计算过程和数据来源的估值草稿。</p></div>',
        unsafe_allow_html=True,
    )
    first, second = st.columns(2)
    first.button("先看完整示例", type="primary", width="stretch", on_click=_load_demo)
    second.button("开始我的估值", width="stretch", on_click=_new_project)
    if st.session_state["ui_mode"] == "real" and st.session_state["ui_records"]:
        if st.button("继续当前项目"):
            st.session_state["ui_started"] = True
            st.session_state["ui_scroll_top"] = True
            st.rerun()
    st.markdown(
        '<div class="how-grid">'
        '<div class="how-card"><div class="num">1</div><strong>准备公司数据</strong><span>下载模板，填好财务表并上传。</span></div>'
        '<div class="how-card"><div class="num">2</div><strong>核对可比公司</strong><span>选择 3–8 家同业，确认数据来源和权重。</span></div>'
        '<div class="how-card"><div class="num">3</div><strong>得到分析结果</strong><span>查看估值与计算过程，下载 Excel 底稿。</span></div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="note">第一次使用，建议先点「先看完整示例」。真实项目需要目标公司的财务表，以及人工收集的可比公司资料。</div>', unsafe_allow_html=True)


def _company_step() -> None:
    st.header("先准备目标公司")
    st.markdown('<div class="step-intro">这一步只需要公司名称和一份财务表。下一步再选择可比公司。</div>', unsafe_allow_html=True)
    company = st.text_input("你要估值的公司叫什么？", value=st.session_state["ui_company"],
                            key=f"field_company_{st.session_state['ui_project_id']}", placeholder="例如：某某科技有限公司")
    if company.strip() != st.session_state["ui_company"]:
        _invalidate_result(reset_confirmations=True)
    st.session_state["ui_company"] = company.strip()
    with st.container(border=True):
        st.subheader("上传财务表")
        st.write("使用下方模板填写。金额列请写明单位，例如「营业收入（亿元）」。")
        st.download_button("1. 下载空白财务模板", template_bytes(), file_name="财务数据模板.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        uploaded = st.file_uploader("2. 上传填好的财务表", type=["xlsx", "xlsm", "csv"],
                                    key=f"field_financial_upload_{st.session_state['ui_project_id']}")
        if uploaded:
            signature = hashlib.sha256(uploaded.getvalue()).hexdigest()
            if signature != st.session_state["ui_financial_signature"]:
                try:
                    records = parse_financial_file(uploaded.name, uploaded.getvalue())
                    st.session_state["ui_records"] = records
                    st.session_state["ui_financial_name"] = uploaded.name
                    st.session_state["ui_financial_error"] = ""
                except Exception as exc:
                    st.session_state["ui_records"] = []
                    st.session_state["ui_financial_error"] = str(exc)
                st.session_state["ui_financial_signature"] = signature
                _invalidate_result(reset_confirmations=True)
    records = st.session_state["ui_records"]
    if st.session_state["ui_financial_error"]:
        st.error("这份表还无法读取：" + st.session_state["ui_financial_error"])
    if records:
        latest = sorted(records, key=lambda record: int(record.year))[-1]
        st.success(f"已读取「{st.session_state['ui_financial_name']}」，共 {len(records)} 个年度。")
        stats([
            ("最新报告年度", latest.year),
            ("最新 EBITDA", f"{money(latest.metrics.get('ebitda'))} 亿元"),
            ("最新账面权益", f"{money(latest.metrics.get('book_equity'))} 亿元"),
        ])
        with st.expander("查看已导入的数据"):
            show_financials(records, include_sources=False)
        for blocker in _target()["data_quality_blockers"]:
            st.error(blocker)
    else:
        st.caption("EV/EBITDA 法需有 EBITDA 与净债务；P/B 法需有账面权益。选做 DCF 时还需收入、现金流和假设。")
    can_continue = bool(records and company.strip() and not _target()["data_quality_blockers"])
    if not can_continue:
        st.caption("填写公司名称并上传带明确单位的财务表后，即可继续。")
    cols = st.columns(2)
    if cols[0].button("返回首页", width="stretch"):
        st.session_state["ui_started"] = False
        st.session_state["ui_scroll_top"] = True
        st.rerun()
    if cols[1].button("下一步：选择可比公司", type="primary", disabled=not can_continue, width="stretch"):
        _go(2)


def _peer_editor() -> None:
    peers = st.session_state["ui_peers"]
    revision = st.session_state["ui_peer_revision"]
    index = st.selectbox(
        "逐家公司核对", list(range(len(peers))), index=min(st.session_state["ui_active_peer"], len(peers) - 1),
        format_func=lambda i: f"{i + 1}. {text(peers[i].get('company')) or '待命名公司'}",
        key=f"field_active_peer_{st.session_state['ui_project_id']}_{revision}",
    )
    st.session_state["ui_active_peer"] = index
    peer = peers[index]
    with st.container(border=True):
        st.subheader("核对这家公司")
        st.caption("修改会自动记录在本次会话中。选中「纳入本次估值」后，它才会参与计算。")
        cols = st.columns([1.5, 1])
        with cols[0]:
            _peer_text(index, "company", "公司名称", placeholder="填写可比公司全称")
        with cols[1]:
            _peer_text(index, "ticker", "股票代码（选填）", placeholder="例如：000001")
        selected_key = _peer_widget_key(index, "selected")
        cols = st.columns(2)
        with cols[0]:
            st.checkbox("纳入本次估值", value=bool(peer.get("selected")), key=selected_key,
                        on_change=_peer_changed, args=(index, "selected", selected_key))
        with cols[1]:
            _peer_number(index, "weight_pct", "本公司权重（%）", min_value=0.0, max_value=100.0, step=1.0)

        st.markdown("**财务与市场数据 · 金额均为亿元**")
        method = st.session_state["ui_method"]
        cols = st.columns(3 if method == "EV/EBITDA" else 2)
        with cols[0]:
            _peer_number(index, "market_cap", "股权市值（亿元）", min_value=0.0, placeholder="填写总市值")
        if method == "EV/EBITDA":
            with cols[1]:
                _peer_number(index, "net_debt", "净债务（亿元）", help="有息债务减现金；净现金填负数。", placeholder="有息债务减现金")
            with cols[2]:
                _peer_number(index, "ebitda", "EBITDA（亿元）", min_value=0.0, help="息税折旧摊销前利润。", placeholder="填写 EBITDA")
            with st.expander("其他融资项目（没有则填 0）"):
                _peer_number(index, "other_ev_adjustment", "其他 EV 调整（亿元）",
                             help="例如少数股东权益、优先股等；用于市值到企业价值的桥接。")
                _peer_text(index, "ev_adjustment_reason", "融资调整说明（非零时必填）")
        else:
            with cols[1]:
                _peer_number(index, "book_equity", "账面权益（亿元）", min_value=0.0, placeholder="填写账面净资产")
            _peer_text(index, "book_equity_basis", "账面权益口径", placeholder="例如：合并报表归母权益")

        cols = st.columns(2)
        with cols[0]:
            _peer_text(index, "period", "财务报告期", placeholder="例如：2025 年年报")
        with cols[1]:
            _peer_text(index, "price_basis", "取价日期与方式", placeholder="例如：2026-10-30 收盘价")
        _peer_text(index, "source", "这些数字从哪里来？", area=True,
                   placeholder="例如：2025 年报第 35 页；交易所公告或行情链接", height=100)
        _peer_text(index, "selection_reason", "为什么选这家公司？", area=True,
                   placeholder="说明主营业务、市场和规模与目标公司的相似处", height=90)
        with st.expander("倍数修正", expanded=not bool(text(peer.get("adjustment_reason")))):
            st.write("修正系数填 1 表示本次不修正；请保留你的判断理由。")
            _peer_number(index, "adjustment_factor", "倍数修正系数", min_value=0.1, max_value=3.0, step=0.05)
            _peer_text(index, "adjustment_reason", "修正或不修正的理由", area=True,
                       placeholder="例如：已比较规模和盈利能力，本次不修正", height=90)
        if text(peer.get("source_location")):
            st.caption("原文件位置：" + text(peer.get("source_location")))


def _peers_step() -> None:
    st.header("选择用来比较的公司")
    st.markdown('<div class="step-intro">选择 3–8 家可比公司。你可以上传完整表格，也可以逐家填写；每家公司都要保留数据来源。</div>', unsafe_allow_html=True)
    method = st.radio(
        "选择本次估值方法", ["EV/EBITDA", "P/B"],
        index=0 if st.session_state["ui_method"] == "EV/EBITDA" else 1,
        format_func=lambda value: "按经营盈利比较（EV/EBITDA）" if value == "EV/EBITDA" else "按账面净资产比较（P/B）",
        key=f"field_method_{st.session_state['ui_project_id']}",
    )
    if st.session_state["ui_method"] != method:
        _invalidate_result(reset_confirmations=True)
    st.session_state["ui_method"] = method
    st.caption("EV/EBITDA 比较企业盈利对应的价值；P/B 比较每一元账面权益对应的市场价值。方法适用性由估值人员判断。")
    with st.expander("批量导入可比公司表", expanded=not bool(st.session_state["ui_peers"])):
        st.download_button("下载可比公司模板", comparable_template_bytes(), file_name="可比公司模板.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        uploaded = st.file_uploader("上传填好的可比公司表", type=["xlsx", "xlsm", "csv"],
                                    key=f"field_peer_upload_{st.session_state['ui_project_id']}")
        if uploaded:
            signature = hashlib.sha256(uploaded.getvalue()).hexdigest()
            if signature != st.session_state["ui_peer_signature"]:
                try:
                    frame = parse_comparable_file(uploaded.name, uploaded.getvalue())
                    st.session_state["ui_peers"] = frame.to_dict(orient="records")
                    st.session_state["ui_peer_error"] = ""
                    st.session_state["ui_active_peer"] = 0
                    st.session_state["ui_peer_revision"] += 1
                    _invalidate_result(reset_confirmations=True)
                except Exception as exc:
                    st.session_state["ui_peer_error"] = str(exc)
                st.session_state["ui_peer_signature"] = signature
        if st.session_state["ui_peer_error"]:
            st.error("这份表还无法读取：" + st.session_state["ui_peer_error"])
    if st.button("＋ 直接添加一家公司"):
        _add_peer()
    peers = st.session_state["ui_peers"]
    if not peers:
        st.markdown('<div class="empty-state">还没有可比公司。上传一份表格，或点击上方「直接添加一家公司」开始填写。</div>', unsafe_allow_html=True)
    else:
        selected = _selected()
        total = sum(number(peer.get("weight_pct")) or 0 for peer in selected)
        stats([("已纳入", f"{len(selected)} 家 / 需要 3–8 家"), ("当前权重合计", f"{total:.2f}% / 需要 100%"), ("本次方法", method)])
        if st.button("所选公司采用等权重", disabled=not 3 <= len(selected) <= 8,
                     help="只分配已纳入公司的权重，不替你选择可比公司。"):
            _equal_weights()
        with st.expander(f"查看全部公司与权重（{len(peers)} 家）"):
            show_peer_summary(peers)
        _peer_editor()
    selected = _selected()
    total = sum(number(peer.get("weight_pct")) or 0 for peer in selected)
    ready = 3 <= len(selected) <= 8 and math.isclose(total, 100.0, abs_tol=0.05)
    if not ready:
        st.info(f"当前纳入 {len(selected)} 家，权重合计 {total:.2f}%。需要纳入 3–8 家、权重合计 100% 后继续。")
    cols = st.columns(2)
    if cols[0].button("上一步：公司数据", width="stretch"):
        _go(1)
    if cols[1].button("下一步：确认并计算", type="primary", disabled=not ready or bool(st.session_state["ui_peer_error"]), width="stretch"):
        _go(3)


def _confirm_step() -> None:
    st.header("确认本次估值")
    st.markdown('<div class="step-intro">先核对以下信息，再填写复核人并确认口径。完成后即可看到估值结果和下载底稿。</div>', unsafe_allow_html=True)
    selected = _selected()
    target = _target()
    method = st.session_state["ui_method"]
    policy = st.session_state["ui_policy"]
    stats([
        ("目标公司", st.session_state["ui_company"]),
        ("本次方法", method),
        ("纳入可比公司", f"{len(selected)} 家"),
    ])
    with st.expander("查看本次选用的公司和权重"):
        show_peer_summary(selected)
    if method == "P/B":
        _policy_text("book_equity_basis", "目标公司账面权益口径", placeholder="例如：合并报表归属于母公司的股东权益")
    _policy_text("reviewer", "本次由谁复核？", placeholder="填写复核人姓名")
    st.subheader("请逐项确认")
    _policy_check("target_review", "目标公司的数据已核对", help="已核对单位、报告期、合并范围，以及本方法所用 EBITDA/净债务或账面权益。")
    _policy_check("peer_review", "可比公司的业务、期间和取价依据已核对", help="已核对入选理由、来源、报告期和市场取价方式，且修正系数与权重符合本次判断。")
    _policy_check("bridge_review", "净债务、权益与其他调整口径已核对", help="EV 法包括现金、债务、少数股东/优先股等重大融资项目；P/B 法需匹配账面权益口径。")
    _policy_check("discount_review", "本次流动性折扣与股权调整已确认", help="下方默认不折扣、无额外调整；如有调整请填写依据。")

    with st.expander("需要折扣或其他股权调整时，再展开填写"):
        st.write("没有这类调整时保持 0 即可。非零取值必须补充理由和来源。")
        _policy_number("dlom_pct", "流动性折扣（%）", min_value=0.0, max_value=80.0, step=1.0)
        if policy["dlom_pct"] > 0:
            _policy_text("dlom_reason", "采用折扣的理由")
            _policy_text("dlom_source", "折扣依据来源")
        _policy_number("bridge_adjustment", "其他股权价值调整（亿元）", step=0.1,
                       help="先应用流动性折扣，再加减此金额。增加价值填正数，减少价值填负数。")
        if not math.isclose(policy["bridge_adjustment"], 0.0):
            _policy_text("bridge_reason", "股权调整理由")
            _policy_text("bridge_source", "股权调整来源")

    with st.expander("选做：增加现金流参照（DCF）"):
        _policy_check("include_dcf", "同时计算 DCF 参照")
        if policy["include_dcf"]:
            st.write("这些预测假设由你填写和确认。DCF 单独展示，不与市场法加权。")
            cols = st.columns(2)
            for column, field, label, low, high, step in (
                (cols[0], "growth_pct", "预测收入增长率（%）", -50.0, 150.0, 1.0),
                (cols[1], "fcf_pct", "预测自由现金流率（%）", -50.0, 90.0, 1.0),
                (cols[0], "wacc_pct", "折现率 WACC（%）", 1.0, 50.0, 0.5),
                (cols[1], "terminal_pct", "永续增长率（%）", 0.0, 15.0, 0.5),
            ):
                with column:
                    _policy_number(field, label, min_value=low, max_value=high, step=step)
            _policy_number("years", "预测年数", min_value=3, max_value=10, step=1)
    request = _market_request()
    inspection = deepcopy(request)
    inspection["confirmations"] = {key: True for key in inspection["confirmations"]}
    inspection["reviewer"] = "输入预检查"
    data_blockers = calculate_market_method(**inspection)["blockers"]
    dcf_blockers = []
    if policy["include_dcf"]:
        if policy["wacc_pct"] <= policy["terminal_pct"]:
            dcf_blockers.append("DCF 的折现率必须大于永续增长率。")
        latest = st.session_state["ui_records"][-1]
        if latest.metrics.get("net_debt") is None:
            dcf_blockers.append("DCF 还需要目标公司最新年度净债务，请补齐财务表。")
        if not latest.metrics.get("revenue") or latest.metrics["revenue"] <= 0:
            dcf_blockers.append("DCF 还需要正数营业收入，请补齐财务表。")
    if data_blockers or dcf_blockers:
        st.error(f"还有 {len(data_blockers) + len(dcf_blockers)} 项输入需要补齐：")
        for blocker in data_blockers + dcf_blockers:
            st.write(f"• {blocker}")
    actual = calculate_market_method(**request)
    ready = not actual["blockers"] and not dcf_blockers
    if ready:
        st.success("本次输入和确认已齐备，可以生成估值结果。")
    elif not data_blockers and not dcf_blockers:
        st.info("数据已齐备。填写复核人，并完成上方 4 项确认后即可计算。")
    cols = st.columns(2)
    if cols[0].button("上一步：可比公司", width="stretch"):
        _go(2)
    if cols[1].button("生成估值结果", type="primary", disabled=not ready, width="stretch"):
        with st.spinner("正在计算估值并整理底稿…"):
            result = run_agent(st.session_state["ui_records"], _assumptions(), request, include_dcf=policy["include_dcf"])
        result.update({"is_demo": False, "run_at": _stamp()})
        if not result.get("market_method", {}).get("result"):
            st.error("输入发生变化，请核对上方缺项后重新计算。")
        else:
            st.session_state["ui_result"] = result
            _go(4)


_require_access()
_initialize()
st.markdown('<div class="brand"><span class="brand-mark">估</span>股权估值工作台<small>公司数据 · 可比分析 · 估值底稿</small></div>', unsafe_allow_html=True)
if not st.session_state["ui_started"]:
    _home()
elif st.session_state["ui_step"] == 4 and st.session_state["ui_result"]:
    if st.session_state["ui_mode"] != "demo":
        stepper(4)
    show_result(st.session_state["ui_company"], st.session_state["ui_records"], st.session_state["ui_result"])
    cols = st.columns(2)
    if st.session_state["ui_mode"] == "demo":
        cols[0].button("开始我的真实项目", type="primary", on_click=_new_project, width="stretch")
        if cols[1].button("返回首页", width="stretch"):
            st.session_state["ui_started"] = False
            st.session_state["ui_scroll_top"] = True
            st.rerun()
    else:
        if cols[0].button("返回修改输入", width="stretch"):
            _go(3)
        cols[1].button("新建一个项目", on_click=_new_project, width="stretch")
else:
    stepper(st.session_state["ui_step"])
    if st.session_state["ui_step"] == 1:
        _company_step()
    elif st.session_state["ui_step"] == 2:
        _peers_step()
    else:
        _confirm_step()
st.markdown('<div class="footer-note">用于估值分析草稿。数据与口径由使用者确认，结果须由专业人员复核。</div>', unsafe_allow_html=True)
if st.session_state.pop("ui_scroll_top", False):
    scroll_to_top()
