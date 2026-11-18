from core.dedup import (
    dedupe_events,
    dedupe_opportunities,
    link_related_disclosures,
    normalize_text,
)
from core.models import Event, Evidence


def make_event(event_id, summary, texts, worth=True):
    return Event(event_id=event_id, summary=summary, evidence=[Evidence(text=text) for text in texts], worth_analyzing=worth)


def test_merges_near_duplicate_summaries():
    first = make_event("e1", "本期债券实际发行规模20亿元，最终票面利率1.67%。", ["本期债券实际发行规模为20亿元。"])
    second = make_event("e2", "本期债券实际发行规模20亿元，最终票面利率1.67%。", ["最终票面利率为1.67%。"])
    kept = dedupe_events([first, second])
    assert [event.event_id for event in kept] == ["e1"]


def test_merges_events_sharing_evidence():
    quote = "北神投资通过北京产权交易所公开挂牌转让所持北交新能20.0249%股权。"
    first = make_event("e1", "转让北交新能股权，挂牌底价7064万元。", [quote])
    second = make_event("e2", "北交新能股权转让完成，成交价6508.09万元。", [quote])
    kept = dedupe_events([first, second])
    assert len(kept) == 1


def test_keeps_distinct_events():
    first = make_event("e1", "公司发行可续期公司债券20亿元。", ["本期债券实际发行规模为20亿元。"])
    second = make_event("e2", "公司获得银行授信总额3602.96亿元。", ["公司共获得各家银行授信总额3,602.96亿元。"])
    kept = dedupe_events([first, second])
    assert [event.event_id for event in kept] == ["e1", "e2"]


def test_duplicate_keeps_worth_analyzing_and_richer_evidence():
    first = make_event("e1", "公司注销股票期权3.5805万份。", ["公司完成注销股票期权。"], worth=False)
    second = make_event("e2", "公司注销股票期权3.5805万份。", ["公司完成注销3.5805万份股票期权。", "本次注销不影响公司股本。"], worth=True)
    kept = dedupe_events([first, second])
    assert len(kept) == 1
    assert kept[0].worth_analyzing is True
    assert len(kept[0].evidence) == 3


def test_merges_composite_event_with_overlapping_core_risk_and_keeps_all_evidence():
    price_fact = "公司股票收盘价为0.95元/股，低于1元面值。"
    risk_fact = "公司股票可能因股价低于面值被终止上市。"
    first = make_event(
        "e1",
        "公司股票低于面值，触及退市风险提示义务。",
        [price_fact],
    )
    second = make_event(
        "e2",
        "公司股票低于面值并存在被终止上市的风险，同时披露审计意见。",
        [price_fact, risk_fact],
    )

    kept = dedupe_events([first, second])

    assert len(kept) == 1
    assert {item.text for item in kept[0].evidence} == {price_fact, risk_fact}


def test_related_disclosures_are_linked_without_merging_source_records():
    first = {
        "opportunity_id": "o1",
        "announcement_id": "a1",
        "stock_code": "000001",
        "publish_date": "2026-11-17",
        "category": "其他类",
        "subcategory": "续聘会计师事务所",
        "event_summary": "公司拟续聘会计师事务所担任年度审计机构。",
    }
    second = {
        "opportunity_id": "o2",
        "announcement_id": "a2",
        "stock_code": "000001",
        "publish_date": "2026-11-18",
        "category": "其他类",
        "subcategory": "续聘会计师事务所",
        "event_summary": "董事会同意续聘会计师事务所担任年度审计机构。",
    }

    linked = link_related_disclosures([first, second])

    assert len(linked) == 2
    assert linked[0]["related_announcement_ids"] == ["a2"]
    assert linked[1]["related_announcement_ids"] == ["a1"]


def test_related_disclosure_linking_does_not_link_unrelated_subcategories():
    first = {
        "opportunity_id": "o1",
        "announcement_id": "a1",
        "stock_code": "000001",
        "publish_date": "2026-11-17",
        "category": "其他类",
        "subcategory": "续聘会计师事务所",
        "event_summary": "公司拟续聘会计师事务所担任年度审计机构。",
    }
    second = {
        **first,
        "opportunity_id": "o2",
        "announcement_id": "a2",
        "subcategory": "高管任免",
    }

    linked = link_related_disclosures([first, second])

    assert linked[0]["related_announcement_ids"] == []
    assert linked[1]["related_announcement_ids"] == []


def test_keeps_similar_generic_summaries_with_different_counterparties():
    first = make_event(
        "e1", "公司签订销售合同", ["公司与甲方签订销售合同。"]
    )
    second = make_event(
        "e2", "公司签订销售合同", ["公司与乙方签订销售合同。"]
    )
    assert [event.event_id for event in dedupe_events([first, second])] == ["e1", "e2"]


def test_keeps_similar_event_summaries_with_conflicting_amounts():
    first = make_event("e1", "公司签订100万元销售合同", ["合同金额100万元。"])
    second = make_event("e2", "公司签订200万元销售合同", ["合同金额200万元。"])
    assert [event.event_id for event in dedupe_events([first, second])] == ["e1", "e2"]


def test_keeps_same_nominal_number_with_different_amount_units():
    first = make_event("e1", "公司投资100万元", ["公司投资100万元。"])
    second = make_event("e2", "公司投资100万美元", ["公司投资100万美元。"])
    assert [event.event_id for event in dedupe_events([first, second])] == ["e1", "e2"]


def test_normalize_text_removes_punctuation_and_spaces():
    assert normalize_text(" 本期债券，规模 20 亿元。 ") == normalize_text("本期债券规模20亿元")

def item(oid, summary, texts, value=None, category="融资新增类"):
    return {"opportunity_id": oid, "category": category, "event_summary": summary, "evidence": [{"text": text} for text in texts], "amount": {"value": value}}


def test_merges_duplicate_opportunity_summaries():
    first = item("o1", "公司发行可续期公司债券20亿元，票面利率1.67%。", ["本期债券实际发行规模为20亿元。"])
    second = item("o2", "公司发行可续期公司债券20亿元，票面利率1.67%，基础期限3年。", ["最终票面利率为1.67%。"])
    kept = dedupe_opportunities([first, second])
    assert [entry["opportunity_id"] for entry in kept] == ["o1"]


def test_merges_opportunities_sharing_evidence():
    quote = "本期债券实际发行规模为20亿元，最终票面利率为1.67%。"
    first = item("o1", "发行债券20亿元。", [quote])
    second = item("o2", "承销机构合计认购9.8亿元。", [quote])
    kept = dedupe_opportunities([first, second])
    assert [entry["opportunity_id"] for entry in kept] == ["o1"]


def test_keeps_distinct_opportunities():
    first = item("o1", "公司发行可续期公司债券20亿元。", ["本期债券实际发行规模为20亿元。"])
    second = item("o2", "公司获得银行授信总额3602.96亿元。", ["公司共获得各家银行授信总额3,602.96亿元。"])
    kept = dedupe_opportunities([first, second])
    assert [entry["opportunity_id"] for entry in kept] == ["o1", "o2"]


def test_keeps_cross_category_opportunities_sharing_evidence():
    quote = "本期债券实际发行规模为20亿元。"
    first = item("o1", "公司发行可续期公司债券20亿元。", [quote], category="融资新增类")
    second = item("o2", "评级机构评定本期债券信用等级为AAA。", [quote], category="报告类")
    kept = dedupe_opportunities([first, second])
    assert [entry["opportunity_id"] for entry in kept] == ["o1", "o2"]


def test_merges_cross_category_composite_duplicate_with_multiple_shared_facts():
    first = item(
        "o1",
        "公司股票收盘价0.95元/股，低于1元面值，存在被终止上市的风险。",
        ["公司股票收盘价为0.95元/股，低于1元面值。"],
        value=0.95,
        category="报告类",
    )
    second = item(
        "o2",
        "公司股票收盘价为0.95元/股，低于1元，存在因股价低于面值被终止上市的风险；"
        "公司审计报告被出具无法表示意见。",
        ["收盘价0.95元/股", "低于1元"],
        value=0.95,
        category="其他类",
    )
    first.update(
        announcement_id="same-announcement",
        amount={"value": 0.95, "type": "股票收盘价", "currency": "CNY"},
        _source_event_id="e1",
    )
    second.update(
        announcement_id="same-announcement",
        amount={"value": 0.95, "type": "股价", "currency": "CNY"},
        _source_event_id="e2",
    )

    kept = dedupe_opportunities([first, second])

    assert len(kept) == 1
    assert len(kept[0]["evidence"]) == 3


def test_keeps_same_summary_opportunities_from_different_categories():
    first = item("o1", "公司获得1000万元项目合同", ["签订项目合同1000万元。"], category="销售类")
    second = item("o2", "公司获得1000万元项目合同", ["签订项目合同1000万元。"], category="融资新增类")
    assert [entry["opportunity_id"] for entry in dedupe_opportunities([first, second])] == ["o1", "o2"]


def test_keeps_similar_opportunities_with_conflicting_amounts():
    first = item("o1", "公司签订100万元销售合同", ["合同金额100万元。"], value=100)
    second = item("o2", "公司签订200万元销售合同", ["合同金额200万元。"], value=200)
    assert [entry["opportunity_id"] for entry in dedupe_opportunities([first, second])] == ["o1", "o2"]


def test_does_not_merge_different_source_events_with_similar_opportunities():
    first = item(
        "o1",
        "公司签订100万元设备采购合同",
        ["公司签订设备采购合同，金额100万元。"],
        category="采购类",
    )
    second = item(
        "o2",
        "公司签订100万元设备采购合同",
        ["公司与另一供应商签订设备采购合同，金额100万元。"],
        category="采购类",
    )
    first["_source_event_id"] = "event_a"
    second["_source_event_id"] = "event_b"
    kept = dedupe_opportunities([first, second])
    assert [entry["opportunity_id"] for entry in kept] == ["o1", "o2"]
