import json
from pathlib import Path

import pytest

from core.company_meta import (
    FIELDS,
    SNAPSHOT_PATH,
    STATUS_DELISTED,
    STATUS_NORMAL,
    STATUS_ST,
    STATUS_STAR_ST,
    STATUSES,
    CompanyMeta,
    MetaSnapshotError,
    board_for,
    build_city_gazetteer,
    build_record,
    city_for,
    city_options,
    filter_companies,
    industry_for,
    is_a_share,
    load_snapshot,
    option_counts,
    status_for,
)

ROWS = [
    {
        "SECURITY_CODE": "600519",
        "SECURITY_NAME_ABBR": "贵州茅台",
        "SECURITY_TYPE": "上交所主板A股",
        "PROVINCE": "贵州",
        "REG_ADDRESS": "贵州省仁怀市茅台镇",
        "BOARD_NAME_LEVEL": "食品饮料-白酒-白酒Ⅲ",
        "INDUSTRYCSRC1": "制造业-酒、饮料和精制茶制造业",
    },
    {
        "SECURITY_CODE": "300750",
        "SECURITY_NAME_ABBR": "宁德时代",
        "SECURITY_TYPE": "深交所创业板A股",
        "PROVINCE": "福建",
        "REG_ADDRESS": "中国福建省宁德市蕉城区漳湾镇新港路2号",
        "BOARD_NAME_LEVEL": "电力设备-电池-锂电池",
        "INDUSTRYCSRC1": "制造业-电气机械和器材制造业",
    },
]


def meta(code="000001", name="平安银行", board="深交所主板", industry="银行",
         province="广东", city="深圳", status=STATUS_NORMAL) -> CompanyMeta:
    return CompanyMeta(code, name, board, industry, province, city, status)


def test_board_uses_code_prefix_not_risk_warning_type():
    # Eastmoney reports risk-warning names with their own board, which would hide
    # the real listing venue.
    assert board_for("600000", "上交所主板A股") == "上交所主板"
    assert board_for("688981", "上交所科创板A股") == "上交所科创板"
    assert board_for("000004", "深交所风险警示板A股") == "深交所主板"
    assert board_for("300001", "深交所创业板A股") == "创业板"
    assert board_for("920992", "北京证券交易所A股") == "北交所"
    assert board_for("430047", "北京证券交易所A股") == "北交所"


def test_board_falls_back_to_reported_market():
    assert board_for("999999", "上交所科创板A股") == "上交所科创板"
    assert board_for("999999", "北京证券交易所A股") == "北交所"
    assert board_for("999999", "未知市场A股") == "其他"


def test_status_splits_st_star_st_and_delisted():
    assert status_for("平安银行", "深交所主板A股") == STATUS_NORMAL
    assert status_for("ST星源", "深交所主板A股") == STATUS_ST
    assert status_for("SST天海", "") == STATUS_ST
    assert status_for("*ST高鸿", "深交所主板A股") == STATUS_STAR_ST
    assert status_for("S*ST前锋", "") == STATUS_STAR_ST
    # Names in the delisting process lose the ST prefix and end with 退.
    assert status_for("国华退", "深交所风险警示板A股") == STATUS_DELISTED
    assert status_for("退市环球", "上交所主板A股") == STATUS_DELISTED
    # PT is the legacy 特别转让 marker for delisted stocks.
    assert status_for("PT水仙", "上交所主板A股") == STATUS_DELISTED
    # A risk-warning board type without a name marker is still not normal.
    assert status_for("正常公司", "上交所风险警示板A股") == STATUS_ST
    # S-prefixed names that are not SST/S*ST are only 未股改, not risk warnings.
    assert status_for("S佳通", "上交所主板A股") == STATUS_NORMAL


def test_city_extraction_handles_province_prefix_and_direct_municipalities():
    assert city_for("贵州", "贵州省仁怀市茅台镇") == "仁怀"
    assert city_for("广东", "广东省深圳市福田区益田路5033号") == "深圳"
    assert city_for("北京", "北京市海淀区中关村") == "北京"
    assert city_for("上海", "上海市浦东新区世纪大道") == "上海"
    assert city_for("四川", "中国(四川)自由贸易试验区成都高新区万象南路399号") == "成都"
    assert city_for("广东", "广东省梅县华侨城香港花园") == ""


def test_city_extraction_recovers_addresses_without_shi_suffix():
    gazetteer = build_city_gazetteer(
        [
            {"PROVINCE": "江苏", "REG_ADDRESS": "江苏省苏州市工业园区星湖街"},
            {"PROVINCE": "辽宁", "REG_ADDRESS": "辽宁省沈阳市和平区"},
        ]
    )
    assert city_for("江苏", "江苏省徐州经济技术开发区驮蓝山路26号", gazetteer) == "徐州"
    assert city_for("辽宁", "沈阳经济技术开发区开发大路17甲1号", gazetteer) == "沈阳"
    assert city_for("河北", "石家庄高新区中山东路931号", gazetteer) == "石家庄"
    # A spelled-out 市 always wins over names found elsewhere in the dataset.
    clash = {"中山": "广东"}
    assert city_for("河北", "河北省石家庄市中山东路931号", clash) == "石家庄"


def test_a_share_scope_skips_b_shares_cdrs_neeq_and_pseudo_codes():
    def row(code, security_type):
        return {"SECURITY_CODE": code, "SECURITY_TYPE": security_type}

    assert is_a_share(row("600519", "上交所主板A股")) is True
    assert is_a_share(row("000004", "深交所风险警示板A股")) is True
    assert is_a_share(row("900901", "上交所主板B股")) is False
    assert is_a_share(row("689009", "上交所科创板中国存托凭证")) is False
    assert is_a_share(row("830001", "新三板三板股")) is False
    assert is_a_share(row("400001", "老三板三板股")) is False
    assert is_a_share(row("A04004", "深交所主板A股")) is False


def test_industry_prefers_eastmoney_level_one_and_folds_legacy_labels():
    assert industry_for("机械设备-通用设备-机床", "制造业-通用设备制造业") == "机械设备"
    assert industry_for(None, "金融业-货币金融服务") == "金融业"
    assert industry_for("", "") == "未分类"
    assert industry_for("电子设备-消费电子", "") == "电子"
    assert industry_for("信息技术-软件服务", "") == "计算机"


def test_build_record_combines_all_dimensions():
    record = build_record(ROWS[1])
    assert record == CompanyMeta(
        stock_code="300750",
        company_name="宁德时代",
        board="创业板",
        industry="电力设备",
        province="福建",
        city="宁德",
        status=STATUS_NORMAL,
    )
    assert record.as_company() == {"stock_code": "300750", "company_name": "宁德时代"}


def test_filter_combines_category_conditions():
    items = [
        meta("000001", "平安银行", industry="银行", city="深圳"),
        meta("600519", "贵州茅台", board="上交所主板", industry="食品饮料",
             province="贵州", city="仁怀"),
        meta("300750", "宁德时代", board="创业板", industry="电力设备",
             province="福建", city="宁德"),
        meta("000004", "国华退", industry="计算机", city="深圳", status=STATUS_DELISTED),
    ]
    assert [item.stock_code for item in filter_companies(items)] == [
        "000001", "600519", "300750", "000004"
    ]
    assert [item.stock_code for item in filter_companies(items, boards=["创业板"])] == ["300750"]
    assert [item.stock_code for item in filter_companies(items, provinces=["广东"])] == [
        "000001", "000004"
    ]
    assert [item.stock_code for item in filter_companies(items, cities=["深圳"])] == [
        "000001", "000004"
    ]
    assert [item.stock_code for item in filter_companies(items, industries=["银行", "计算机"])] == [
        "000001", "000004"
    ]
    assert [item.stock_code for item in filter_companies(items, statuses=[STATUS_DELISTED])] == [
        "000004"
    ]
    assert [
        item.stock_code
        for item in filter_companies(
            items, statuses=[STATUS_NORMAL, STATUS_STAR_ST], provinces=["广东"]
        )
    ] == ["000001"]
    assert [item.stock_code for item in filter_companies(items, query=" 茅台 ")] == ["600519"]
    assert [
        item.stock_code
        for item in filter_companies(
            items, provinces=["广东"], statuses=[STATUS_DELISTED], query="国华"
        )
    ] == ["000004"]
    assert filter_companies(items, provinces=["广东"], cities=["宁德"]) == []


def test_city_options_follow_selected_provinces():
    items = [meta(city="深圳"), meta("600519", province="贵州", city="仁怀")]
    assert city_options(items) == ["仁怀", "深圳"]
    assert city_options(items, ["广东"]) == ["深圳"]
    assert city_options(items, []) == ["仁怀", "深圳"]


def test_option_counts_orders_by_frequency():
    items = [meta(), meta("000002", city="深圳"), meta("600519", province="贵州", city="仁怀")]
    assert option_counts(items, "province") == {"广东": 2, "贵州": 1}
    assert option_counts(items, "city") == {"深圳": 2, "仁怀": 1}


def test_snapshot_is_missing_or_malformed(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(MetaSnapshotError) as exc:
        load_snapshot(missing)
    assert exc.value.code == "META_SNAPSHOT_MISSING"

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(MetaSnapshotError) as exc:
        load_snapshot(broken)
    assert exc.value.code == "META_SNAPSHOT_UNREADABLE"

    wrong_fields = tmp_path / "schema.json"
    wrong_fields.write_text(json.dumps({"fields": ["stock_code"], "items": []}), encoding="utf-8")
    with pytest.raises(MetaSnapshotError) as exc:
        load_snapshot(wrong_fields)
    assert exc.value.code == "META_SNAPSHOT_SCHEMA"

    short_row = tmp_path / "short.json"
    short_row.write_text(
        json.dumps({"fields": list(FIELDS), "items": [["000001"]]}), encoding="utf-8"
    )
    with pytest.raises(MetaSnapshotError) as exc:
        load_snapshot(short_row)
    assert exc.value.code == "META_SNAPSHOT_SCHEMA"


@pytest.mark.skipif(not SNAPSHOT_PATH.exists(), reason="metadata snapshot not generated")
def test_committed_snapshot_covers_the_a_share_market():
    items = load_snapshot()
    assert len(items) >= 5000
    codes = [item.stock_code for item in items]
    assert len(codes) == len(set(codes)), "duplicate stock codes in snapshot"
    assert all(code.isdigit() and len(code) == 6 for code in codes)
    assert {item.board for item in items} <= {
        "上交所主板", "深交所主板", "创业板", "上交所科创板", "北交所"
    }
    # A share names are unique enough that a mismatch here means the column
    # mapping drifted; spot-check two well-known issuers.
    by_code = {item.stock_code: item for item in items}
    assert by_code["600519"].company_name == "贵州茅台"
    assert by_code["600519"].city == "仁怀"
    assert by_code["300750"].board == "创业板"
    assert by_code["000001"].province == "广东"
    assert not [item for item in items if not item.company_name or not item.industry]
    with_city = sum(1 for item in items if item.city)
    assert with_city / len(items) > 0.95, f"only {with_city} of {len(items)} have a city"
    statuses = {item.status for item in items}
    assert statuses == set(STATUSES)
    counts = {status: sum(1 for item in items if item.status == status) for status in STATUSES}
    assert counts[STATUS_NORMAL] > 5000
    assert counts[STATUS_ST] > 100
    assert counts[STATUS_STAR_ST] > 100
    assert counts[STATUS_DELISTED] > 100
    assert by_code["000004"].status == STATUS_DELISTED
    assert by_code["000005"].status == STATUS_ST


def test_snapshot_file_is_small_enough_for_the_repository():
    if not SNAPSHOT_PATH.exists():
        pytest.skip("metadata snapshot not generated")
    size_kb = SNAPSHOT_PATH.stat().st_size / 1024
    assert size_kb < 1024, f"snapshot grew to {size_kb:.0f} KiB; check for duplicated rows"
    payload = json.loads(Path(SNAPSHOT_PATH).read_text(encoding="utf-8"))
    assert payload["fields"] == list(FIELDS)
    assert payload["count"] == len(payload["items"])
