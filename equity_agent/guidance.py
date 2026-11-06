"""Show what supports a choice and what still needs human judgment."""

from __future__ import annotations

from collections import Counter

from .project import LISTED, ROUTES
from .screening import REVIEW_DIMENSIONS, Pool, split_terms


ROUTE_MATERIAL = dict(zip(ROUTES, (
    "同业上市公司业务、财报或行情",
    "同业成交对价及股权条款",
    "标的近期融资协议或交割材料",
    "多业务或控股平台的底层资产资料",
)))
ROUTE_GUIDE = {
    LISTED: "先核对上市公司的业务证据、同口径年度财务和基准日行情。",
    "交易案例比较法": "对齐成交日、权益范围、对价、控制权和交易条件。",
    "最近融资价格参考": "核对融资是否完成、投前投后口径、股份类别与特殊权利。",
    "穿透到经营资产": "确认持股链条与比例，按底层业务分别建档。",
}
ROUTE_DOCUMENTS = {
    "交易案例比较法": (
        "交易对象与成交日", "成交权益比例、对价与币种", "控制权及特殊交易条件", "协议或公告来源",
    ),
    "最近融资价格参考": (
        "融资日期与交割状态", "融资金额与投前投后口径", "股份类别、优先权及特殊条款", "融资文件来源",
    ),
    "穿透到经营资产": (
        "底层经营资产与主营", "持股链条、比例与控制权", "财务和非经营性项目的归属", "结构图或资料来源",
    ),
}

SCREEN_PRESETS = {
    "拓宽搜寻": (0, "纳入较新上市的公司，适合细分行业或早期找样本。"),
    "常规初筛": (24, "先看上市满 24 个月的候选，再逐家核验。"),
    "较长交易记录": (36, "优先保留有较长交易记录的公司，先查看少了哪些样本。"),
    "自定义": (None, "按本项目的行业、交易市场和数据条件设置。"),
}

METHOD_GUIDE = {
    "EV/EBITDA": "看经营盈利及融资桥接。核对 EBITDA 定义、租赁和净债务。",
    "P/B": "看可比净资产。核对资产质量、归母范围和盈利能力差异。",
    "P/E": "看可比股东盈利。核对归母或扣非口径及一次性损益。",
}


def route_suggestions(materials: list[str] | tuple[str, ...]) -> list[str]:
    return [route for route, material in ROUTE_MATERIAL.items() if material in materials]


def tag_suggestions(pool: Pool, products: str) -> list[tuple[str, int, str]]:
    terms = tuple(term.casefold() for term in split_terms(products) if len(term) >= 2)[:12]
    if not terms:
        return []
    hits: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for candidate in pool.candidates:
        business = f"{candidate.business} {' '.join(candidate.products)}".casefold()
        matched = next((term for term in terms if term in business), "")
        if not matched:
            continue
        for tag in set(candidate.tags):
            hits[tag] += 1
            examples.setdefault(tag, f"{candidate.name} · {matched}")
    ordered = sorted(hits, key=lambda tag: (-hits[tag], tag))
    return [(tag, hits[tag], examples[tag]) for tag in ordered[:5]]


def review_suggestion(checks: dict[str, str]) -> str:
    if not checks or all(value == "未核对" for value in checks.values()):
        return ""
    core = [checks.get(name, "未核对") for name in REVIEW_DIMENSIONS[:3]]
    risk = checks.get(REVIEW_DIMENSIONS[-1])
    if core[0] == "关键不匹配":
        return "建议考虑剔除。主营或收入构成存在关键差异，请在理由中定位证据。"
    if any(value != "相近" for value in core) or risk in {"关键不匹配", "待补证据"}:
        return "建议先存疑。补齐关键资料，或说明已发现差异对本次比较的影响。"
    if risk != "相近":
        return "建议核对规模、阶段与风险后再定案。若差异可解释，可考虑采纳。"
    return "可考虑采纳。四项复核均标为相近，请记录共同特征与样本局限。"
