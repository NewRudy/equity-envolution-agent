"""Per-project review decisions, calculation inputs, and a portable working record."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from secrets import token_hex
from typing import Any

from .financials import FinancialRecord
from .screening import (
    DIMENSION_CHOICES, REVIEW_DIMENSIONS, Candidate, DECISIONS, ScreenRow, Screening, clean, code_key, identity, iso_date,
)

LISTED = "上市公司比较法"
ROUTES = (LISTED, "交易案例比较法", "最近融资价格参考", "穿透到经营资产")
METHODS = ("EV/EBITDA", "P/B", "P/E")
BOOK_BASES = ("合并报表归母权益", "单体报表股东权益")
EARNINGS_BASES = ("归母净利润", "扣非归母净利润")
AGGREGATIONS = {"equal": "等权均值", "median": "中位数", "weighted": "自定义加权"}
PRICE_POLICIES = {"reference": "按已记录依据核对", "exact": "仅基准日", "recent": "限基准日前若干日"}
VALUE_DRIVERS = {
    "": "尚待判断",
    "EV/EBITDA": "经营盈利与融资结构",
    "P/B": "资产质量与资本占用",
    "P/E": "归母盈利与盈利质量",
}
PEER_PARAMETERS = ("adjustment_factor", "adjustment_reason", "weight_pct")
POLICY_RANGES = {
    "dlom_pct": (0, 80), "growth_pct": (-50, 150), "fcf_pct": (-50, 90),
    "wacc_pct": (1, 50), "terminal_pct": (0, 15), "years": (3, 10), "price_lookback": (1, 90),
}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


@dataclass(frozen=True)
class Profile:
    name: str = ""
    benchmark: str = ""
    products: str = ""
    customers: str = ""
    business_model: str = ""
    difference: str = ""
    source: str = ""
    route: str = LISTED


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    reviewer: str
    at: str
    basis: str
    evidence: str = ""
    dimensions: dict[str, str] = field(default_factory=dict)


def valuation_policy() -> dict[str, Any]:
    return {
        "method": "EV/EBITDA", "method_reason": "", "book_equity_basis": "", "earnings_basis": "",
        "target_year": "", "value_driver": "", "aggregation": "weighted", "aggregation_reason": "",
        "price_policy": "reference", "price_lookback": 5,
        "dlom_pct": 0.0, "dlom_reason": "", "dlom_source": "", "bridge_adjustment": 0.0,
        "bridge_reason": "", "bridge_source": "", "include_dcf": False, "growth_pct": 15.0,
        "fcf_pct": 8.0, "wacc_pct": 12.0, "terminal_pct": 3.0, "years": 5,
    }


@dataclass
class Project:
    profile: Profile = field(default_factory=Profile)
    mode: str = "real"
    id: str = field(default_factory=lambda: token_hex(6))
    reference_profile: tuple[tuple[str, str], ...] = ()
    screen: Screening | None = None
    rules: dict[str, Any] = field(default_factory=dict)
    manual: dict[str, Candidate] = field(default_factory=dict)
    peers: dict[str, dict] = field(default_factory=dict)
    reviews: dict[str, Decision] = field(default_factory=dict)
    model_advice: dict[str, dict] = field(default_factory=dict)
    records: list[FinancialRecord] = field(default_factory=list)
    financial_name: str = ""
    reviewer: str = ""
    route_materials: tuple[str, ...] = ()
    route_reason: str = ""
    route_documents: dict[str, dict[str, str]] = field(default_factory=dict)
    sample_sets: dict[str, list[str]] = field(default_factory=dict)
    sample_reasons: dict[str, str] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=valuation_policy)
    audit: list[dict] = field(default_factory=list)
    result: dict | None = None
    revision: int = 0
    profile_stamp: str = ""
    created_at: str = field(default_factory=stamp)

    @property
    def context(self) -> str:
        fields = asdict(self.profile)
        return digest([fields, self.profile_stamp] if self.profile_stamp else fields)

    @property
    def active_screen(self) -> Screening | None:
        if self.screen and (self.screen.archive or self.screen.context == self.context):
            return self.screen
        return None

    @property
    def ready_profile(self) -> bool:
        return bool(self.profile.name and self.profile.products and iso_date(self.profile.benchmark))

    def invalidate(self) -> None:
        self.result = None
        self.revision += 1

    def note(self, action: str, detail: str, *, actor: str = "", code: str = "", evidence: str = "") -> None:
        self.audit.append({
            "at": stamp(), "action": action, "detail": detail, "actor": actor, "code": code, "evidence": evidence,
        })

    def save_profile(self, profile: Profile) -> None:
        if profile != self.profile:
            self.note("修改标的与口径", f"{self.profile.name or '新项目'} → {profile.name} · {profile.benchmark or '基准日待填'} · {profile.route}",
                      evidence=profile.source)
            self.profile = profile
            self.profile_stamp = token_hex(6)
            self.invalidate()

    def save_screen(self, screen: Screening, rules: dict) -> None:
        if self.active_screen == screen and {k: v for k, v in self.rules.items() if k != "run"} == rules:
            return
        before = self.roster()
        confirmed = {k: decision for k, row in before.items() if (decision := self.decision(row))}
        self.screen = screen
        self.rules = rules | {"run": token_hex(6)}
        kept = 0
        for k, row in self.roster().items():
            if k in confirmed and row == before[k]:
                self.reviews[k] = replace(confirmed[k], basis=self.decision_basis(row))
                kept += 1
        self.invalidate()
        scope = "；".join(f"{k}：{'、'.join(map(str, v)) if isinstance(v, (list, tuple)) else v}"
                          for k, v in rules.items() if k != "input")
        self.note("生成候选池", f"{screen.version} · 行业入池 {screen.scope} 家 · 规则剔除 {screen.hard_removed} 家" +
                  (f" · 保留 {kept} 条原证据一致的复核" if confirmed else "") +
                  (f" · {scope}" if scope else ""), evidence=screen.source)

    def roster(self) -> dict[str, ScreenRow]:
        rows = {row.candidate.key: row for row in self.active_screen.rows} if self.active_screen else {}
        for key, candidate in self.manual.items():
            if key not in rows:
                rows[key] = ScreenRow(candidate, "待核验", suggestion="人工提交", reason="人工录入或导入，请核对业务与交易条件。")
        return rows

    def decision_basis(self, row: ScreenRow) -> str:
        return digest([self.context, self.rules.get("run"), asdict(row)])

    def decision(self, row: ScreenRow) -> Decision | None:
        stored = self.reviews.get(row.candidate.key)
        if row.gate != "剔除" and stored and stored.basis == self.decision_basis(row):
            return stored
        return None

    def decide(
        self, key: str, state: str, reason: str, reviewer: str, evidence: str = "", dimensions: dict | None = None,
    ) -> None:
        row = self.roster().get(key)
        reason, reviewer = clean(reason, 1500), clean(reviewer, 80)
        if not row or row.gate == "剔除" or state not in (*DECISIONS, "待复核"):
            raise ValueError("该公司当前不在可复核名单内。")
        if not reviewer or not reason:
            raise ValueError("请填写复核人和本次判断的依据。")
        previous = self.decision(row)
        self.reviewer = reviewer
        evidence = clean(evidence, 800)
        dimensions = _review_dimensions(dimensions or {})
        self.reviews[key] = Decision(state, reason, reviewer, stamp(), self.decision_basis(row), evidence, dimensions)
        self.invalidate()
        detail = f"{row.candidate.name} · {previous.state if previous else '待复核'} → {state}：{reason}"
        if dimensions:
            detail += " · " + " / ".join(f"{name} {assessment}" for name, assessment in dimensions.items())
        self.note("三态复核", detail, actor=reviewer, code=row.candidate.code, evidence=evidence or row.candidate.source)

    def adopted(self) -> dict[str, dict]:
        output = {}
        for key, row in self.roster().items():
            decision = self.decision(row)
            if not decision or decision.state != "采纳":
                continue
            output[key] = dict(self.peers.get(key, {})) | {
                "selected": True, "company": row.candidate.name, "ticker": row.candidate.code,
                "selection_reason": decision.reason,
            }
        return output

    def selected(self, method: str | None = None) -> list[dict]:
        adopted = self.adopted()
        ratio = method or self.policy["method"]
        keys = self.sample_sets.get(method, adopted)
        return [adopted[k] | self.peer_parameters(k, ratio) for k in keys if k in adopted]

    def peer_parameters(self, key: str, method: str) -> dict:
        defaults = {"adjustment_factor": 1.0, "adjustment_reason": "", "weight_pct": None}
        row = self.peers.get(key, {})
        if "method_parameters" not in row:
            return defaults | {name: row[name] for name in PEER_PARAMETERS if name in row}
        return defaults | row["method_parameters"].get(method, {})

    def store_peer_values(self, key: str, method: str, row: dict) -> None:
        saved = dict(self.peers.get(key, {}).get("method_parameters", {}))
        saved[method] = {name: row.get(name) for name in PEER_PARAMETERS}
        self.peers[key] = {name: value for name, value in row.items() if name not in PEER_PARAMETERS} | {
            "method_parameters": saved,
        }

    def save_samples(self, method: str, keys: list[str], reason: str = "") -> None:
        adopted = self.adopted()
        if method not in METHODS or len(keys) != len(set(keys)) or any(k not in adopted for k in keys):
            raise ValueError("本法样本必须来自当前已采纳公司，且不能重复。")
        reason = clean(reason, 1000)
        if self.sample_sets.get(method) != keys or self.sample_reasons.get(method, "") != reason:
            self.sample_sets[method] = list(keys)
            self.sample_reasons[method] = reason
            self.invalidate()
            self.note("保存本法样本", f"{method} · " + ("、".join(adopted[k]["company"] for k in keys) or "未选样本"),
                      actor=self.reviewer, evidence=reason)

    def save_peer_inputs(self, rows: list[dict], source: str) -> None:
        incoming = {}
        for row in rows:
            name = clean(row.get("company"), 200)
            code = code_key(row.get("ticker"))
            key = identity(code, name)
            if not name or key in incoming:
                raise ValueError("可比表中公司名称为空或有重复公司，请合并后再导入。")
            incoming[key] = dict(row, company=name, ticker=code, selected=False)
        current = self.roster()
        existing_by_name = {identity(None, row.candidate.name): key for key, row in current.items()}
        resolved = {}
        for key, row in incoming.items():
            name_key = identity(None, row["company"])
            prior = existing_by_name.get(name_key)
            # A missing code can be resolved by an exact name only when that name is unique.
            if not row["ticker"] and prior:
                matches = [k for k, value in current.items() if identity(None, value.candidate.name) == name_key]
                if len(matches) != 1:
                    raise ValueError(f"{row['company']} 对应多个代码，请在表中补充代码。")
                key = prior
                row["ticker"] = current[key].candidate.code
            if key in resolved:
                raise ValueError(f"{row['company']} 与同一证券的另一行重复，请合并后再导入。")
            resolved[key] = row
        for key, row in resolved.items():
            self.store_peer_values(key, self.policy["method"], row)
            if key not in current:
                self.manual[key] = Candidate(
                    row["ticker"], row["company"], source=clean(row.get("source_location")) or source,
                )
        self.invalidate()
        self.note("导入可比数据", f"写入 {len(rows)} 家的输入；导入不产生采纳结论。", evidence=source)


def dump_project(project: Project) -> bytes:
    # A workpaper exports its calculation; the portable draft always reopens without a prior sign-off.
    return json.dumps({
        "schema": "equity-workbench/1",
        **{f.name: getattr(project, f.name) for f in fields(project) if f.name not in {"id", "result", "revision"}},
    }, default=asdict, ensure_ascii=False, allow_nan=False, indent=None).encode("utf-8")


def restore_project(data: bytes) -> Project:
    if len(data) > 35 * 1024 * 1024:
        raise ValueError("项目草稿超过大小限制。")
    try:
        raw = json.loads(data, parse_constant=_invalid_json_number)
        if not isinstance(raw, dict) or raw.get("schema") != "equity-workbench/1" or raw.get("mode") not in {"real", "demo", "archive"}:
            raise ValueError("不支持的项目草稿版本。")
        profile = Profile(**{f.name: clean(raw["profile"].get(f.name), 1500) for f in fields(Profile)})
        if profile.route not in ROUTES or (profile.benchmark and not iso_date(profile.benchmark)):
            raise ValueError("草稿中的估值路径或基准日无效。")
        p = Project(profile, mode=raw["mode"])
        p.profile_stamp = clean(raw.get("profile_stamp"), 24)
        p.reference_profile = tuple((clean(k, 80), clean(v, 1200)) for k, v in raw.get("reference_profile", []))
        p.rules = _dict(raw.get("rules"))
        manual = [_candidate(v) for v in _dict(raw.get("manual")).values()]
        p.manual = {c.key: c for c in manual}
        p.peers = {clean(k, 200): _dict(v) for k, v in _dict(raw.get("peers")).items()}
        p.reviews = {
            clean(k, 200): Decision(
                **{f.name: clean(v.get(f.name), 1500) for f in fields(Decision) if f.name != "dimensions"},
                dimensions=_review_dimensions(_dict(v.get("dimensions", {}))),
            )
            for k, v in _dict(raw.get("reviews")).items() if v.get("state") in (*DECISIONS, "待复核")
        }
        p.model_advice = {
            clean(k, 200): {f: clean(v.get(f), 800) for f in ("verdict", "reason", "model", "at", "basis")}
            | {"evidence": [clean(q, 120) for q in v.get("evidence", [])[:3]]}
            for k, v in _dict(raw.get("model_advice", {})).items()
        }
        p.records = [
            FinancialRecord(clean(r["year"], 4), _dict(r["metrics"]), clean(r["source"]), _dict(r["provenance"]))
            for r in raw.get("records", [])
        ]
        for record in p.records:
            if not record.year.isdecimal() or len(record.year) != 4 or any(
                value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)))
                for value in record.metrics.values()
            ) or not all(isinstance(value, str) for value in record.provenance.values()):
                raise ValueError("草稿中的财务记录无效。")
        p.financial_name = clean(raw.get("financial_name"), 250)
        p.reviewer = clean(raw.get("reviewer"), 80)
        p.route_materials = tuple(clean(v, 100) for v in raw.get("route_materials", [])[:12])
        p.route_reason = clean(raw.get("route_reason"), 1500)
        p.route_documents = {
            k: {clean(label, 100): clean(value, 1500) for label, value in _dict(v).items()}
            for k, v in _dict(raw.get("route_documents", {})).items() if k in ROUTES
        }
        p.sample_sets = {
            k: list(dict.fromkeys(clean(x, 200) for x in v))
            for k, v in _dict(raw.get("sample_sets", {})).items() if k in METHODS and isinstance(v, list)
        }
        p.sample_reasons = {k: clean(v, 1000) for k, v in _dict(raw.get("sample_reasons", {})).items() if k in METHODS}
        p.audit = [{k: clean(item.get(k)) for k in ("at", "action", "detail", "actor", "code", "evidence")} for item in raw.get("audit", [])]
        p.created_at = clean(raw.get("created_at"), 40) or stamp()
        policy = _dict(raw.get("policy"))
        for key, default in valuation_policy().items():
            value = policy.get(key)
            if isinstance(value, type(default)) and not (isinstance(value, bool) and not isinstance(default, bool)):
                p.policy[key] = value
        if p.policy["method"] not in METHODS or p.policy["value_driver"] not in VALUE_DRIVERS or p.policy["aggregation"] not in AGGREGATIONS or p.policy["price_policy"] not in PRICE_POLICIES or any(
            not low <= p.policy[k] <= high for k, (low, high) in POLICY_RANGES.items()
        ) or not math.isfinite(p.policy["bridge_adjustment"]):
            raise ValueError("草稿中的估值方法或参数超出有效范围。")
        for key, row in list(p.peers.items()):
            if "method_parameters" not in row:
                p.store_peer_values(key, p.policy["method"], row)
            else:
                row["method_parameters"] = {
                    method: {name: _dict(values).get(name) for name in PEER_PARAMETERS}
                    for method, values in _dict(row["method_parameters"]).items() if method in METHODS
                }
        if raw.get("screen"):
            screen = _dict(raw["screen"])
            rows = []
            for item in screen.pop("rows"):
                row = ScreenRow(
                    _candidate(item["candidate"]), clean(item["gate"], 20), clean(item["rule"], 15),
                    clean(item["suggestion"], 60), clean(item["reason"]), tuple(clean(x) for x in item["evidence"]),
                )
                if row.gate not in {"归档", "通过", "待核验", "剔除"}:
                    raise ValueError("草稿中的候选状态无效。")
                rows.append(row)
            if len(rows) > 10_000 or len({row.candidate.key for row in rows}) != len(rows):
                raise ValueError("草稿中的候选名单过长或存在重复公司。")
            p.screen = Screening(rows=tuple(rows), **screen)
            if any(not isinstance(n, int) or n < 0 for n in (p.screen.universe, p.screen.scope, p.screen.hard_removed)):
                raise ValueError("草稿中的筛选统计无效。")
            if not 0 <= p.screen.hard_removed <= p.screen.scope <= p.screen.universe:
                raise ValueError("草稿中的筛选统计无效。")
            if bool(p.screen.archive) != (p.mode == "archive") or not isinstance(p.screen.archive_counts, dict):
                raise ValueError("草稿中的归档状态无效。")
        p.note("恢复项目草稿", "从本地草稿载入。估值确认和生成结果需重新进行。")
        return p
    except (AttributeError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError("该文件不是可读取的工作台项目草稿。") from exc


def _candidate(raw: dict) -> Candidate:
    values = {f.name: clean(raw.get(f.name)) for f in fields(Candidate) if f.name not in {"tags", "products"}}
    return Candidate(**values, tags=tuple(map(clean, raw.get("tags", []))), products=tuple(map(clean, raw.get("products", []))))


def _dict(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("项目草稿中的数据结构无效。")
    return dict(value)


def _review_dimensions(values: dict) -> dict[str, str]:
    return {
        name: values[name] for name in REVIEW_DIMENSIONS
        if values.get(name) in DIMENSION_CHOICES[1:]
    }


def _invalid_json_number(value: str) -> None:
    raise ValueError(f"项目草稿含无效数字：{value}。")
