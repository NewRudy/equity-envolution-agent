from .financials import FinancialRecord


def demo_records() -> list[FinancialRecord]:
    """Synthetic software company case used only for demonstration."""
    values = [
        ("2023", 8.0, 0.8, 0.4, 0.7, 0.3, 1.5, 4.0),
        ("2024", 10.0, 1.2, 0.6, 0.9, 0.35, 1.5, 4.8),
        ("2025", 13.0, 1.8, 0.9, 1.3, 0.5, 1.5, 5.5),
    ]
    fields = ("revenue", "ebitda", "net_income", "operating_cash_flow", "capex", "net_debt", "book_equity")
    records = []
    for year, *numbers in values:
        records.append(
            FinancialRecord(
                year=year,
                metrics=dict(zip(fields, numbers)),
                source="合成演示数据（非真实企业）",
                provenance={field: "演示样例" for field in fields},
            )
        )
    return records


def demo_comparables() -> list[dict]:
    """A complete, explicitly fictional peer set for the one-click product walkthrough."""
    peers = []
    for name, cap, debt, ebitda, equity in (
        ("示例同业 A", 18.0, 2.0, 2.0, 7.0),
        ("示例同业 B", 24.0, 1.2, 2.4, 8.0),
        ("示例同业 C", 15.5, 0.7, 1.8, 5.5),
        ("示例同业 D", 28.0, 2.0, 2.5, 9.0),
    ):
        peers.append({
            "selected": True,
            "company": name,
            "ticker": "虚构",
            "period": "2025 年年报（合成）",
            "price_basis": "示例基准日股权市值（合成）",
            "market_cap": cap,
            "net_debt": debt,
            "other_ev_adjustment": 0.0,
            "ev_adjustment_reason": "合成示例无其他融资项目",
            "ebitda": ebitda,
            "book_equity": equity,
            "book_equity_basis": "合并报表归母权益（合成）",
            "source": "合成演示数据；不对应真实公司、公告或市场行情",
            "source_location": "程序内合成示例",
            "selection_reason": "合成场景预设业务与规模相近，用于展示操作流程",
            "adjustment_factor": 1.0,
            "weight_pct": 25.0,
            "adjustment_reason": "合成示例暂不修正倍数",
        })
    return peers
