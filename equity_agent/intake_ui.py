from __future__ import annotations

import streamlit as st

from .financials import parse_financial_file, template_bytes
from .guidance import ROUTE_DOCUMENTS, ROUTE_GUIDE, ROUTE_MATERIAL, SCREEN_PRESETS, route_suggestions, tag_suggestions
from .market_method import parse_comparable_file, comparable_template_bytes
from .presentation import money, table
from .project import LISTED, ROUTES, Profile, Project
from .screening import (
    RULES, Candidate, Pool, Screening, candidate_template, clean, code_key,
    iso_date, read_candidate_csv, removed_by_rule, run_screen, split_terms,
)
from .ui_common import XLSX, counter_cards, go, key, nav_actions, page_intro, plain
from .workflow import available_records


def profile_page(project: Project) -> None:
    page_intro("profile", "明确估值对象、基准日和比较口径。优先填写主营产品与业务资料来源。", project)
    profile = project.profile
    if project.mode in {"archive", "demo"}:
        archive = project.mode == "archive"
        with st.container(border=True, key=key(project, "wb_panel_profile_ref")):
            st.subheader("归档标的画像" if archive else "合成标的画像")
            pairs = project.reference_profile if archive else (
                ("主营产品", profile.products), ("客户类型", profile.customers),
                ("商业模式", profile.business_model), ("容易误选", profile.difference),
            )
            table(["维度", "交付包原文" if archive else "示例情况"], [list(pair) for pair in pairs])
            plain(profile.source)
        nav_actions(("返回总览", "home"), ("查看候选筛选", "pool"))
        return
    with st.expander("按已有资料比较工作路径", expanded=True):
        material_options = list(ROUTE_MATERIAL.values())
        materials = st.multiselect(
            "目前能取得的资料", material_options,
            default=[item for item in project.route_materials if item in material_options],
            key=key(project, "route_materials"),
        )
        suggested = route_suggestions(materials)
        if suggested:
            plain("可优先核对 " + "、".join(suggested) + "。建议基于你勾选的资料。")
        else:
            st.caption("勾选已有资料，可看到对应的工作路径和下一步要核对的内容。")
        table(["路径", "需要的资料", "下一步"], [[route, ROUTE_MATERIAL[route], ROUTE_GUIDE[route]] for route in ROUTES])
    with st.form(key(project, "profile"), border=True):
        st.subheader("建档信息")
        cols = st.columns([1.45, 1])
        name = cols[0].text_input("标的名称", value=profile.name, max_chars=160, placeholder="例如：某工业软件公司")
        benchmark_text = cols[1].text_input("估值基准日", value=profile.benchmark, max_chars=10, placeholder="YYYY-MM-DD")
        route = st.selectbox("估值路径", ROUTES, index=ROUTES.index(profile.route))
        route_reason = st.text_input("路径选择依据", value=project.route_reason, max_chars=800,
                                     placeholder="简述已有证据，或说明为什么选择其他路径")
        st.subheader("业务画像")
        product = st.text_area("主营产品或服务", value=profile.products, max_chars=1000, height=82,
                               placeholder="写清产品解决的问题，多个产品可用顿号分隔")
        left, right = st.columns(2)
        customers = left.text_area("客户类型", value=profile.customers, max_chars=700, height=82,
                                   placeholder="主要买方、终端客户与服务市场")
        model = right.text_area("商业模式", value=profile.business_model, max_chars=700, height=82,
                                placeholder="自产、代工、代理、订阅、项目交付等")
        difference = st.text_input("容易误选的业务", value=profile.difference, max_chars=700,
                                   placeholder="例如：设备代理、通用办公软件")
        source = st.text_input("标的业务资料来源", value=profile.source, max_chars=1000,
                               placeholder="文件名、章节、页码或可回查的链接")
        saved = st.form_submit_button("保存并进入候选池", type="primary", width="stretch")
        if saved:
            benchmark = iso_date(benchmark_text)
            if benchmark_text.strip() and not benchmark:
                st.error("估值基准日请按 YYYY-MM-DD 填写，日期须有效。")
            elif not all((name.strip(), benchmark, product.strip(), source.strip())):
                st.error("请填写标的名称、估值基准日、主营产品和业务资料来源。")
            elif (route != profile.route or (suggested and route not in suggested)) and not route_reason.strip():
                st.error("切换路径或选择资料暂不支持的路径时，请补充选择依据。")
            else:
                project.save_profile(Profile(
                    name.strip(), benchmark.isoformat(), product.strip(), customers.strip(), model.strip(),
                    difference.strip(), source.strip(), route,
                ))
                if tuple(materials) != project.route_materials or route_reason.strip() != project.route_reason:
                    project.route_materials = tuple(materials)
                    project.route_reason = route_reason.strip()
                    project.invalidate()
                    project.note("保存路径依据", f"{route} · 建议路径 {'、'.join(suggested) or '待补资料'}",
                                 evidence=project.route_reason)
                go("pool")


def _route_checklist(project: Project) -> None:
    route = project.profile.route
    documents = project.route_documents.get(route, {})
    with st.form(key(project, f"documents_{route}"), border=True):
        st.subheader(route + " · 资料核对")
        plain(ROUTE_GUIDE[route])
        st.caption(f"已记录 {sum(bool(value) for value in documents.values())}/{len(ROUTE_DOCUMENTS[route])} 项，可将取证记录带入底稿。")
        values = {
            label: st.text_input(label, value=documents.get(label, ""), max_chars=900, placeholder="填写已核对的事实、未决项或原件位置").strip()
            for label in ROUTE_DOCUMENTS[route]
        }
        if st.form_submit_button("保存本路径取证", type="primary"):
            if values != documents:
                project.route_documents[route] = values
                project.invalidate()
                project.note("保存路径取证", f"{route} · 已记录 {sum(bool(v) for v in values.values())} 项",
                             evidence=values[ROUTE_DOCUMENTS[route][-1]])
            st.rerun()


def financial_upload(project: Project) -> None:
    with st.expander("标的年度财务", expanded=not bool(project.records)):
        st.caption("可以先做可比复核再补财务。每年一行，金额列须标单位；季度数据请整理为可核对的完整年度。")
        st.download_button("下载标的财务模板", template_bytes(), file_name="标的财务模板.xlsx", mime=XLSX, on_click="ignore")
        with st.form(key(project, "financials_upload"), border=False):
            file = st.file_uploader("选择标的财务表", type=["xlsx", "xlsm", "csv"])
            if st.form_submit_button("读取年度财务"):
                if not file:
                    st.error("请选择财务表。")
                else:
                    try:
                        records = parse_financial_file(file.name, file.getvalue())
                        project.records = records
                        project.financial_name = file.name
                        project.invalidate()
                        project.note("导入标的财务", f"{len(records)} 个年度记录", evidence=file.name)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        st.error("文件无法读取，请另存为无密码的 XLSX 或使用 CSV 模板。")
        if project.records:
            eligible = available_records(project)
            plain(f"{project.financial_name} · 已读取 {len(project.records)} 个年度")
            table(
                ["年度", "收入", "EBITDA", "净利润", "净债务", "账面权益"],
                [[row.year, *[money(row.metrics.get(field)) for field in
                              ("revenue", "ebitda", "net_income", "net_debt", "book_equity")]] for row in project.records],
            )
            st.caption("金额已换算为人民币亿元。")
            if len(eligible) < len(project.records):
                st.warning("晚于估值基准日的年度不会用于测算。报表在基准日前是否已披露，仍需人工核对。")
            with st.expander("查看原文件定位"):
                table(["年度", "来源", "字段位置"], [
                    [r.year, r.source, "；".join(f"{k}：{v}" for k, v in r.provenance.items())] for r in project.records
                ])


def _funnel(project: Project) -> None:
    screen = project.active_screen
    if not screen:
        return
    remaining = screen.scope - screen.hard_removed
    manual_only = project.rules.get("input") == "manual"
    if not manual_only:
        counter_cards([
            ("资料覆盖", screen.universe, "数据源公司数"),
            ("行业入池", screen.scope, "去重后的候选"),
            ("规则剔除", screen.hard_removed, "按首个命中的硬条件计"),
            ("进入复核", remaining, "含待核验；人工补录另计"),
        ])
    with st.container(border=True, key=key(project, "wb_panel_funnel")):
        st.subheader("筛选依据")
        plain(f"{screen.source} · {screen.version}")
        if screen.snapshot_date:
            st.caption(f"行情快照 {screen.snapshot_date} · 筛选基准 {screen.as_of}。")
        if screen.note:
            plain(screen.note)
        if screen.archive:
            counts = screen.archive_counts
            table(["归档建议", "公司数"], [[name, count] for name, count in counts.items()])
            st.caption("归档仅提供进入模型的明细，硬条件剔除数量按归档漏斗差值展示。")
        elif screen.hard_removed:
            counts = removed_by_rule(screen)
            table(["规则", "剔除公司数"], [[f"{code} · {RULES.get(code, '')}", count] for code, count in counts.items()])
            with st.expander("查看规则剔除明细"):
                table(["代码", "公司", "规则", "原因"], [
                    [r.candidate.code, r.candidate.name, r.rule, r.reason] for r in screen.rows if r.gate == "剔除"
                ][:80])
                if screen.hard_removed > 80:
                    st.caption("这里显示前 80 家。完整名单见 Excel 底稿。")


def _csv_source(project: Project) -> Pool | None:
    with st.form(key(project, "pool_csv"), border=False):
        file = st.file_uploader("上传候选业务资料 CSV", type=["csv"])
        if st.form_submit_button("读取候选资料"):
            if not file:
                st.error("请选择一份候选资料表。")
            else:
                try:
                    st.session_state["wb_pools"][project.id] = read_candidate_csv(file.name, file.getvalue())
                    st.rerun()
                except (ValueError, UnicodeError) as exc:
                    st.error(str(exc))
    st.download_button("下载候选资料模板", candidate_template(), file_name="候选业务资料模板.csv", mime="text/csv", on_click="ignore")
    return st.session_state["wb_pools"].get(project.id)


def _screen_form(project: Project, pool: Pool, source: str) -> None:
    rules = project.rules
    preset_options = list(SCREEN_PRESETS)
    default_preset = "自定义" if rules.get("最低上市月数", 24) != 24 else "常规初筛"
    saved_preset = rules.get("筛选方案", default_preset)
    preset = st.selectbox("筛选起点", preset_options, index=preset_options.index(saved_preset) if saved_preset in preset_options else 1,
                          key=key(project, f"preset_{source}"))
    default_months, use_case = SCREEN_PRESETS[preset]
    st.caption(use_case + " 上市月数可按项目修改。")
    tags_key = key(project, f"screen_tags_{source}")
    suggested_tags = tag_suggestions(pool, project.profile.products)
    with st.expander("查看智能体建议的行业"):
        if suggested_tags:
            table(["行业标签", "主营词命中公司数", "示例"], [list(row) for row in suggested_tags])
            st.caption("按标的主营词与资料库原文匹配。请核对标签覆盖的业务。")
            if st.button("把首个建议填入行业选择", key=key(project, f"tags_hint_{source}")):
                st.session_state[tags_key] = [suggested_tags[0][0]]
        else:
            st.caption("现有主营描述没有明确的标签线索。可用更短的检索词，或直接选择行业。")
    available_tags = pool.tags
    existing = st.session_state.get(tags_key, rules.get("行业标签", []))
    st.session_state[tags_key] = [tag for tag in existing if tag in available_tags]
    with st.form(key(project, f"screen_{source}_{preset}"), border=True):
        st.subheader("设置筛选范围")
        tags = st.multiselect("行业标签", available_tags, key=tags_key, placeholder="选择一个或多个行业")
        st.caption("所选标签按并集入池；自有资料不选标签时使用该表的全部公司。")
        cols = st.columns(2)
        terms = cols[0].text_input("主营检索词", value="、".join(rules.get("检索词", [])) or project.profile.products[:180],
                                   help="用逗号或顿号分隔。命中用于排列复核顺序。")
        differences = cols[1].text_input("差异提醒词", value="、".join(rules.get("差异词", [])) or project.profile.difference[:180],
                                         help="命中只提示，需结合业务占比人工判断。")
        a, b = st.columns([1, 1.3])
        months_saved = int(rules.get("最低上市月数", 24))
        month_value = months_saved if default_months is None or preset == rules.get("筛选方案") else default_months
        months = a.number_input("最低上市月数", min_value=0, max_value=120, step=1, value=month_value,
                                help="0 表示不要求最短交易历史。基准日后上市的公司仍剔除。")
        st_only = b.checkbox("剔除 ST / 退市标记", value=bool(rules.get("剔除 ST", True)))
        a_only = b.checkbox("剔除已知 B 股或非 A 股", value=bool(rules.get("限制 A 股", source == "opc")))
        if st.form_submit_button("预览筛选影响", type="primary", width="stretch"):
            st.session_state.pop(key(project, "screen_preview"), None)
            try:
                screen = run_screen(
                    pool, as_of=project.profile.benchmark, tags=tuple(tags), terms=split_terms(terms),
                    caution_terms=split_terms(differences), listing_months=months,
                    exclude_st=st_only, exclude_non_a=a_only, context=project.context,
                )
                chosen_rules = {
                    "input": source, "行业标签": tags, "检索词": list(split_terms(terms)),
                    "差异词": list(split_terms(differences)), "最低上市月数": months,
                    "剔除 ST": st_only, "限制 A 股": a_only, "筛选方案": preset,
                }
                st.session_state[key(project, "screen_preview")] = (pool, screen, chosen_rules)
            except ValueError as exc:
                st.error(str(exc))
    preview = st.session_state.get(key(project, "screen_preview"))
    if not preview or preview[0] is not pool or preview[1].context != project.context or preview[2]["筛选方案"] != preset:
        return
    _, screen, settings = preview
    previous = project.active_screen
    previous_names = {r.candidate.key: r.candidate.name for r in previous.rows if r.gate != "剔除"} if previous else {}
    upcoming = {r.candidate.key for r in screen.rows if r.gate != "剔除"}
    removed = [name for code, name in previous_names.items() if code not in upcoming]
    with st.container(border=True, key=key(project, "wb_panel_screen_preview")):
        st.subheader("本次预览")
        plain(f"{settings['筛选方案']} · 行业 {'、'.join(settings['行业标签']) or '整份自有资料'} · 上市至少 {settings['最低上市月数']} 个月")
        plain(f"{screen.scope} 家入池 · {screen.hard_removed} 家规则剔除 · {len(upcoming)} 家可复核。"
              + (f" 相比当前规则池新增 {len(upcoming - previous_names.keys())} 家、移出 {len(removed)} 家。" if previous else ""))
        if removed:
            plain("移出样本举例 " + "、".join(removed[:8]))
        st.caption("保存后会保留候选证据和筛查结果一致的原复核。改动参数后请重新预览。")
        if st.button("采用这组预览并生成候选池", key=key(project, "apply_screen")):
            project.save_screen(screen, settings)
            st.session_state.pop(key(project, "screen_preview"), None)
            st.rerun()


def _direct_entry(project: Project) -> None:
    with st.expander("导入可比底表或添加指定公司", expanded=not bool(project.roster())):
        st.caption(f"已有人选可直接提交复核。底表中的取价与财务会带入测算，修正与权重记在当前比率 {project.policy['method']}。")
        st.download_button("下载可比数据模板", comparable_template_bytes(), file_name="可比数据模板.xlsx",
                           mime=XLSX, on_click="ignore")
        with st.form(key(project, "peer_import"), border=False):
            file = st.file_uploader("上传可比数据底表", type=["xlsx", "xlsm", "csv"])
            if st.form_submit_button("导入公司和取价数据"):
                if not file:
                    st.error("请选择可比数据表。")
                else:
                    try:
                        frame = parse_comparable_file(file.name, file.getvalue())
                        if frame.empty:
                            raise ValueError("表中没有公司记录。")
                        if len(frame) > 500:
                            raise ValueError("可比数据底表最多 500 家；大范围资料请用候选 CSV。")
                        rows = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
                        project.save_peer_inputs(rows, file.name)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        st.error("文件无法读取，请用页面模板另存为 XLSX 或 CSV。")
        with st.form(key(project, "nominate"), border=False, clear_on_submit=True):
            a, b = st.columns(2)
            company = a.text_input("公司名称", max_chars=160)
            ticker = b.text_input("证券代码（可选）", max_chars=36)
            business = st.text_area("主营业务与商业模式", max_chars=2000, height=82)
            source = st.text_input("业务证据来源", max_chars=750, placeholder="例如：2025 年报第 18 页")
            if st.form_submit_button("加入待复核名单"):
                candidate = Candidate(code_key(ticker), clean(company, 160), business=clean(business), source=clean(source))
                if not candidate.name or not candidate.source or not candidate.business:
                    st.error("请填写公司名称、业务摘要和证据来源。")
                elif candidate.key in project.roster():
                    st.error("名单里已有该公司。可比数据可在估值测算中更新。")
                else:
                    project.manual[candidate.key] = candidate
                    project.invalidate()
                    project.note("人工提交候选", candidate.name, code=candidate.code, evidence=candidate.source)
                    st.rerun()


def pool_page(project: Project, opc_pool: Pool | None) -> None:
    page_intro("pool", "先限定行业与硬条件，再按业务线索排出需要逐家核对的公司。", project)
    if project.mode != "archive" and not project.ready_profile:
        st.info("请先保存标的名称、主营产品和估值基准日。")
        nav_actions(("回到标的画像", "profile"), ("进入三态复核", "review"), disabled=True)
        return
    if project.mode != "archive" and project.profile.route != LISTED:
        _route_checklist(project)
        nav_actions(("返回标的与口径", "profile"), ("查看工作底稿", "paper"))
        return
    if project.screen and not project.active_screen:
        st.warning("标的画像或基准日有变化，请重建候选池后重新复核。原记录保留在操作日志。")
    if project.mode == "real":
        choices = ["manual", "csv"] + (["opc"] if opc_pool else [])
        current = project.rules.get("input", "opc" if opc_pool else "manual")
        mode = st.radio(
            "候选来源", choices, index=choices.index(current) if current in choices else 0,
            format_func={"manual": "已有人选", "csv": "自有业务资料", "opc": "OPC 历史快照"}.get,
            horizontal=True, key=key(project, "pool_source"),
        )
        pool = None
        if mode == "csv":
            pool = _csv_source(project)
        elif mode == "opc":
            pool = opc_pool
        if pool:
            plain(f"{pool.name} · {len(pool.candidates):,} 家" + (f" · 快照 {pool.snapshot_date}" if pool.snapshot_date else ""))
            _screen_form(project, pool, mode)
        if mode == "manual":
            if project.screen and project.rules.get("input") != "manual":
                if st.button("改用人工提交名单", help="关闭本轮自动筛选；旧判断仍保留在操作记录。"):
                    project.save_screen(
                        Screening((), "人工提交", 0, 0, 0, version="人工名单", as_of=project.profile.benchmark, context=project.context),
                        {"input": "manual"},
                    )
                    st.rerun()
            _direct_entry(project)
        elif project.active_screen:
            _direct_entry(project)
    _funnel(project)
    if project.manual:
        st.caption(f"人工提交：{len(project.manual)} 家。与候选池按证券代码合并，重复公司只出现一次。")
    ready = any(row.gate != "剔除" for row in project.roster().values()) and (not project.screen or bool(project.active_screen))
    if not ready and project.mode != "archive":
        st.info("当前没有可复核候选。可调整行业范围、导入底表或提交指定公司。")
    nav_actions(("返回标的与口径", "profile"), ("进入三态复核", "review"), disabled=not ready and project.mode != "archive")
