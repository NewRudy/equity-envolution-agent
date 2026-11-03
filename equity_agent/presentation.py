from __future__ import annotations

import html
import inspect
import math
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

import streamlit as st

from .financials import FinancialRecord
from .workpaper import create_workpaper_excel


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


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
    style = Path(__file__).with_name("ui.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{style}</style>", unsafe_allow_html=True)


def scroll_to_top() -> None:
    # Only fixed application code is executed; uploaded text never enters this script.
    command = "requestAnimationFrame(() => { document.querySelector('[data-testid=stMain]')?.scrollTo(0, 0); window.scrollTo(0, 0); });"
    if "unsafe_allow_javascript" in inspect.signature(st.html).parameters:
        st.html(f"<script>{command}</script><!-- {token_hex(4)} -->", unsafe_allow_javascript=True)
    else:
        from streamlit.components.v1 import html as component_html
        command = command.replace("document.querySelector", "window.parent.document.querySelector").replace("window.scrollTo", "window.parent.scrollTo")
        component_html(f"<script>{command}</script>", height=0)


def table(headers: list[str], rows: list[list[Any]]) -> None:
    heading = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(text(item))}</td>" for item in row) + "</tr>"
        for row in rows
    )
    st.markdown(
        f'<div class="table-wrap"><table class="readable-table"><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def stats(items: list[tuple[str, str]]) -> None:
    cards = "".join(
        '<div class="stat-card">'
        f'<div class="stat-label">{html.escape(label)}</div>'
        f'<div class="stat-value">{html.escape(value)}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="stat-grid">{cards}</div>', unsafe_allow_html=True)


def stepper(current: int) -> None:
    titles = ["公司数据", "可比公司", "确认计算", "分析结果"]
    steps = "".join(
        f'<li class="{"current" if i == current else "done" if i < current else ""}"'
        f'{" aria-current=\"step\"" if i == current else ""}>{i}. {title}</li>'
        for i, title in enumerate(titles, 1)
    )
    st.markdown(f'<ol class="steps" aria-label="估值步骤">{steps}</ol>', unsafe_allow_html=True)


def show_financials(records: list[FinancialRecord], include_sources: bool = True) -> None:
    table(
        ["年度", "营业收入", "EBITDA", "净债务", "账面权益"],
        [[record.year, *[money(record.metrics.get(key)) for key in ("revenue", "ebitda", "net_debt", "book_equity")]] for record in records],
    )
    st.caption("上表金额单位均为人民币亿元。")
    if include_sources:
        with st.expander("查看原文件与字段位置"):
            for record in reversed(records):
                st.markdown(f"**{record.year} 年**")
                st.write(record.source)
                for key, label in (("revenue", "收入"), ("ebitda", "EBITDA"), ("net_debt", "净债务"), ("book_equity", "账面权益")):
                    if key in record.provenance:
                        st.write(f"{label}：{record.provenance[key]}")


def show_peer_summary(peers: list[dict[str, Any]]) -> None:
    table(
        ["公司", "本次纳入", "权重"],
        [
            [
                text(peer.get("company")) or "待命名公司",
                "已纳入" if peer.get("selected") else "未纳入",
                f"{number(peer.get('weight_pct')) or 0:.2f}%" if peer.get("selected") else "—",
            ]
            for peer in peers
        ],
    )


def show_result(company: str, records: list[FinancialRecord], result: dict[str, Any]) -> None:
    demo = result.get("is_demo", False)
    market = result.get("market_method", {})
    values = market.get("result") or {}
    if demo:
        st.markdown(
            '<div class="demo-note"><strong>这是完整的合成示例。</strong>公司、行情和复核条件均为预设，只用来说明操作与结果长什么样。</div>',
            unsafe_allow_html=True,
        )
    st.header("示例结果" if demo else f"{company} · 估值结果")
    st.write(f"采用 {market.get('method')}，共纳入 {len(market.get('peer_analysis', []))} 家可比公司。")
    if not values:
        st.error("本次输入尚未满足计算条件，请返回确认页补齐。")
        return

    st.markdown(
        '<div class="result-card"><div class="label">股权价值参考</div>'
        f'<div class="value">{html.escape(money(values.get("equity_value")))}<small>亿元</small></div>'
        f'<div class="range">所选样本观察范围：{html.escape(money(values.get("low_equity_value")))} — '
        f'{html.escape(money(values.get("high_equity_value")))} 亿元</div></div>',
        unsafe_allow_html=True,
    )
    st.caption("观察范围来自所选公司修正后倍数的最小值和最大值；基准值使用你确认的权重计算。")
    output = create_workpaper_excel(company, records, result)
    st.download_button(
        "下载示例底稿（Excel）" if demo else "下载估值底稿（Excel）",
        output,
        file_name=f"{'合成示例_' if demo else ''}估值工作底稿_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        width="stretch",
    )
    stats([
        ("加权修正倍数", f"{values['weighted_multiple']:.2f} 倍"),
        ("流动性折扣", f"{market.get('dlom_pct', 0):.1f}%"),
        ("其他股权调整", f"{money(market.get('bridge_adjustment'))} 亿元"),
    ])

    with st.expander("这个结果怎么算出来的", expanded=True):
        multiplier = values["weighted_multiple"]
        target_value = market.get("target_value")
        if market.get("method") == "EV/EBITDA":
            operating_value = target_value * multiplier
            equity_before_discount = operating_value - market["target_net_debt"]
            calculation = [
                ["企业价值", f"目标 EBITDA {money(target_value)} × {multiplier:.2f} 倍", f"{money(operating_value)} 亿元"],
                ["扣除净债务", f"{money(operating_value)} − {money(market['target_net_debt'])}", f"{money(equity_before_discount)} 亿元"],
            ]
        else:
            equity_before_discount = target_value * multiplier
            calculation = [
                ["市场法股权价值", f"目标账面权益 {money(target_value)} × {multiplier:.2f} 倍", f"{money(equity_before_discount)} 亿元"],
            ]
        calculation.append([
            "应用折扣与调整",
            f"× (1 − {market.get('dlom_pct', 0):.1f}%) + {money(market.get('bridge_adjustment'))}",
            f"{money(values.get('equity_value'))} 亿元",
        ])
        table(["步骤", "计算", "结果"], calculation)

    with st.expander("查看可比公司、修正和数据来源"):
        table(
            ["可比公司", "原始倍数", "修正后倍数", "权重"],
            [[peer["company"], f"{peer['raw_multiple']:.2f} 倍", f"{peer['adjusted_multiple']:.2f} 倍", f"{peer['weight_pct']:.2f}%"] for peer in market.get("peer_analysis", [])],
        )
        for peer in market.get("peer_analysis", []):
            st.markdown(f"**{peer['company']}**")
            st.write(f"报告期：{peer['period']}；取价依据：{peer['price_basis']}")
            st.write(f"来源：{peer['source']}")
            st.write(f"入选理由：{peer['selection_reason']}")
            st.write(f"修正理由：{peer['adjustment_reason']}")

    with st.expander("查看目标公司财务数据"):
        show_financials(records, include_sources=False)
        for record in records:
            st.write(f"{record.year} 年来源：{record.source}")
    dcf = result.get("valuations", {}).get("dcf", {})
    if dcf:
        with st.expander("查看现金流参照（DCF）"):
            table(
                ["情景", "股权价值（亿元）"],
                [[label, money(dcf[key]["equity_value"])] for key, label in (("bear", "偏低"), ("base", "基准"), ("bull", "偏高"))],
            )
            st.write("这是独立的敏感性参照，不与市场法加权。")
    warnings = result.get("warnings", [])
    if warnings:
        with st.expander(f"财务数据提示（{len(warnings)} 项）"):
            for item in warnings:
                st.write(f"• {item['message']}")
    st.caption(
        f"生成于 {result.get('run_at', '本次会话')} · "
        + ("复核条件为演示预设。" if demo else f"复核人：{market.get('reviewer', '未填写')}。")
    )
