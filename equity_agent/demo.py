from .financials import FinancialRecord
from .project import Profile, Project
from .screening import Candidate, Pool, run_screen


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
    for index, (name, cap, debt, ebitda, equity) in enumerate((
        ("示例同业 A", 18.0, 2.0, 2.0, 7.0),
        ("示例同业 B", 24.0, 1.2, 2.4, 8.0),
        ("示例同业 C", 15.5, 0.7, 1.8, 5.5),
        ("示例同业 D", 28.0, 2.0, 2.5, 9.0),
    ), start=1):
        peers.append({
            "selected": True,
            "company": name,
            "ticker": f"DEMO-{index:02d}",
            "period": "2025 年年报（合成）",
            "report_end": "2025-12-31",
            "pricing_date": "2026-11-04",
            "price_basis": "基准日收盘股权市值（合成）",
            "market_cap": cap,
            "net_debt": debt,
            "other_ev_adjustment": 0.0,
            "ev_adjustment_reason": "合成示例无其他融资项目",
            "ebitda": ebitda,
            "book_equity": equity,
            "book_equity_basis": "合并报表归母权益",
            "net_income": round(ebitda * 0.45, 2),
            "earnings_basis": "归母净利润",
            "source": "合成演示数据；不对应真实公司、公告或市场行情",
            "source_location": "程序内合成示例",
            "selection_reason": "合成场景预设业务与规模相近，用于展示操作流程",
            "adjustment_factor": 1.0,
            "weight_pct": 25.0,
            "adjustment_reason": "合成示例暂不修正倍数",
        })
    return peers


def demo_project() -> Project:
    project = Project(Profile(
        "示例科技 · 工业软件", "2026-11-04", "工厂排产软件、生产执行系统",
        "中型离散制造工厂；客户为生产计划和数字化部门。",
        "自研软件按年订阅，配套部署与维护。",
        "设备代理、通用办公软件和以一次性交付为主的外包服务。",
        "本工作台内置虚构情境。",
    ), mode="demo")
    peers = demo_comparables()
    lines = [
        ("DEMO-01", "示例同业 A", "向离散制造工厂提供自研排产软件，以年度许可续费为主。", "排产软件", "2014-06-19"),
        ("DEMO-02", "示例同业 B", "主营生产执行系统，客户为电子与装配工厂，按站点订阅。", "生产执行系统", "2018-02-20"),
        ("DEMO-03", "示例同业 C", "工厂排产软件、良率分析和配套实施，客户集中在汽车零部件。", "排产软件", "2020-09-01"),
        ("DEMO-04", "示例同业 D", "离散工厂生产执行系统，软件订阅与运维收费。", "生产执行系统", "2019-04-18"),
        ("DEMO-05", "示例设备代理 E", "销售自动化设备，排产软件为采购设备的附带模块。", "设备代理", "2016-11-04"),
        ("DEMO-06", "示例工业服务 F", "经营智能制造服务，尚无按产品拆分的收入资料。", "智能制造服务", "2019-12-13"),
        ("DEMO-07", "示例软件 G", "自研排产软件，服务离散工厂。", "排产软件", "2026-03-06"),
        ("DEMO-08", "示例通用软件 H", "主营办公文档及企业即时通信，按用户席位收费。", "通用办公软件", "2015-08-20"),
    ]
    candidates = tuple(Candidate(
        code, name, ("工业软件",), "示例市场", listed, business, (product,), period="2025-12-31",
        source="内置合成样例 · 虚构业务摘要",
    ) for code, name, business, product, listed in lines)
    screen = run_screen(
        Pool("工业软件合成候选库", candidates), as_of=project.profile.benchmark, tags=("工业软件",),
        terms=("排产软件", "生产执行系统"), caution_terms=("设备代理", "通用办公软件"), context=project.context,
    )
    project.save_screen(screen, {
        "来源": "内置合成候选库", "行业标签": ["工业软件"], "检索词": ["排产软件", "生产执行系统"],
        "差异词": ["设备代理", "通用办公软件"], "最低上市月数": 24, "剔除 ST": True, "限制 A 股": True,
    })
    project.save_peer_inputs(peers, "合成估值参数")
    for row in peers:
        project.decide(row["ticker"], "采纳", "演示预设：产品、工厂客户和收费模式与标的相近，规模仍需核对。", "演示预设")
    project.decide("DEMO-05", "剔除", "演示预设：主营收入来自设备代理，软件是附带模块。", "演示预设")
    project.decide("DEMO-06", "存疑", "演示预设：缺少主营产品收入拆分，暂无法确认订阅业务。", "演示预设")
    project.records = demo_records()
    project.financial_name = "内置合成年报"
    project.policy.update(
        method_reason="演示预设：四家公司均有可比的经营利润和净债务口径。",
        book_equity_basis="合并报表归母权益", earnings_basis="归母净利润",
    )
    return project
