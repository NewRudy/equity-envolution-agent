from __future__ import annotations

import hmac
import os
from html import escape
from pathlib import Path

import streamlit as st

from equity_agent.demo import demo_project
from equity_agent.intake_ui import profile_page, pool_page
from equity_agent.opc_package import ARCHIVE_DATE, ARCHIVE_MODEL, MAX_PACKAGE_SIZE, ArchiveCase, OPCPackage, read_opc_package
from equity_agent.presentation import install_styles, scroll_to_top
from equity_agent.project import Profile, Project, restore_project
from equity_agent.review_ui import review_page
from equity_agent.ui_common import MODE_LABEL, PAGES, badge, go, project_strip, working_label
from equity_agent.valuation_ui import paper_page, valuation_page


st.set_page_config(page_title="股权估值工作台", page_icon="▦", layout="wide", initial_sidebar_state="auto")
install_styles()


def require_access() -> None:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or st.session_state.get("authenticated"):
        return
    _, center, _ = st.columns([0.4, 1.4, 0.4])
    with center:
        st.title("股权估值工作台")
        with st.form("access-form", border=True):
            provided = st.text_input("访问口令", type="password")
            if st.form_submit_button("进入工作台", type="primary", width="stretch"):
                if hmac.compare_digest(provided.encode(), expected.encode()):
                    st.session_state["authenticated"] = True
                    st.rerun()
                st.error("口令不正确，请重新输入。")
    st.stop()


def initialize() -> None:
    st.session_state.setdefault("wb_projects", {})
    st.session_state.setdefault("wb_active", "")
    st.session_state.setdefault("wb_page", "home")
    st.session_state.setdefault("wb_recent", {})
    st.session_state.setdefault("wb_case_projects", {})
    st.session_state.setdefault("wb_pools", {})
    if "wb_opc" not in st.session_state:
        st.session_state["wb_opc"] = None
        if source := os.getenv("OPC_PACKAGE_PATH", ""):
            path = Path(source).expanduser()
            try:
                if path.stat().st_size > MAX_PACKAGE_SIZE:
                    raise ValueError("交付包超过大小限制。")
                st.session_state["wb_opc"] = read_opc_package(path.name, path.read_bytes())
            except (OSError, ValueError):
                st.session_state["wb_opc_local_error"] = True


def activate(project: Project, page: str) -> None:
    st.session_state["wb_projects"][project.id] = project
    st.session_state["wb_active"] = project.id
    go(page)


def select_project() -> None:
    project_id = st.session_state["wb_select_project"]
    if project_id not in st.session_state["wb_projects"] or project_id == st.session_state["wb_active"]:
        return
    st.session_state["wb_active"] = project_id
    st.session_state["wb_page"] = st.session_state["wb_recent"].get(project_id, "profile")
    st.session_state["wb_scroll"] = True


def sidebar(project: Project | None, page: str, opc: OPCPackage | None) -> None:
    with st.sidebar:
        st.html(
            '<div class="wb-brand"><div class="wb-mark" aria-hidden="true"><i></i><i></i><i></i></div>'
            '<div><strong>股权估值</strong><span>可比池工作台</span></div></div>'
        )
        if st.button("工作台总览", width="stretch", type="primary" if page == "home" else "secondary"):
            go("home")
        projects = st.session_state["wb_projects"]
        if project and projects:
            if st.session_state.get("wb_select_project") != project.id:
                st.session_state["wb_select_project"] = project.id
            st.selectbox(
                "切换项目", list(projects),
                format_func=lambda k: f"{projects[k].profile.name or '未命名项目'} · {MODE_LABEL[projects[k].mode]}",
                key="wb_select_project", on_change=select_project,
            )
            st.html('<div class="wb-rail-label">工作流</div>')
            for name, (number, label) in PAGES.items():
                if st.button(f"{number}　{label}", width="stretch", key=f"rail_{name}", type="primary" if page == name else "secondary"):
                    go(name)
            if project.profile.products:
                st.html(
                    f'<div class="wb-rail-profile"><span>标的画像 · {escape(working_label(project))}</span>'
                    f'<strong>{escape(project.profile.products[:145])}</strong>'
                    f'<small>{escape(project.profile.business_model[:110])}</small></div>'
                )
        st.html(
            '<div class="wb-rail-label">资料状态</div>'
            f'<div class="wb-source-tag">{badge("已载入" if opc else "未载入", "ok" if opc else "neutral")} '
            '<span>OPC 参考资料</span></div>'
        )
        if opc:
            st.caption(f"行情快照 {opc.pool.snapshot_date} · {len(opc.cases)} 个归档案例")
        st.caption("工作进度可下载为项目草稿，稍后继续。")


def open_case(case: ArchiveCase) -> None:
    archives = st.session_state["wb_case_projects"]
    projects = st.session_state["wb_projects"]
    if case.id in archives and archives[case.id] in projects:
        activate(projects[archives[case.id]], "review")
    values = dict(case.profile)
    project = Project(
        Profile(
            case.title, case.screening.as_of,
            products=values.get("主营产品", ""), customers=values.get("客户类型", ""),
            business_model=values.get("商业模式", values.get("客户 / 模式", "")),
            difference=values.get("不纳入", ""), source=case.screening.source,
            route={"4": "穿透到经营资产", "14": "最近融资价格参考"}.get(case.id, "上市公司比较法"),
        ),
        mode="archive", reference_profile=case.profile,
    )
    project.save_screen(case.screening, {
        "归档模型": ARCHIVE_MODEL, "归档日期": ARCHIVE_DATE, "归档上市月数": 24,
        "归档入池逻辑": "板块或行业标签命中；排除 ST/退市风险、B 股、上市不足 24 个月。",
    })
    archives[case.id] = project.id
    activate(project, "review")


def home(opc: OPCPackage | None) -> None:
    st.html(
        '<header class="wb-hero"><div class="wb-hero-main"><div class="wb-kicker"><b></b> 投资估值 / 可比公司分析</div>'
        '<h1>股权估值工作台</h1><p>把行业筛选、业务证据和取价依据整理到一个项目里。'
        '逐家复核后，再测算并导出可回查的估值底稿。</p></div>'
        '<div class="wb-hero-path" aria-label="工作流程"><span><b>01</b> 画像与候选</span><span><b>02</b> 证据与复核</span>'
        '<span><b>03</b> 测算与底稿</span></div></header>'
    )
    a, b, _ = st.columns([1, 1, 1.25])
    if a.button("＋ 新建估值项目", type="primary", width="stretch"):
        activate(Project(), "profile")
    if b.button("打开合成示例", width="stretch"):
        activate(demo_project(), "review")
    st.html('<div class="wb-section-label"><span>资料与项目</span></div>')
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True, key="home_opc"):
        st.html(f'<div class="wb-card-kicker">01　候选资料 {badge("已载入" if opc else "离线资料", "ok")}</div>')
        st.subheader("OPC 案例回放")
        if opc:
            st.caption(f"{len(opc.pool.candidates):,} 家 A 股资料 · 行情快照 {opc.pool.snapshot_date} · 初筛归档 {ARCHIVE_DATE}")
            ids = {case.id: case for case in opc.cases}
            keys = list(ids)
            chosen = st.selectbox("选择历史标的", keys, index=keys.index("17") if "17" in ids else 0,
                                  format_func=lambda item: ids[item].title, key="wb_case_select")
            if st.button("打开候选回放", width="stretch"):
                open_case(ids[chosen])
        else:
            st.write("导入 OPC ZIP，查看 20 个归档案例、模型初筛建议和公司业务摘要。")
        if st.session_state.get("wb_opc_local_error"):
            st.caption("未能读取预设资料，可在这里重新选择原 ZIP。")
        with st.expander("导入 OPC ZIP" if not opc else "更换 OPC 资料"):
            with st.form("opc_import", border=False):
                file = st.file_uploader("选择原始交付包", type=["zip"])
                if st.form_submit_button("读取候选资料", width="stretch"):
                    if not file:
                        st.error("请先选择 ZIP。")
                    else:
                        try:
                            with st.spinner("正在读取快照和归档案例…"):
                                new_package = read_opc_package(file.name, file.getvalue())
                            st.session_state["wb_opc"] = new_package
                            st.session_state["wb_case_projects"] = {}
                            st.session_state.pop("wb_opc_local_error", None)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
    with right, st.container(border=True, key="home_resume"):
        st.html('<div class="wb-card-kicker">02　继续工作</div>')
        st.subheader("恢复项目草稿")
        st.write("打开之前下载的项目草稿，继续编辑画像、复核记录或估值输入。")
        with st.expander("打开项目 JSON"):
            with st.form("restore", border=False):
                file = st.file_uploader("选择工作台导出的项目草稿", type=["json"])
                if st.form_submit_button("恢复项目", width="stretch"):
                    if not file:
                        st.error("请先选择项目 JSON。")
                    else:
                        try:
                            project = restore_project(file.getvalue())
                            activate(project, "review" if project.roster() else "profile")
                        except ValueError as exc:
                            st.error(str(exc))
        projects = list(st.session_state["wb_projects"].values())
        if projects:
            st.caption("本次会话中的项目")
            for item in projects[-3:][::-1]:
                if st.button(f"打开 · {item.profile.name or '未命名项目'}", key=f"resume_{item.id}", width="stretch"):
                    activate(item, st.session_state["wb_recent"].get(item.id, "profile"))


require_access()
initialize()
current = st.session_state["wb_projects"].get(st.session_state["wb_active"])
page = st.session_state["wb_page"] if current else "home"
opc = st.session_state["wb_opc"]
sidebar(current, page, opc)
if page == "home":
    home(opc)
else:
    st.session_state["wb_recent"][current.id] = page
    project_strip(current)
    if page == "pool":
        pool_page(current, opc.pool if opc else None)
    else:
        {"profile": profile_page, "review": review_page, "valuation": valuation_page, "paper": paper_page}[page](current)
st.html('<div class="wb-footer"><span>股权估值工作台</span><span>估值分析草稿 · 供专业复核</span></div>')
if st.session_state.pop("wb_scroll", False):
    scroll_to_top()
