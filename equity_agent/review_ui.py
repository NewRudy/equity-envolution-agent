from __future__ import annotations

from collections import Counter
from html import escape

import streamlit as st

from .model_review import ModelConfig, candidate_payload, suggest
from .opc_package import ARCHIVE_MODEL
from .presentation import table
from .project import LISTED, Project, digest
from .screening import DECISIONS, ScreenRow
from .ui_common import TONES, badge, counter_cards, current_state, key, nav_actions, page_intro, plain


def _model_option(project: Project, row: ScreenRow) -> None:
    advice = project.model_advice.get(row.candidate.key, {})
    basis = project.decision_basis(row)
    if advice.get("basis") == basis:
        plain(f"模型核对 · {advice['verdict']}：{advice['reason']}", "wb-archive-line")
        for quote in advice["evidence"]:
            plain(f"原文：「{quote}」", "wb-source-line")
        plain(f"模型：{advice['model']} · 仅供人工判断", "wb-source-line")
    config = ModelConfig.from_env()
    if not config or not any(candidate_payload(row.candidate).values()):
        return
    with st.expander("选做：模型辅助核对"):
        plain(f"点击后将标的业务画像与当前候选的业务摘录发送至 {config.host}。请先确认这些文本可共享。")
        if st.button("发送业务摘录并核对", key=key(project, "ask_model")):
            try:
                with st.spinner("正在核对业务实质…"):
                    answer = suggest(config, project.profile, row.candidate)
                project.model_advice[row.candidate.key] = answer | {"basis": basis}
                project.note("模型辅助核对", f"{answer['model']} · {answer['verdict']}", code=row.candidate.code, evidence=row.candidate.source)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _details(project: Project, row: ScreenRow) -> None:
    candidate = row.candidate
    state = current_state(project, row)
    with st.container(border=True, key=key(project, "wb_panel_review")):
        tag = badge(state, TONES.get(state, "no"))
        st.html(
            f'<div class="wb-evidence-title"><div><span class="wb-kicker">公司证据 / {escape(candidate.code or "无代码")}</span>'
            f'<h2>{escape(candidate.name)}</h2></div>{tag}</div>'
        )
        archive = row.gate == "归档"
        st.html(
            f'<div class="wb-suggestion">{badge(row.suggestion, "model" if archive else "neutral")}'
            f'<p>{escape(row.reason or "未提供理由。")}</p></div>'
        )
        if row.evidence:
            plain(("归档提取的产品：" if archive else "原文命中：") + "；".join(row.evidence))
        with st.expander("查看业务原文与来源", expanded=not bool(row.evidence)):
            if candidate.tags:
                plain("行业标签：" + " / ".join(candidate.tags))
            plain(f"上市日期：{candidate.listed_on or '未核验'} · 市场：{candidate.market or '未核验'} · 资料期：{candidate.period or '未提供'}")
            if candidate.products:
                plain("主营构成：" + "；".join(candidate.products))
            if candidate.business:
                plain("主营业务：" + candidate.business)
            if candidate.commentary:
                plain("经营评述：" + candidate.commentary)
            plain("来源：" + (candidate.source or "待补充"), "wb-source-line")
        if row.gate == "剔除":
            st.caption(f"命中 {row.rule}。若确有例外，请回到候选池调整适用条件，再重新复核。")
            return
        if project.mode == "real":
            _model_option(project, row)
        saved = project.decision(row)
        if saved:
            plain(f"最近记录：{saved.reviewer} · {saved.at.replace('T', ' ').replace('+00:00', ' UTC')}", "wb-source-line")
        choices = (*DECISIONS, "待复核")
        form_key = key(project, f"decision_{digest(candidate.key)}_{len(project.audit)}")
        with st.form(form_key, border=False):
            st.subheader("记录人工判断" if project.mode != "demo" else "练习三态复核")
            status = st.radio("复核决定", choices, horizontal=True,
                              index=choices.index(saved.state) if saved else None,
                              help="采纳才会进入估值；存疑待补证据；待复核用于撤回原判断。")
            reason = st.text_area("判断依据", value=saved.reason if saved else "", max_chars=1500, height=94,
                                  placeholder="例如：产品用途和客户相近；但收入以一次性交付为主，需核对订阅收入占比。")
            cite = st.text_input("补充证据定位", value=saved.evidence if saved else "", max_chars=800,
                                 placeholder="来源、页码或数据表位置；可沿用上面的公司来源")
            reviewer = st.text_input("复核人", value=project.reviewer, max_chars=80, placeholder="填写姓名或工作标识")
            if st.form_submit_button("保存复核决定", type="primary", width="stretch"):
                try:
                    if not status:
                        raise ValueError("请先选择复核决定。")
                    if status == "采纳" and not (candidate.business or candidate.products or candidate.commentary or cite.strip()):
                        raise ValueError("缺少公司业务资料。采纳前请补充可回查的业务证据定位。")
                    project.decide(candidate.key, status, reason, reviewer, cite)
                    st.toast("已保存复核决定")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def review_page(project: Project) -> None:
    page_intro("review", "对照标的画像和候选资料，逐家记录采纳、存疑或剔除的依据。", project)
    if project.screen and not project.active_screen:
        st.warning("标的口径有变更，旧判断已失效。请先重建候选池。")
        nav_actions(("回到候选池", "pool"), ("查看工作底稿", "paper"))
        return
    roster = project.roster()
    states = {code: current_state(project, row) for code, row in roster.items()}
    counts = Counter(states.values())
    archived = project.mode == "archive"
    hard = project.screen.hard_removed if archived and project.screen else counts["规则剔除"]
    counter_cards([
        ("待复核", counts["待复核"], "尚未记录人工决定"),
        ("已采纳", counts["采纳"], "归档复核意见" if archived else "用于后续测算"),
        ("存疑", counts["存疑"], "待补资料"),
        ("人工剔除", counts["剔除"], f"另有 {hard} 家规则剔除"),
    ])
    if project.mode == "archive" and project.screen:
        screen = project.screen
        plain(
            f"归档：行业入池 {screen.scope} → 进入模型 {len(screen.rows)}。"
            + "  ".join(f"{label} {value}" for label, value in screen.archive_counts.items()),
            "wb-archive-line",
        )
        st.caption(f"初筛为 {ARCHIVE_MODEL} 的历史输出；行情快照 {screen.snapshot_date}，筛选归档 {screen.as_of}。")
        plain(screen.note)
    if not roster:
        st.info("这个项目没有进入复核的候选。" if project.mode == "archive" else "先在候选池生成名单或提交指定公司。")
        nav_actions(("返回候选池", "pool"), ("查看工作底稿", "paper"))
        return
    with st.expander("对照标的画像"):
        pairs = project.reference_profile or (
            ("主营产品", project.profile.products), ("客户类型", project.profile.customers),
            ("商业模式", project.profile.business_model), ("容易误选", project.profile.difference),
        )
        table(["维度", "标的"], [[label, value or "未提供"] for label, value in pairs])
    q, s, m = st.columns([1.25, 1, 1])
    query = q.text_input("搜索公司或代码", key=key(project, "review_query"), placeholder="输入公司名称或证券代码").strip().casefold()
    state_filter = s.selectbox(
        "人工状态", ["全部", "待复核", "采纳", "存疑", "剔除", "规则剔除"], key=key(project, "review_filter"),
    )
    suggestions = list(dict.fromkeys(row.suggestion for row in roster.values()))
    advice = m.selectbox("初筛建议", ["全部", *suggestions], key=key(project, "review_advice_filter"))
    codes = [
        code for code, row in roster.items()
        if (not query or query in f"{row.candidate.code} {row.candidate.name}".casefold())
        and (state_filter == "全部" or states[code] == state_filter)
        and (advice == "全部" or row.suggestion == advice)
    ]
    if not codes:
        st.info("没有符合条件的公司，请调整搜索或筛选。")
    else:
        left, right = st.columns([1.05, 1.2], gap="medium")
        with left, st.container(border=True, key=key(project, "wb_panel_roster")):
            st.subheader("复核名单")
            limit = 350
            st.caption(f"找到 {len(codes)} 家。可直接点选表中公司或用下拉框定位。" +
                       (f" 当前展示前 {limit} 家，可用名称或代码继续缩小范围。" if len(codes) > limit else ""))
            codes = codes[:limit]
            table_key = key(project, f"review_table_{digest(codes)}")
            event = st.dataframe(
                [{"公司": roster[code].candidate.name, "代码": roster[code].candidate.code,
                  "初筛": roster[code].suggestion, "状态": states[code]} for code in codes],
                hide_index=True, height=318, row_height=36, key=table_key, on_select="rerun", selection_mode="single-row",
                placeholder="待补",
            )
            pick_key = key(project, "review_company")
            previous = st.session_state.get(key(project, "review_last_pick"))
            row_indices = event.selection.rows
            if row_indices and row_indices[0] < len(codes):
                clicked = codes[row_indices[0]]
                if previous != (table_key, clicked):
                    st.session_state[pick_key] = clicked
                    st.session_state[key(project, "review_last_pick")] = (table_key, clicked)
            if st.session_state.get(pick_key) not in codes:
                st.session_state[pick_key] = codes[0]
            chosen = st.selectbox(
                "当前公司", codes, key=pick_key,
                format_func=lambda code: f"{roster[code].candidate.name}  ·  {roster[code].candidate.code or '无代码'}",
            )
        with right:
            _details(project, roster[chosen])
    if project.mode == "archive" or project.profile.route != LISTED:
        nav_actions(("返回候选池", "pool"), ("导出筛选底稿", "paper"))
    else:
        if counts["采纳"] < 3:
            st.caption("测算模板需要至少 3 家采纳样本。样本不足时可以先导出筛选底稿。")
        nav_actions(("返回候选池", "pool"), ("进入估值测算", "valuation"))
