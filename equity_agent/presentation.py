from __future__ import annotations

import html
import inspect
import math
from pathlib import Path
from secrets import token_hex
from typing import Any

import streamlit as st


def number(value: Any) -> float | None:
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def money(value: Any) -> str:
    value = number(value)
    return "未提供" if value is None else f"{value:,.2f}"


def install_styles() -> None:
    st.html(Path(__file__).with_name("ui.css"))


def scroll_to_top() -> None:
    # Only fixed application code is executed; uploaded text never enters the script.
    command = "requestAnimationFrame(() => { document.querySelector('[data-testid=stMain]')?.scrollTo(0, 0); window.scrollTo(0, 0); });"
    if "unsafe_allow_javascript" in inspect.signature(st.html).parameters:
        st.html(f"<script>{command}</script><!-- {token_hex(4)} -->", unsafe_allow_javascript=True)
    else:
        from streamlit.components.v1 import html as component_html
        command = command.replace("document.querySelector", "window.parent.document.querySelector").replace("window.scrollTo", "window.parent.scrollTo")
        component_html(f"<script>{command}</script>", height=0)


def table(headers: list[str], rows: list[list[Any]]) -> None:
    heading = "".join(f'<th scope="col">{html.escape(str(item))}</th>' for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape('' if item is None else str(item))}</td>" for item in row) + "</tr>"
        for row in rows
    )
    st.html(f'<div class="table-wrap" tabindex="0"><table class="readable-table"><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>')
