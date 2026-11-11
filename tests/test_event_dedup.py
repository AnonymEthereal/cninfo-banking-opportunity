from core.dedup import dedupe_events, dedupe_opportunities, normalize_text
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
    assert len(kept[0].evidence) == 2


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
