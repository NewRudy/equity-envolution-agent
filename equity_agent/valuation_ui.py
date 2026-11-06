from __future__ import annotations

from html import escape

import streamlit as st

from .guidance import METHOD_GUIDE
from .intake_ui import financial_upload
from .presentation import money, number, table
from .project import (
    AGGREGATIONS, BOOK_BASES, EARNINGS_BASES, LISTED, METHODS, PRICE_POLICIES, VALUE_DRIVERS, Project, digest, dump_project,
)
from .screening import clean, iso_date
from .ui_common import XLSX, badge, counter_cards, go, key, nav_actions, page_intro, plain
from .workflow import (
    CHECKS, available_records, blank_peer, compute, dcf_blockers, draft, inspect, method_indicators, target_input, workbench_details,
)
from .workpaper import create_workpaper_excel


def _bounded(value, low: float, high: float = float("inf")) -> float | None:
    converted = number(value)
    return converted if converted is not None and low <= converted <= high else None


def _save_policy(project: Project, changes: dict, action: str) -> None:
    changed = [field for field in changes if project.policy[field] != changes[field]]
    if changed:
        project.policy.update(changes)
        project.invalidate()
        policy = project.policy
        project.note(action, f"{policy['method']} · {policy['target_year'] or '最近年度'} · {AGGREGATIONS[policy['aggregation']]} · "
                     f"{PRICE_POLICIES[policy['price_policy']]}", actor=project.reviewer,
                     evidence=changes.get("method_reason") or changes.get("aggregation_reason", ""))


def _method(project: Project) -> None:
    with st.container(border=True, key=key(project, "wb_panel_method")):
        st.subheader("比较价值比率与年度")
        policy = project.policy
        years = sorted(({r.year for r in available_records(project)} | {policy["target_year"]}) - {""}, reverse=True)
        year_options = ["", *years]
        left, right = st.columns(2)
        year = left.selectbox("比较财报年度", year_options, index=year_options.index(policy["target_year"]),
                              format_func=lambda value: f"{value} 年报" if value else "最近可用完整年度",
                              key=key(project, "year_preview"))
        drivers = list(VALUE_DRIVERS)
        driver = right.selectbox("主要价值驱动", drivers, index=drivers.index(policy["value_driver"]),
                                 format_func=VALUE_DRIVERS.get, key=key(project, "driver_preview"))
        assessments = method_indicators(project, year, driver)
        parts = []
        for method, option in assessments.items():
            aligned = len(option["aligned"])
            label = "优先核对" if option["recommended"] else "待补标的或口径" if not option["target_ready"] else "待核样本" if aligned < 3 else "备选"
            status = badge(label, "ok" if option["recommended"] else "neutral")
            coverage = f"{aligned} 家采纳样本已对齐基础数据" if option["target_ready"] else "先核对标的分母、年度和口径"
            parts.append(f'<div><strong>{method}</strong>{status}<p>{coverage}</p>'
                         f'<p>{escape(METHOD_GUIDE[method])}</p></div>')
        st.html('<div class="wb-methods">' + "".join(parts) + "</div>")
        st.caption("优先建议结合价值驱动和数据对齐情况。改变年度、驱动或比率后可先看方案，再保存本次口径。")
        if driver and not assessments[driver]["recommended"]:
            st.caption(f"当前价值驱动对应 {driver}。请先核对标的口径和至少 3 家对齐的样本。")
        st.caption(f"已保存 {policy['method']} · {policy['target_year'] + ' 年报' if policy['target_year'] else '最近可用年度'}")
        selected = st.radio("本次比率", METHODS, index=METHODS.index(policy["method"]),
                            horizontal=True, key=key(project, "valuation_method"))
        with st.form(key(project, f"method_{selected}_{project.revision}"), border=False):
            updates = {"method": selected, "target_year": year, "value_driver": driver}
            if selected in {"P/B", "P/E"}:
                field, label, options = (
                    ("book_equity_basis", "标的账面权益口径", BOOK_BASES) if selected == "P/B" else
                    ("earnings_basis", "标的净利润口径", EARNINGS_BASES)
                )
                current = policy[field]
                updates[field] = st.selectbox(label, options, index=options.index(current) if current in options else None) or ""
            updates["method_reason"] = st.text_area(
                "比率选择与总体可比性说明", value=policy["method_reason"] if selected == policy["method"] else "",
                max_chars=1300, height=90,
                placeholder="说明为什么采用这个比率，以及共同的业务特征与主要差异",
            ).strip()
            if st.form_submit_button("保存方法与口径"):
                _save_policy(project, updates, "保存估值方法与口径")
                st.rerun()


def _sample_selection(project: Project, advice: dict) -> None:
    adopted = project.adopted()
    if not adopted:
        return
    method = project.policy["method"]
    saved = [code for code in project.sample_sets.get(method, adopted) if code in adopted]
    proposed = advice["aligned"]
    with st.container(border=True, key=key(project, "wb_panel_sample")):
        st.subheader(f"{method} 使用哪些样本")
        st.caption(f"业务采纳 {len(adopted)} 家 · 本法已选 {len(saved)} 家 · 基础数据对齐 {len(proposed)} 家")
        with st.expander("查看已采纳公司的数据缺项"):
            table(["公司", "当前本法", "财报、取价与比率口径"], [
                [peer["company"], "选用" if code in saved else "备选",
                 "、".join(advice["gaps"].get(code, [])) or ("已对齐" if advice["target_ready"] else "待标的口径")]
                for code, peer in adopted.items()
            ])
        picker_key = key(project, f"sample_keys_{method}_{project.revision}")
        if 3 <= len(proposed) <= 8:
            if st.button("把数据对齐的样本填入选择", key=key(project, "sample_suggestion")):
                st.session_state[picker_key] = list(proposed)
        else:
            st.caption("本表测算使用 3–8 家。请结合业务证据、数据缺项和样本代表性选取。")
        with st.form(key(project, f"sample_form_{method}"), border=False):
            if picker_key not in st.session_state:
                st.session_state[picker_key] = saved
            chosen = st.multiselect("本法样本", list(adopted), format_func=lambda code: adopted[code]["company"], key=picker_key)
            reason = st.text_input("样本选择说明", value=project.sample_reasons.get(method, ""), max_chars=800,
                                   placeholder="说明为什么保留或不使用部分已采纳公司")
            if st.form_submit_button("保存本法样本"):
                if chosen and set(chosen) != set(proposed) and not reason.strip():
                    st.error("自选样本与当前数据对齐名单有差异，请记录理由。")
                else:
                    project.save_samples(method, chosen, reason)
                    st.rerun()


def _calculation_policy(project: Project, inspection: dict) -> None:
    policy = project.policy
    with st.expander("选择取价规则与倍数汇总", expanded=True):
        with st.form(key(project, f"aggregation_{project.revision}"), border=False):
            cols = st.columns(2)
            modes = list(PRICE_POLICIES)
            price = cols[0].selectbox("实际取价日规则", modes, index=modes.index(policy["price_policy"]),
                                      format_func=PRICE_POLICIES.get)
            days = cols[0].number_input("取价窗口（自然日）", min_value=1, max_value=90, value=policy["price_lookback"],
                                        help="仅用于「限基准日前若干日」。提前取价需在每家公司的依据中说明。")
            modes = list(AGGREGATIONS)
            aggregation = cols[1].selectbox("样本汇总方式", modes, index=modes.index(policy["aggregation"]),
                                             format_func=AGGREGATIONS.get)
            reason = cols[1].text_input("汇总选择依据", value=policy["aggregation_reason"], max_chars=800,
                                        placeholder="例如：样本有偏离，选择中位数")
            if st.form_submit_button("保存取价与汇总"):
                if aggregation == "weighted" and policy["aggregation"] != "weighted" and not reason.strip():
                    st.error("使用自定义权重时，请说明可回查的分配依据。")
                else:
                    _save_policy(project, {
                        "price_policy": price, "price_lookback": days, "aggregation": aggregation,
                        "aggregation_reason": reason.strip(),
                    }, "保存取价与汇总")
                    st.rerun()
        st.caption("基准日有适用行情时可选当天取价。无适用行情时可设窗口，并保留实际取价日与依据。")
        st.caption("可从等权均值起步，用中位数观察极端值影响。自定义加权需要解释权重差异。")
        options = inspection.get("multiple_options", {})
        if options:
            table(["汇总方式", "当前样本观察倍数"], [
                [label, f"{options[mode]:.3f}×" if mode in options else "待补权重"]
                for mode, label in AGGREGATIONS.items()
            ])


def _peer_editor(project: Project, codes: list[str]) -> None:
    roster = project.roster()
    method = project.policy["method"]
    chosen = st.selectbox("编辑已采纳公司", codes, key=key(project, "valuation_peer"),
                          format_func=lambda code: roster[code].candidate.name)
    row = project.peers.get(chosen, blank_peer()) | project.peer_parameters(chosen, method)
    candidate = roster[chosen].candidate
    updated = dict(row)
    with st.form(key(project, f"peer_values_{digest(chosen)}_{project.revision}"), border=False):
        st.caption(f"金额均为人民币亿元。修正与自定义权重保存到 {method}。")
        a, b, c = st.columns(3)
        pricing_text = a.text_input("实际取价日", value=clean(row.get("pricing_date"), 10), placeholder="YYYY-MM-DD", max_chars=10)
        period_text = b.text_input("财报截止日", value=clean(row.get("report_end"), 10), placeholder="YYYY-MM-DD", max_chars=10)
        pricing, period = iso_date(pricing_text), iso_date(period_text)
        updated["market_cap"] = c.number_input("股权市值（亿元）", value=_bounded(row.get("market_cap"), 0), min_value=0.0, format="%.4f")
        updated["pricing_date"] = pricing.isoformat() if pricing else ""
        updated["report_end"] = period.isoformat() if period else ""
        updated["period"] = f"{period.year} 年报" if period else ""
        updated["price_basis"] = st.text_input("取价方式与依据", value=clean(row.get("price_basis")), max_chars=800,
                                               placeholder="如：基准日收盘市值；若提前取价请注明原因")
        updated["source"] = st.text_input("市场及财务来源", value=clean(row.get("source")), max_chars=1000,
                                           placeholder="注明行情数据位置及年报页码")
        financial_columns = st.columns(3 if method == "EV/EBITDA" else 2)
        a, b = financial_columns[:2]
        if method == "EV/EBITDA":
            updated["ebitda"] = a.number_input("EBITDA（亿元）", value=number(row.get("ebitda")), format="%.4f")
            updated["net_debt"] = b.number_input("净债务（亿元）", value=number(row.get("net_debt")), format="%.4f",
                                                 help="有息债务减现金。净现金填负数。")
            updated["other_ev_adjustment"] = financial_columns[2].number_input(
                "其他 EV 调整（无则填 0）", value=number(row.get("other_ev_adjustment")), format="%.4f",
            )
            updated["ev_adjustment_reason"] = st.text_input(
                "EV 调整说明（非零必填）", value=clean(row.get("ev_adjustment_reason")), max_chars=800,
            )
        else:
            field, basis, label, options = (
                ("book_equity", "book_equity_basis", "账面权益", BOOK_BASES) if method == "P/B" else
                ("net_income", "earnings_basis", "净利润", EARNINGS_BASES)
            )
            updated[field] = a.number_input(f"{label}（亿元）", value=number(row.get(field)), format="%.4f")
            current = clean(row.get(basis))
            available = (*options, current) if current and current not in options else options
            updated[basis] = b.selectbox(f"可比公司{label}口径", available,
                                         index=available.index(current) if current else None) or ""
        a, b = st.columns(2)
        updated["adjustment_factor"] = a.number_input(
            "倍数修正系数", min_value=0.1, max_value=3.0, value=_bounded(row.get("adjustment_factor", 1), 0.1, 3.0), step=0.05,
            help="1.00 表示不修正。",
        )
        if project.policy["aggregation"] == "weighted":
            updated["weight_pct"] = b.number_input(
                "样本权重（%）", min_value=0.0, max_value=100.0, value=_bounded(row.get("weight_pct"), 0, 100), format="%.2f",
            )
        updated["adjustment_reason"] = st.text_input(
            "修正或不修正的理由", value=clean(row.get("adjustment_reason")), max_chars=800,
        )
        if st.form_submit_button("保存该公司取价与参数", width="stretch"):
            if (pricing_text.strip() and not pricing) or (period_text.strip() and not period):
                st.error("实际取价日和财报截止日请填写有效日期，格式为 YYYY-MM-DD。")
                return
            updated.update(company=candidate.name, ticker=candidate.code, source_location=row.get("source_location") or "工作台手工填写")
            project.store_peer_values(chosen, method, updated)
            project.invalidate()
            project.note("保存可比取价与参数", candidate.name, code=candidate.code, actor=project.reviewer, evidence=updated["source"])
            st.rerun()


def _peer_inputs(project: Project) -> None:
    codes = list(project.adopted())
    if not codes:
        st.info("采纳公司会自动进入这里。请先在三态复核中记录至少 3 家采纳样本。")
        return
    with st.container(border=True, key=key(project, "wb_panel_values")):
        st.subheader(f"补齐已采纳样本 · {len(codes)} 家")
        peers = project.selected(project.policy["method"])
        weight = sum(number(p.get("weight_pct")) or 0 for p in peers)
        date_count = sum(bool(iso_date(p.get("pricing_date")) and iso_date(p.get("report_end"))) for p in peers)
        weighting = project.policy["aggregation"] == "weighted"
        st.caption(f"本法已填取价及财报日期 {date_count}/{len(peers)} 家" + (f" · 自定义权重合计 {weight:.2f}%" if weighting else ""))
        with st.expander("查看已采纳样本与数据"):
            table(
                ["公司", "财报截止日", "取价日", "市值（亿元）", "已录自定义权重", "入选理由"],
                [[p["company"], p.get("report_end") or "待补", p.get("pricing_date") or "待补",
                  money(p.get("market_cap")), f"{number(p.get('weight_pct')) or 0:g}%", p["selection_reason"]] for p in peers],
            )
        chosen = [k for k in project.sample_sets.get(project.policy["method"], codes) if k in codes]
        if weighting and st.button("按等权填写本法权重", disabled=not 3 <= len(chosen) <= 8):
            base = round(100 / len(chosen), 2)
            method = project.policy["method"]
            for i, code in enumerate(chosen):
                row = project.peers.get(code, blank_peer()) | project.peer_parameters(code, method)
                row["weight_pct"] = round(100 - base * i, 2) if i == len(chosen) - 1 else base
                project.store_peer_values(code, method, row)
            project.invalidate()
            project.note("分配等权重", f"{project.policy['method']} · {len(chosen)} 家本法样本", actor=project.reviewer)
            st.rerun()
        with st.expander("逐家补取价、口径与修正", expanded=date_count != len(peers)):
            _peer_editor(project, codes)


def _advanced(project: Project) -> None:
    policy = project.policy
    with st.expander("选填：折扣、股权调整与 DCF"):
        st.caption("样本倍数修正、流动性折扣和股权调整分开记录。请核对是否重复反映同一项差异。")
        include = st.checkbox("增加独立 DCF 参照", value=policy["include_dcf"], key=key(project, "include_dcf"))
        if include != policy["include_dcf"]:
            _save_policy(project, {"include_dcf": include}, "调整现金流参照")
        with st.form(key(project, f"advanced_{project.revision}"), border=False):
            left, right = st.columns(2)
            changes = {}
            changes["dlom_pct"] = left.number_input("流动性折扣（%）", min_value=0.0, max_value=80.0, value=policy["dlom_pct"])
            changes["bridge_adjustment"] = right.number_input("其他股权调整（亿元）", value=policy["bridge_adjustment"], format="%.4f")
            changes["dlom_reason"] = left.text_input("折扣理由", value=policy["dlom_reason"])
            changes["bridge_reason"] = right.text_input("股权调整理由", value=policy["bridge_reason"])
            changes["dlom_source"] = left.text_input("折扣来源", value=policy["dlom_source"])
            changes["bridge_source"] = right.text_input("股权调整来源", value=policy["bridge_source"])
            if include:
                st.caption("DCF 是独立的情景参照。预测假设由使用者填写，不与市场法加权。")
                for column, field, label, low, high in (
                    (left, "growth_pct", "收入增长率（%）", -50.0, 150.0),
                    (right, "fcf_pct", "自由现金流率（%）", -50.0, 90.0),
                    (left, "wacc_pct", "折现率（%）", 1.0, 50.0),
                    (right, "terminal_pct", "永续增长率（%）", 0.0, 15.0),
                ):
                    changes[field] = column.number_input(label, min_value=low, max_value=high, value=policy[field])
                changes["years"] = st.number_input("预测年数", min_value=3, max_value=10, value=policy["years"])
            if st.form_submit_button("保存测算参数"):
                _save_policy(project, changes, "保存测算参数")
                st.rerun()


def valuation_page(project: Project) -> None:
    page_intro("valuation", "在同一基准日和年度口径下核对样本；条件齐备后生成股权价值草稿。", project)
    if project.mode == "archive" or project.profile.route != LISTED:
        st.info("当前项目可导出筛选与证据底稿。实际上市公司比较法测算需用真实项目及基准日数据。")
        nav_actions(("返回三态复核", "review"), ("查看工作底稿", "paper"))
        return
    if not project.ready_profile:
        st.info("请先在标的与口径中保存业务画像和基准日。")
        nav_actions(("返回标的与口径", "profile"), ("查看工作底稿", "paper"))
        return
    financial_upload(project)
    _method(project)
    inspection = inspect(project)
    _calculation_policy(project, inspection)
    _sample_selection(project, method_indicators(project)[project.policy["method"]])
    _peer_inputs(project)
    _advanced(project)
    blockers = inspection["blockers"] + dcf_blockers(project)
    with st.container(border=True, key=key(project, "wb_panel_check")):
        st.subheader("计算前检查")
        st.caption("测算使用已保存的数据、方法和参数。")
        target = target_input(project)
        if target["report_end"]:
            st.caption(f"采用标的财报截止日 {target['report_end']} · 估值基准日 {project.profile.benchmark}")
        if blockers:
            st.warning(f"还有 {len(blockers)} 项条件待满足。当前可以导出不含估值结果的底稿。")
            for item in blockers:
                plain(item, "wb-blocker")
        else:
            st.success("数据检查通过。请确认样本、数据和假设。")
        if project.mode == "demo":
            st.caption("合成情境的确认使用演示预设。")
            submitted = st.button("生成合成估值草稿", type="primary", disabled=bool(blockers), width="stretch")
            checks, reviewer = dict.fromkeys(CHECKS, True), "演示预设"
        else:
            with st.form(key(project, f"confirm_{project.revision}"), border=False):
                reviewer = st.text_input("本次测算复核人", value=project.reviewer, max_chars=80)
                checks = {name: st.checkbox(label) for name, label in CHECKS.items()}
                submitted = st.form_submit_button("生成估值草稿", type="primary", disabled=bool(blockers), width="stretch")
        if submitted:
            result = compute(project, checks, reviewer)
            if result["market_method"].get("result"):
                go("paper")
            else:
                for issue in result["market_method"]["blockers"]:
                    plain(issue, "wb-blocker")
    nav_actions(("返回三态复核", "review"), ("前往底稿", "paper"))


def _result(result: dict) -> None:
    market = result["market_method"]
    metrics = market["result"]
    aggregation = market.get("aggregation_label", "自定义加权")
    multiple = metrics.get("selected_multiple", metrics["weighted_multiple"])
    low, high, value = (metrics[k] for k in ("low_equity_value", "high_equity_value", "equity_value"))
    position = 50 if high == low else 100 * (value - low) / (high - low)
    st.html(
        '<div class="wb-result"><div class="wb-result-main"><span class="wb-kicker">全部股东权益测算参考</span>'
        f'<strong>{money(value)}<small>亿元</small></strong>'
        f'<span>{escape(market["method"])} · {escape(aggregation)} {multiple:.3f}×</span></div>'
        '<div class="wb-result-range"><span>所选样本观察区间</span>'
        f'<div class="wb-meter" style="--marker:{position:.2f}%"><i></i></div>'
        f'<div><b>{money(low)}</b><b>{money(high)} 亿元</b></div>'
        '<small>基于所选样本最小与最大修正倍数。</small></div></div>'
    )
    with st.expander("计算路径与样本明细", expanded=True):
        selected = market["peer_analysis"]
        table(["公司", "原始倍数", "修正后倍数", "实际计算权重", "取价日"], [
            [row["company"], f"{row['raw_multiple']:.3f}×", f"{row['adjusted_multiple']:.3f}×",
             f"{weight:.2f}%" if (weight := row.get("effective_weight_pct")) is not None else "中位数",
             row["pricing_date"]] for row in selected
        ])
        method = market["method"]
        basis = {"EV/EBITDA": "EBITDA", "P/B": "账面权益", "P/E": "归母净利润"}[method]
        detail = f"目标{basis} {money(market['target_value'])} × 选用倍数 {multiple:.4f}"
        if method == "EV/EBITDA":
            detail += f" − 目标净债务 {money(market['target_net_debt'])}"
        plain(f"（{detail}） ×（1 − {market['dlom_pct']:g}%）＋ 其他股权调整 {money(market['bridge_adjustment'])} 亿元")
        plain(f"选用依据：{market['method_reason']}")
    dcf = result.get("valuations", {}).get("dcf")
    if dcf:
        with st.expander("现金流参照（DCF）"):
            table(["情景", "股权价值（亿元）"], [
                [name, money(dcf[k]["equity_value"])] for k, name in (("bear", "偏低"), ("base", "基准"), ("bull", "偏高"))
            ])
            st.caption("独立情景参照，不与市场法加权。")
    if result.get("warnings"):
        with st.expander("财务数据提示"):
            for item in result["warnings"]:
                plain(item["message"], "wb-blocker")


def paper_page(project: Project) -> None:
    page_intro("paper", "带走候选来源、人工判断和取价记录。也可以保存项目草稿，回来继续。", project)
    details = workbench_details(project)
    result = draft(project)
    if project.result:
        _result(result)
    else:
        counter_cards([
            ("候选记录", len(details["rows"]), "含待核验及剔除"),
            ("有效采纳", len(project.selected()), "按当前复核判断"),
            ("留痕记录", len(project.audit), "可随 Excel 导出"),
        ])
        if project.mode == "archive":
            plain("当前为历史回放。底稿保留筛选结果、业务证据及你记录的复核决定。", "wb-archive-line")
        else:
            st.info("尚无本次估值结果。可先下载含已有资料和待处理项的底稿。")
    with st.expander("查看方案选择与依据"):
        table(["决策", "本次选择", "说明或来源"], [list(row) for row in details["choices"]])
    filename = "合成示例" if project.mode == "demo" else "历史回放" if project.mode == "archive" else "项目"
    with st.container(border=True, key=key(project, "wb_panel_download")):
        st.subheader("下载本次工作")
        left, right = st.columns(2)
        left.download_button(
            "下载 Excel 工作底稿",
            create_workpaper_excel(project.profile.name or "未命名项目", project.records, result, details),
            file_name=f"{filename}_股权估值底稿.xlsx", mime=XLSX, type="primary", width="stretch", on_click="ignore",
        )
        right.download_button(
            "保存可恢复的项目草稿", dump_project(project),
            file_name=f"{filename}_工作台草稿.json", mime="application/json", width="stretch", on_click="ignore",
        )
        st.caption("项目草稿可在工作台总览恢复。再次计算需重新确认输入；重跑筛选时需重新选择候选资料。")
    with st.expander("查看操作记录"):
        if project.audit:
            table(["时间（UTC）", "动作", "填写人", "详情"], [
                [entry["at"].replace("T", " ").removesuffix("+00:00"), entry["action"], entry["actor"], entry["detail"]]
                for entry in reversed(project.audit[-60:])
            ])
        else:
            st.caption("还没有记录。")
    nav_actions(("返回三态复核" if project.mode == "archive" else "返回估值测算",
                 "review" if project.mode == "archive" else "valuation"), ("工作台总览", "home"))
