from .financials import FinancialRecord


def demo_records() -> list[FinancialRecord]:
    """Synthetic software company case used only for demonstration."""
    values = [
        ("2023", 8.0, 0.8, 0.4, 0.7, 0.3, 1.5),
        ("2024", 10.0, 1.2, 0.6, 0.9, 0.35, 1.5),
        ("2025", 13.0, 1.8, 0.9, 1.3, 0.5, 1.5),
    ]
    fields = ("revenue", "ebitda", "net_income", "operating_cash_flow", "capex", "net_debt")
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
