"""Shared view pieces for the workbench."""

from __future__ import annotations

from html import escape

import streamlit as st

from .project import Project
from .screening import ScreenRow


PAGES = {
    "profile": ("01", "标的与口径"),
    "pool": ("02", "候选池"),
    "review": ("03", "三态复核"),
    "valuation": ("04", "估值测算"),
    "paper": ("05", "结果与底稿"),
}
MODE_LABEL = {"real": "真实项目", "demo": "合成示例", "archive": "历史回放"}
TONES = {"采纳": "ok", "存疑": "warn", "剔除": "no", "待复核": "neutral"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def go(page: str) -> None:
    st.session_state["wb_page"] = page
    st.session_state["wb_scroll"] = True
    st.rerun()


def key(project: Project, name: str) -> str:
    return f"{name}_{project.id}"


def badge(label: str, tone: str = "neutral") -> str:
    return f'<span class="wb-badge {tone}">{escape(label)}</span>'


def plain(value: str, class_name: str = "wb-copy") -> None:
    st.html(f'<div class="{class_name}">{escape(value)}</div>')


def page_intro(page: str, summary: str, project: Project) -> None:
    step, title = PAGES[page]
    tone = {"real": "ok", "demo": "warn", "archive": "model"}[project.mode]
    st.html(
        f'<div class="wb-page"><div class="wb-context"><span class="wb-kicker">工作区 / {step}</span>'
        f'{badge(MODE_LABEL[project.mode], tone)}</div><h1>{title}</h1>'
        f'<p>{escape(summary)}</p></div>'
    )


def project_strip(project: Project) -> None:
    current = project.profile
    marker = "归档筛选日" if project.mode == "archive" else "估值基准日"
    st.html(
        f'<div class="wb-project-strip"><div><span>当前标的</span><strong>{escape(current.name or "未命名项目")}</strong></div>'
        f'<div><span>{marker}</span><strong>{escape(current.benchmark or "待设置")}</strong></div>'
        f'<div><span>工作路径</span><strong>{escape("候选回放" if project.mode == "archive" else current.route)}</strong></div></div>'
    )


def nav_actions(left: tuple[str, str], right: tuple[str, str], *, disabled: bool = False) -> None:
    cols = st.columns(2)
    if cols[0].button(left[0], width="stretch", key=f"nav_back_{left[1]}"):
        go(left[1])
    if cols[1].button(right[0], type="primary", width="stretch", disabled=disabled, key=f"nav_next_{right[1]}"):
        go(right[1])


def counter_cards(items: list[tuple[str, int | str, str]]) -> None:
    parts = "".join(
        f'<div class="wb-stat"><span>{escape(label)}</span><strong>{escape(str(value))}</strong>'
        f'<small>{escape(detail)}</small></div>' for label, value, detail in items
    )
    st.html(f'<div class="wb-stats">{parts}</div>')


def current_state(project: Project, row: ScreenRow) -> str:
    saved = project.decision(row)
    return "规则剔除" if row.gate == "剔除" else saved.state if saved else "待复核"


def working_label(project: Project) -> str:
    if project.result:
        return "草稿已生成"
    if project.selected():
        return "已有采纳"
    if project.roster():
        return "待逐家复核"
    return "待补充候选" if project.ready_profile else "待建档"
