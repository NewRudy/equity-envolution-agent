"""Read the OPC delivery as data; never extract files, execute HTML, or read configuration."""

from __future__ import annotations

import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from zipfile import BadZipFile, ZipFile, ZipInfo

from .screening import Candidate, Pool, ScreenRow, Screening, clean, code_key, iso_date


MAX_PACKAGE_SIZE = 35 * 1024 * 1024
ARCHIVE_DATE = "2026-11-04"
ARCHIVE_MODEL = "glm-4-flash"
SPECIAL_CASES = {
    "4": "穿透案例：归档对象是康复业务，不是保险母公司。其他底层资产需分别建档和取证。",
    "14": "该案例未找到上市可比。归档采用最近融资价格作为参考，本工作台不据此生成上市公司比较法结果。",
    "15": "境外水泥项目：归档的 A 股样本只用于业务参照，需另核对境外可比、币种与市场口径。",
}


@dataclass(frozen=True)
class ArchiveCase:
    id: str
    title: str
    profile: tuple[tuple[str, str], ...]
    screening: Screening


@dataclass(frozen=True)
class OPCPackage:
    pool: Pool
    cases: tuple[ArchiveCase, ...]
    report_period: str


def read_opc_package(filename: str, payload: bytes) -> OPCPackage:
    if len(payload) > MAX_PACKAGE_SIZE:
        raise ValueError("交付包超过 35 MB。请上传原 OPC ZIP。")
    try:
        with ZipFile(io.BytesIO(payload)) as zipfile:
            names = [(_member_name(member), member) for member in zipfile.infolist()]
            html_entries = [
                member for name, member in names
                if re.search(r"(?:^|/)01_前端/工作台v4[^/]*\.html$", name)
            ]
            if len(html_entries) != 1 or html_entries[0].file_size > 28_000_000:
                raise ValueError("未找到唯一的 OPC v4 工作台数据文件，请选择原 ZIP。")
            markup = zipfile.read(html_entries[0]).decode("utf-8-sig")
            snapshot = _embedded(markup, "SNAPSHOT", dict)
            archives = _embedded(markup, "LLM20", list)
            reference = _embedded(markup, "RULESET", dict)
            # The archive pipeline used both the board and industry labels. Retain only labels here.
            fundamentals = next(
                (member for name, member in names if name.endswith("/全网数据/fundamentals.json")), None
            )
            labels = {}
            if fundamentals and fundamentals.file_size < 7_000_000:
                rows = json.loads(zipfile.read(fundamentals)).get("data", {})
                labels = {
                    code_key(k): (clean(v.get("board"), 120), clean(v.get("industry"), 120))
                    for k, v in rows.items() if isinstance(v, dict)
                }
            return _assemble(filename, snapshot, archives, reference, labels)
    except (BadZipFile, UnicodeError, KeyError, TypeError, AttributeError, RuntimeError, OverflowError) as exc:
        raise ValueError("交付包无法读取，请使用原始 OPC v4 ZIP。") from exc


def _member_name(member: ZipInfo) -> str:
    name = member.filename
    if not member.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gb18030")
        except UnicodeError:
            pass
    return name.replace("\\", "/")


def _embedded(markup: str, key: str, expected: type):
    match = re.search(r"(?m)^\s*const " + re.escape(key) + r"\s*=\s*", markup)
    if not match:
        raise ValueError(f"交付包缺少 {key} 数据。")
    value, _ = json.JSONDecoder().raw_decode(markup, match.end())
    if not isinstance(value, expected):
        raise ValueError(f"交付包中的 {key} 格式不匹配。")
    return value


def _assemble(filename: str, snapshot: dict, archives: list, reference: dict, labels: dict) -> OPCPackage:
    metadata = snapshot["meta"]
    quote_day = clean(metadata.get("snapshot_date"), 20)
    if not iso_date(quote_day):
        raise ValueError("无法识别工作台快照日期。")
    node_tags = defaultdict(list)
    for tag, codes in snapshot["nodes"].items():
        for code in codes:
            node_tags[code_key(code)].append(clean(tag, 120))

    rows = {}
    for company in snapshot["companies"]:
        code, name = code_key(company.get("code")), clean(company.get("name"), 200)
        if not code or not name or code in rows:
            raise ValueError("交付包有无效或重复的公司记录。")
        products = []
        for segment in company.get("seg") or []:
            if not segment.get("n"):
                continue
            name_text = clean(segment["n"], 120)
            share = segment.get("r")
            period = clean(segment.get("p"), 20)
            detail = f"{share:.1%}" if isinstance(share, (int, float)) and 0 <= share <= 1 else ""
            suffix = " · ".join(v for v in (detail, period) if v)
            products.append(f"{name_text}（{suffix}）" if suffix else name_text)
        rows[code] = Candidate(
            code, name, tuple(dict.fromkeys(filter(None, (
                clean(company.get("industry"), 120), *labels.get(code, ()), *node_tags[code],
            )))),
            market=clean(company.get("market"), 20),
            listed_on=clean(company.get("listing_date"), 20),
            business=clean(company.get("main_biz")),
            products=tuple(products[:16]),
            commentary=clean(company.get("review")),
            period=clean(company.get("report_date"), 24),
            source=f"{filename} / SNAPSHOT · {code}（本地行情与 F10 资料）",
        )

    source = f"OPC 交付包 · {filename}"
    version = "OPC 归档 / 参考规则 " + clean(reference.get("meta", {}).get("version"), 40)
    cases = []
    seen = set()
    for entry in archives:
        case_id = clean(entry["id"], 12)
        if case_id in seen:
            raise ValueError("交付包有重复的归档标的编号。")
        seen.add(case_id)
        review_rows = []
        case_codes = set()
        for item in entry["items"]:
            code = code_key(item["code"])
            verdict = {"是": "模型建议纳入", "存疑": "模型存疑", "否": "模型建议排除"}.get(item["verdict"])
            if code not in rows or code in case_codes or not verdict:
                raise ValueError("归档初筛记录无法与唯一的快照公司匹配。")
            case_codes.add(code)
            review_rows.append(ScreenRow(
                rows[code], "归档", suggestion=verdict, reason=clean(item["reason"], 1200),
                evidence=tuple(clean(value, 140) for value in (item.get("products") or [])[:6]),
            ))
        order = {"模型建议纳入": 0, "模型存疑": 1, "模型建议排除": 2}
        review_rows.sort(key=lambda row: (order[row.suggestion], row.candidate.code))
        counts = Counter(row.suggestion for row in review_rows)
        expected = entry["stats"]
        scope, eligible = int(expected["pool"]), len(review_rows)
        if scope < eligible or eligible != int(expected["llm"]) or any(
            counts[k] != int(expected[key])
            for k, key in zip(order, ("yes", "doubt", "no"))
        ):
            raise ValueError(f"标的 {case_id} 的归档漏斗与明细数量不一致。")
        screening = Screening(
            tuple(review_rows), source, len(rows), scope, scope - eligible, version=version,
            snapshot_date=quote_day, as_of=ARCHIVE_DATE, archive=True,
            archive_counts={k: counts[k] for k in order},
            note=SPECIAL_CASES.get(case_id) or "复用交付包预计算模型建议；规则剔除数量取自归档，未重新运行模型。",
        )
        cases.append(ArchiveCase(
            case_id, clean(entry["label"], 200),
            tuple((clean(x["q"], 80), clean(x["a"], 1000)) for x in entry["profile"]["rows"]),
            screening,
        ))
    if not rows or not cases:
        raise ValueError("交付包未包含可读取的候选快照和归档案例。")
    return OPCPackage(
        Pool(source, tuple(rows.values()), "历史快照", quote_day, "该快照仅用于候选线索，不为估值表预填行情。"),
        tuple(cases), clean(metadata.get("report_date"), 20),
    )
