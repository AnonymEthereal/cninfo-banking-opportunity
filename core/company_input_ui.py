"""Additive company selection interface."""
import re
import streamlit as st
from .company_directory import QUICK_COMPANIES, load_company_directory, search_companies, merge_companies
from .company_import_ui import render_excel_import


@st.cache_data(ttl=86400, show_spinner=False)
def cached_directory():
    return load_company_directory()


def render_company_input() -> dict | None:
    state = st.session_state
    if "selected_companies" not in state:
        state.selected_companies = []
    if "directory_attempted" not in state:
        state.directory_attempted = True
        try:
            with st.spinner("加载企业名录…"):
                state.company_directory = cached_directory()
            state.directory_error = False
        except Exception:
            state.company_directory = QUICK_COMPANIES
            state.directory_error = True
    directory = state.company_directory
    by_code = {item.stock_code: item for item in directory}

    def add(items):
        try:
            previous = len(state.selected_companies)
            state.selected_companies = merge_companies(state.selected_companies, items)
            count = len(state.selected_companies) - previous
            st.success(f"已添加 {count} 家企业；重复企业自动跳过。")
        except ValueError as exc:
            st.error(str(exc))

    def candidate_picker(label, matches, key):
        selected_codes = {item["stock_code"] for item in state.selected_companies}
        available = [item.stock_code for item in matches if item.stock_code not in selected_codes]
        all_option = "__all_visible__"

        def commit():
            choices = state.get(key, [])
            codes = available if all_option in choices else choices
            try:
                state.selected_companies = merge_companies(state.selected_companies, [by_code[code].dict() for code in codes])
                state.pop("picker_error", None)
            except ValueError as exc:
                state.picker_error = str(exc)
            state[key] = []

        st.multiselect(label, [all_option] + available if available else [],
                       format_func=lambda code: f"全选当前候选（{len(available)} 家）" if code == all_option else f"{by_code[code].company_name} · {code}",
                       key=key, on_change=commit, disabled=not available,
                       placeholder="选择即添加，可在下拉框全选")
        if not available and matches:
            st.caption("这些候选企业已全部加入名单。")

    st.subheader("01 · 建立关注名单")
    st.caption(f"可搜索 {len(directory):,} 家企业 · 多种方式可混合添加 · 企业数量不设固定上限")
    if state.directory_error:
        st.warning("完整名录暂时无法加载，当前使用常用企业列表。已有选择仍保留，可上传 Excel 或重试。")
    if st.button("刷新企业名录"):
        try:
            cached_directory.clear()
            state.company_directory = cached_directory()
            state.directory_error = False
            st.rerun()
        except Exception:
            st.error("刷新失败，请稍后重试。")
    add_col, selected_col = st.columns([3, 2])
    with add_col:
        search_tab, batch_tab, file_tab = st.tabs(["搜索添加", "批量粘贴", "Excel 导入"])
        with search_tab:
            query = st.text_input("搜索名称或股票代码", placeholder="如：招商、茅台、600、宁德时带")
            candidates = search_companies(query, directory)
            candidate_picker("匹配企业（选择即添加）", candidates, "search_candidates")
            st.caption("下拉框首项可全选当前候选；已选企业自动隐藏。模糊结果请核对名称与代码。")
        with batch_tab:
            text = st.text_area("粘贴企业名称或代码", placeholder="平安银行，招商\n600519", help="换行、空格、逗号或分号分隔。模糊名称逐项确认，不会自动猜选。")
            if st.button("识别候选企业", disabled=not text.strip()):
                tokens = list(dict.fromkeys(token for token in re.split(r"[\s,，;；、]+", text.strip()) if token))
                state.batch_tokens = tokens
            for index, token in enumerate(state.get("batch_tokens", [])):
                matches = search_companies(token, directory, limit=15)
                candidate_picker(token, matches, f"batch_match_{index}_{token}")
                if not matches:
                    st.caption("\u6ca1\u6709\u627e\u5230\u5019\u9009\uff0c\u53ef\u6362\u4e00\u4e2a\u5173\u952e\u8bcd\u3002")
            if state.get("picker_error"):
                st.error(state.picker_error)
        with file_tab:
            render_excel_import(directory)
    with selected_col:
        st.markdown("#### 已选企业")
        st.caption(f"已选 {len(state.selected_companies)} 家 · 各入口添加的企业汇总在这里")
        selected = {item["stock_code"]: item for item in state.selected_companies}
        remove = st.multiselect("选择要移除的企业", list(selected), format_func=lambda code: f"{selected[code]['company_name']} · {code}")
        left, right = st.columns(2)
        if left.button("移除所选", disabled=not remove):
            state.selected_companies = [item for item in state.selected_companies if item["stock_code"] not in remove]
            st.rerun()
        if right.button("清空名单", disabled=not selected):
            state.selected_companies = []
            st.rerun()
        if selected:
            st.dataframe([{"企业": item["company_name"], "代码": item["stock_code"]} for item in state.selected_companies], hide_index=True, use_container_width=True)
        else:
            st.info("从左侧添加第一家企业。添加后可继续搜索、粘贴或导入。")
    if not state.selected_companies:
        return None
    return {"valid": True, "companies": state.selected_companies, "errors": [], "summary": {"valid_rows": len(state.selected_companies)}}
