from __future__ import annotations

from html import escape

import streamlit as st

from .intake_ui import financial_upload
from .presentation import money, number, table
from .project import BOOK_BASES, EARNINGS_BASES, LISTED, METHODS, Project, digest, dump_project
from .screening import clean, iso_date
from .ui_common import XLSX, badge, counter_cards, go, key, nav_actions, page_intro, plain
from .workflow import (
    CHECKS, blank_peer, compute, dcf_blockers, draft, inspect, method_indicators, target_input, workbench_details,
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
        project.note(action, f"{project.policy['method']} · 已更新 {len(changed)} 项设置", actor=project.reviewer)


def _method(project: Project) -> None:
    with st.container(border=True, key=key(project, "wb_panel_method")):
        st.subheader("选用价值比率")
        parts = []
        for method, explanation, available in method_indicators(project):
            status = badge("有标的分母" if available else "待补标的数据", "ok" if available else "neutral")
            parts.append(f'<div><strong>{method}</strong>{status}<p>{escape(explanation)}</p></div>')
        st.html('<div class="wb-methods">' + "".join(parts) + "</div>")
        selected = st.radio("本次比率", METHODS, index=METHODS.index(project.policy["method"]),
                            horizontal=True, key=key(project, "valuation_method"))
        if selected != project.policy["method"]:
            _save_policy(project, {"method": selected, "method_reason": ""}, "切换估值比率")
        with st.form(key(project, f"method_{selected}_{project.revision}"), border=False):
            updates = {}
            if selected in {"P/B", "P/E"}:
                field, label, options = (
                    ("book_equity_basis", "标的账面权益口径", BOOK_BASES) if selected == "P/B" else
                    ("earnings_basis", "标的净利润口径", EARNINGS_BASES)
                )
                current = project.policy[field]
                updates[field] = st.selectbox(label, options, index=options.index(current) if current in options else None) or ""
            updates["method_reason"] = st.text_area(
                "比率选择与总体可比性说明", value=project.policy["method_reason"], max_chars=1300, height=90,
                placeholder="说明为什么采用这个比率，以及共同的业务特征与主要差异",
            ).strip()
            if st.form_submit_button("保存方法与口径"):
                _save_policy(project, updates, "保存估值方法与口径")
                st.rerun()


def _adopted(project: Project) -> list[str]:
    return [
        k for k, row in project.roster().items()
        if (decision := project.decision(row)) and decision.state == "采纳"
    ]


def _peer_editor(project: Project, codes: list[str]) -> None:
    roster = project.roster()
    method = project.policy["method"]
    chosen = st.selectbox("编辑已采纳公司", codes, key=key(project, "valuation_peer"),
                          format_func=lambda code: roster[code].candidate.name)
    row = project.peers.get(chosen, blank_peer())
    candidate = roster[chosen].candidate
    updated = dict(row)
    with st.form(key(project, f"peer_values_{digest(chosen)}_{project.revision}"), border=False):
        st.caption("金额均为人民币亿元。候选资料中的历史行情不会带入这里。")
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
            project.peers[chosen] = updated
            project.invalidate()
            project.note("保存可比取价与参数", candidate.name, code=candidate.code, actor=project.reviewer, evidence=updated["source"])
            st.rerun()


def _peer_inputs(project: Project) -> None:
    codes = _adopted(project)
    if not codes:
        st.info("采纳公司会自动进入这里。请先在三态复核中记录至少 3 家采纳样本。")
        return
    with st.container(border=True, key=key(project, "wb_panel_values")):
        st.subheader(f"补齐已采纳样本 · {len(codes)} 家")
        peers = project.selected()
        weight = sum(number(p.get("weight_pct")) or 0 for p in peers)
        date_count = sum(bool(iso_date(p.get("pricing_date")) and iso_date(p.get("report_end"))) for p in peers)
        st.caption(f"已填取价及财报日期：{date_count}/{len(codes)} 家 · 已填权重合计：{weight:.2f}%")
        with st.expander("查看已采纳样本与数据"):
            table(
                ["公司", "财报截止日", "取价日", "市值（亿元）", "权重", "入选理由"],
                [[p["company"], p.get("report_end") or "待补", p.get("pricing_date") or "待补",
                  money(p.get("market_cap")), f"{number(p.get('weight_pct')) or 0:g}%", p["selection_reason"]] for p in peers],
            )
        if st.button("给已采纳样本等分权重", disabled=not 3 <= len(codes) <= 8):
            base = round(100 / len(codes), 2)
            for i, code in enumerate(codes):
                row = project.peers.setdefault(code, blank_peer())
                row["weight_pct"] = round(100 - base * i, 2) if i == len(codes) - 1 else base
            project.invalidate()
            project.note("分配等权重", f"{len(codes)} 家已采纳公司", actor=project.reviewer)
            st.rerun()
        with st.expander("逐家补取价、口径与修正", expanded=date_count != len(codes)):
            _peer_editor(project, codes)


def _advanced(project: Project) -> None:
    policy = project.policy
    with st.expander("选填：折扣、股权调整与 DCF"):
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
    _peer_inputs(project)
    _advanced(project)
    blockers = inspect(project)["blockers"] + dcf_blockers(project)
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
    low, high, value = (metrics[k] for k in ("low_equity_value", "high_equity_value", "equity_value"))
    position = 50 if high == low else 100 * (value - low) / (high - low)
    st.html(
        '<div class="wb-result"><div class="wb-result-main"><span class="wb-kicker">股权价值草稿</span>'
        f'<strong>{money(value)}<small>亿元</small></strong>'
        f'<span>{escape(market["method"])} · 加权修正倍数 {metrics["weighted_multiple"]:.3f}×</span></div>'
        '<div class="wb-result-range"><span>所选样本观察区间</span>'
        f'<div class="wb-meter" style="--marker:{position:.2f}%"><i></i></div>'
        f'<div><b>{money(low)}</b><b>{money(high)} 亿元</b></div>'
        '<small>基于所选样本最小与最大修正倍数。</small></div></div>'
    )
    with st.expander("计算路径与样本明细", expanded=True):
        selected = market["peer_analysis"]
        table(["公司", "原始倍数", "修正后倍数", "权重", "取价日"], [
            [row["company"], f"{row['raw_multiple']:.3f}×", f"{row['adjusted_multiple']:.3f}×",
             f"{row['weight_pct']:.2f}%", row["pricing_date"]] for row in selected
        ])
        method = market["method"]
        basis = {"EV/EBITDA": "EBITDA", "P/B": "账面权益", "P/E": "归母净利润"}[method]
        detail = f"目标{basis} {money(market['target_value'])} × 加权修正倍数 {metrics['weighted_multiple']:.4f}"
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
