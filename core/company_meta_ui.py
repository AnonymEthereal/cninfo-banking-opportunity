"""Category filter tab: add companies by board, region, industry and status.

The tab reads the committed A-share snapshot through ``core.company_meta`` and
hands the matches to the shared ``add`` callback, so duplicates, validation and
the "已选企业" panel behave exactly like the other entry points.
"""
from __future__ import annotations

import streamlit as st

from .company_meta import (
    STATUS_NORMAL,
    STATUS_RISK,
    MetaSnapshotError,
    city_options,
    filter_companies,
    load_snapshot,
    option_counts,
)

BOARD_ORDER = ("上交所主板", "深交所主板", "创业板", "上交所科创板", "北交所", "其他")
STATUS_CHOICES = {"全部": (), "仅正常": (STATUS_NORMAL,), "仅风险警示": (STATUS_RISK,)}
TABLE_LIMIT = 400


@st.cache_data(ttl=86400, show_spinner=False)
def cached_meta():
    return load_snapshot()


def _ordered(values, preferred: tuple[str, ...]):
    known = [value for value in preferred if value in values]
    return known + [value for value in values if value not in preferred]


def render_category_filter(add) -> None:
    try:
        with st.spinner("加载分类元数据…"):
            items = cached_meta()
    except MetaSnapshotError as exc:
        st.warning(f"{exc.message}（{exc.code}）")
        st.caption("分类筛选暂不可用；上面的搜索、批量粘贴和 Excel 导入不受影响。")
        return

    provinces_all = sorted({item.province for item in items if item.province})
    board_counts = option_counts(items, "board")
    industry_counts = option_counts(items, "industry")
    province_counts = option_counts(items, "province")

    region_left, region_right = st.columns(2)
    with region_left:
        provinces = st.multiselect(
            "地区 · 省/直辖市",
            provinces_all,
            format_func=lambda value: f"{value}（{province_counts.get(value, 0):,}）",
            key="meta_provinces",
            placeholder="不限",
        )
    with region_right:
        cities_available = city_options(items, provinces)
        city_counts = option_counts(items, "city")
        cities = st.multiselect(
            "地区 · 城市",
            cities_available,
            format_func=lambda value: f"{value}（{city_counts.get(value, 0):,}）",
            # Changing the province resets the city choice instead of keeping a
            # stale value that is no longer among the options.
            key="meta_cities_" + "|".join(provinces),
            placeholder="不限" if provinces else "先选省份可缩小范围",
        )

    scope_left, scope_right = st.columns(2)
    with scope_left:
        boards = st.multiselect(
            "上市板块",
            _ordered(list(board_counts), BOARD_ORDER),
            format_func=lambda value: f"{value}（{board_counts[value]:,}）",
            key="meta_boards",
            placeholder="不限",
        )
    with scope_right:
        industries = st.multiselect(
            "所属行业",
            list(industry_counts),
            format_func=lambda value: f"{value}（{industry_counts[value]:,}）",
            key="meta_industries",
            placeholder="不限",
        )

    status_left, status_right = st.columns([2, 3])
    with status_left:
        status = st.segmented_control(
            "监管状态",
            list(STATUS_CHOICES),
            default="全部",
            key="meta_status",
        )
    with status_right:
        query = st.text_input(
            "在结果中筛选",
            placeholder="名称或代码，如 银行、600",
            key="meta_query",
        )

    matched = filter_companies(
        items,
        boards=boards,
        provinces=provinces,
        cities=cities,
        industries=industries,
        statuses=STATUS_CHOICES.get(status or "全部", ()),
        query=query,
    )
    st.caption(
        f"命中 {len(matched):,} 家 / 共 {len(items):,} 家 A 股。"
        "按板块、地区、行业组合筛选后可全选添加，也可在表格中勾选部分企业。"
    )
    if len(matched) > 1000:
        st.caption("选择范围较大，后续检索与解析耗时会更长；建议再缩小地区或行业。")
    if not matched:
        st.info("当前条件下没有企业。试试放宽筛选，或清空关键词。")
        return

    shown = matched[:TABLE_LIMIT]
    frame = [
        {
            "代码": item.stock_code,
            "企业": item.company_name,
            "板块": item.board,
            "行业": item.industry,
            "省份": item.province,
            "城市": item.city or "—",
            "状态": item.status,
        }
        for item in shown
    ]
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        height=340,
        on_select="rerun",
        selection_mode="multi-row",
        key="meta_table",
    )
    picked = [shown[index] for index in event.selection.rows if index < len(shown)]
    if len(matched) > len(shown):
        st.caption(f"表格只显示前 {len(shown):,} 家，其余请用下方“添加命中企业”或继续缩小条件。")

    action_left, action_right = st.columns(2)
    with action_left:
        add_all = st.button(
            f"添加全部命中（{len(matched):,} 家）",
            disabled=not matched,
            width="stretch",
            key="meta_add_all",
        )
    with action_right:
        add_picked = st.button(
            f"添加勾选（{len(picked):,} 家）",
            disabled=not picked,
            width="stretch",
            key="meta_add_picked",
        )
    if add_all:
        add([item.as_company() for item in matched])
    elif add_picked:
        add([item.as_company() for item in picked])
