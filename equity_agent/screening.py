"""Build a reviewable comparable pool. Search hits are evidence leads, not decisions."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any


RULE_VERSION = "上市可比 · WB 1.0"
RULES = {
    "H01": "ST / 退市风险标记",
    "H02": "B 股或明确非 A 股",
    "H03": "基准日未上市或上市年限不足",
}
POOL_COLUMNS = ["代码", "公司", "行业标签", "市场", "上市日期", "主营业务", "主营产品", "经营评述", "报告期", "来源"]
DECISIONS = ("采纳", "存疑", "剔除")


def clean(value: Any, limit: int = 3000) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()[:limit]


def iso_date(value: Any) -> date | None:
    value = clean(value, 40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def split_terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(s.strip()[:80] for s in re.split(r"[,，、;；\n|]+", value) if s.strip()))[:30]


def code_key(value: Any) -> str:
    code = unicodedata.normalize("NFKC", clean(value, 36)).upper()
    if re.fullmatch(r"\d{1,6}\.0", code):
        code = code[:-2]
    match = re.fullmatch(r"(?:SH|SZ|BJ)?(\d{6})(?:\.(?:SH|SZ|BJ))?", code)
    return match[1] if match else code.zfill(6) if code.isdecimal() and len(code) <= 6 else code


def identity(code: Any, name: Any) -> str:
    return code_key(code) or "名称:" + unicodedata.normalize("NFKC", clean(name, 200)).casefold()


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    tags: tuple[str, ...] = ()
    market: str = ""
    listed_on: str = ""
    business: str = ""
    products: tuple[str, ...] = ()
    commentary: str = ""
    period: str = ""
    source: str = ""

    @property
    def key(self) -> str:
        return identity(self.code, self.name)


@dataclass(frozen=True)
class ScreenRow:
    candidate: Candidate
    gate: str = "通过"
    rule: str = ""
    suggestion: str = "待人工核对"
    reason: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pool:
    name: str
    candidates: tuple[Candidate, ...]
    kind: str = "自有候选资料"
    snapshot_date: str = ""
    note: str = ""

    @property
    def tags(self) -> list[str]:
        return sorted({tag for candidate in self.candidates for tag in candidate.tags})


@dataclass
class Screening:
    rows: tuple[ScreenRow, ...]
    source: str
    universe: int
    scope: int
    hard_removed: int
    version: str = RULE_VERSION
    snapshot_date: str = ""
    as_of: str = ""
    context: str = ""
    archive: bool = False
    archive_counts: dict[str, int] = field(default_factory=dict)
    note: str = ""


def read_candidate_csv(filename: str, data: bytes) -> Pool:
    if len(data) > 12_000_000:
        raise ValueError("候选资料表超过 12 MB，请按行业拆分后再上传。")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = data.decode("gb18030")
    reader = csv.DictReader(io.StringIO(source))
    if not {"公司", "代码"}.issubset(reader.fieldnames or []):
        raise ValueError("候选资料表需包含「公司」「代码」列，可下载页面模板。")
    rows: dict[str, Candidate] = {}
    for row in reader:
        if not any(clean(row.get(key), 1) for key in POOL_COLUMNS):
            continue
        name = clean(row.get("公司"), 200)
        if not name:
            raise ValueError(f"第 {reader.line_num} 行缺少公司名称。")
        cited = clean(row.get("来源"), 500)
        candidate = Candidate(
            code=code_key(row.get("代码")), name=name, tags=split_terms(clean(row.get("行业标签"), 800)),
            market=clean(row.get("市场"), 40), listed_on=clean(row.get("上市日期"), 24),
            business=clean(row.get("主营业务")), products=split_terms(clean(row.get("主营产品"), 1600)),
            commentary=clean(row.get("经营评述")), period=clean(row.get("报告期"), 40),
            source=f"{filename} / 第 {reader.line_num} 行" + (f" · {cited}" if cited else " · 原始来源待补"),
        )
        if candidate.key in rows:
            raise ValueError(f"第 {reader.line_num} 行的公司 {name} 重复，请合并记录后重试。")
        rows[candidate.key] = candidate
        if len(rows) > 10_000:
            raise ValueError("候选资料表最多 10,000 家，请按行业拆分。")
    if not rows:
        raise ValueError("没有读取到公司，请在表头下方填写候选资料。")
    return Pool(filename, tuple(rows.values()))


def candidate_template() -> bytes:
    stream = io.StringIO()
    csv.writer(stream).writerow(POOL_COLUMNS)
    return stream.getvalue().encode("utf-8-sig")


def run_screen(
    pool: Pool,
    *,
    as_of: str,
    tags: tuple[str, ...],
    terms: tuple[str, ...],
    caution_terms: tuple[str, ...] = (),
    listing_months: int = 24,
    exclude_st: bool = True,
    exclude_non_a: bool = True,
    context: str = "",
) -> Screening:
    benchmark = iso_date(as_of)
    if not benchmark:
        raise ValueError("请先设置有效的估值基准日。")
    if pool.kind == "历史快照" and not tags:
        raise ValueError("请在候选池选择至少一个行业标签，明确检索范围。")
    snapshot_day = iso_date(pool.snapshot_date)
    if snapshot_day and benchmark < snapshot_day:
        raise ValueError(f"当前快照晚于基准日（{pool.snapshot_date}）。请改用当时可取得的资料或人工名单。")
    wanted = set(tags)
    scope = [row for row in pool.candidates if not wanted or wanted.intersection(row.tags)]
    rows: list[ScreenRow] = []
    for company in scope:
        rows.append(_screen_row(company, benchmark, terms, caution_terms, listing_months, exclude_st, exclude_non_a))
    rank = {"优先核对": 0, "差异提示": 1, "待人工核对": 2}
    rows.sort(key=lambda row: (row.gate == "剔除", rank.get(row.suggestion, 3), row.candidate.key))
    return Screening(
        tuple(rows), pool.name, len(pool.candidates), len(scope), sum(row.gate == "剔除" for row in rows),
        snapshot_date=pool.snapshot_date, as_of=as_of, context=context,
        note="停牌与重大重组未自动核验；关键词命中只供复核排序。" + (f" {pool.note}" if pool.note else ""),
    )


def _screen_row(
    company: Candidate, benchmark: date, terms: tuple[str, ...], caution: tuple[str, ...],
    listing_months: int, exclude_st: bool, exclude_non_a: bool,
) -> ScreenRow:
    name = unicodedata.normalize("NFKC", company.name).upper().replace(" ", "")
    if exclude_st and (re.match(r"^(?:[S*]*ST)", name) or "退市" in name or name.endswith("退")):
        return ScreenRow(company, "剔除", "H01", "规则剔除", "证券简称有 ST 或退市标记。")
    market = unicodedata.normalize("NFKC", company.market).replace(" ", "").upper()
    if exclude_non_a and (
        market in {"B股", "B", "港股", "美股", "H股", "境外", "非A股"}
        or re.fullmatch(r"(?:200|900)\d{3}", company.code)
    ):
        return ScreenRow(company, "剔除", "H02", "规则剔除", "市场字段或代码显示为 B 股/非 A 股。")
    listed = iso_date(company.listed_on)
    if listed:
        age = (benchmark.year - listed.year) * 12 + benchmark.month - listed.month - (benchmark.day < listed.day)
        if age < listing_months:
            return ScreenRow(company, "剔除", "H03", "规则剔除", f"按基准日计上市 {max(age, 0)} 个完整月，要求至少 {listing_months} 个月。" if age >= 0 else "上市日在估值基准日之后。")
    unknown = []
    if not listed:
        unknown.append("上市日期")
    if exclude_non_a and not market:
        unknown.append("交易市场")
    if not company.products and not company.business and not company.commentary:
        unknown.append("主营资料")
    gate = "待核验" if unknown else "通过"
    fields = (("主营产品", "；".join(company.products)), ("主营业务", company.business), ("经营评述", company.commentary))
    positive = _find_terms(fields, terms)
    negative = _find_terms(fields, caution)
    suggestion = "差异提示" if negative else "优先核对" if positive else "待人工核对"
    reason = ("以下字段缺失或待核验：" + "、".join(unknown) + "。") if unknown else ""
    reason += (
        "命中差异词，请核对业务占比和角色。" if negative else
        "命中标的检索词，请对照产品、客户与业务模式。" if positive else
        "暂无足够的词证，请查看原始材料或补充关键词。"
    )
    return ScreenRow(company, gate, "", suggestion, reason, tuple((negative + positive)[:8]))


def _find_terms(fields: tuple[tuple[str, str], ...], terms: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for term in terms:
        for label, value in fields:
            at = value.lower().find(term.lower())
            if at >= 0:
                snippet = value[max(at - 22, 0): at + len(term) + 34]
                found.append(f"{label} · {term}：「{snippet}」")
                break
    return found


def removed_by_rule(screening: Screening) -> dict[str, int]:
    return dict(Counter(row.rule for row in screening.rows if row.gate == "剔除"))
