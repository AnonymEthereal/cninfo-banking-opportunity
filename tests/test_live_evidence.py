from types import SimpleNamespace

import pytest

from agents.live import EvidenceError, LiveClassifyAgent, LiveEventAgent, check_evidence
from core.models import Event, EventExtractionResult, Evidence, OpportunityDraft


def test_whitespace_only_normalization():
    check_evidence([Evidence(text="投资100万元")], "投资 100\n万元")
    with pytest.raises(EvidenceError):
        check_evidence([Evidence(text="投资200万元")], "投资100万元")


def test_typography_variants_match_but_paraphrase_does_not():
    source = "公司拟投资１亿元（含税），建设新基地。2026—2028年完成。"
    check_evidence([Evidence(text="公司拟投资1亿元(含税),建设新基地.")], source)
    check_evidence([Evidence(text="2026-2028年完成")], source)
    check_evidence([Evidence(text='"公司拟投资1亿元"')], source)
    with pytest.raises(EvidenceError):
        check_evidence([Evidence(text="公司拟投资约1亿元")], source)
    with pytest.raises(EvidenceError):
        check_evidence([Evidence(text="公司拟投资…建设新基地")], source)


def test_invisible_characters_do_not_break_matching():
    check_evidence([Evidence(text="投资100万元")], "投\u200b资100万元\u00ad")


def test_empty_evidence_is_rejected():
    with pytest.raises(EvidenceError):
        check_evidence([], "正文")


def test_procedural_event_with_bad_evidence_does_not_discard_real_events():
    class Client:
        def generate_json(self, *args):
            return EventExtractionResult(
                announcement_id="ignored",
                events=[
                    Event(
                        event_id="",
                        summary="会议程序",
                        evidence=[Evidence(text="这句不在原文")],
                        worth_analyzing=False,
                    ),
                    Event(
                        event_id="",
                        summary="项目投资",
                        evidence=[Evidence(text="投资100万元")],
                        worth_analyzing=True,
                    ),
                ],
            )

    result = LiveEventAgent(Client()).analyze(
        SimpleNamespace(announcement_id="ann_1"), "公司拟投资100万元建设基地。"
    )
    assert [event.event_id for event in result.events] == ["ev_1", "ev_2"]


def test_event_agent_repairs_a_rejected_quote_once():
    class Client:
        calls = 0

        def generate_json(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                return EventExtractionResult(
                    announcement_id="ignored",
                    events=[
                        Event(
                            event_id="e1",
                            summary="项目投资",
                            evidence=[Evidence(text="投资约100万元")],
                        )
                    ],
                )
            assert "rejected" in prompt
            return EventExtractionResult(
                announcement_id="ignored",
                events=[
                    Event(
                        event_id="e1",
                        summary="项目投资",
                        evidence=[Evidence(text="投资100万元")],
                    )
                ],
            )

    client = Client()
    result = LiveEventAgent(client).analyze(
        SimpleNamespace(announcement_id="ann_1"), "公司拟投资100万元建设基地。"
    )
    assert client.calls == 2
    assert result.announcement_id == "ann_1"
    assert result.events[0].evidence[0].text == "投资100万元"


def test_event_agent_keeps_checkpoint_when_repair_also_fails():
    class Client:
        calls = 0

        def generate_json(self, *args):
            self.calls += 1
            return EventExtractionResult(
                announcement_id="ignored",
                events=[
                    Event(
                        event_id="e1",
                        summary="项目投资",
                        evidence=[Evidence(text="投资约100万元")],
                    )
                ],
            )

    client = Client()
    with pytest.raises(EvidenceError) as raised:
        LiveEventAgent(client).analyze(
            SimpleNamespace(announcement_id="ann_1"), "公司拟投资100万元建设基地。"
        )
    assert client.calls == 2
    assert raised.value.code == "EVIDENCE_MISMATCH"
    assert raised.value.quote == "投资约100万元"


def test_classify_agent_repairs_amount_evidence_and_marks_undisclosed():
    class Client:
        calls = 0

        def generate_json(self, prompt, schema):
            self.calls += 1
            original = "约1亿元" if self.calls == 1 else "1亿元"
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="投资1亿元")],
                amount={"value": 100000000, "original_text": original},
            )

    client = Client()
    result = LiveClassifyAgent(client).analyze(
        Event(event_id="e1", summary="项目投资"), "公司拟投资1亿元建设基地。"
    )
    assert client.calls == 2
    assert result.event_id == "e1"
    assert result.amount.undisclosed is False


def test_amount_uses_same_source_matching():
    class Client:
        def generate_json(self, *args):
            return OpportunityDraft(
                event_id="e",
                category="投资并购类",
                event_summary="投资",
                evidence=[{"text": "投资100万元"}],
                amount={"value": 1000000, "original_text": "投资100万元"},
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e", summary="投资"), "投资 100\n万元"
    )
    assert result.amount.value == 1000000


def test_classify_agent_repairs_amount_value_with_wrong_unit_conversion():
    class Client:
        calls = 0

        def generate_json(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                assert "100000000" in prompt
                value = 10000000
            else:
                value = 100000000
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="人民币1亿元")],
                amount={
                    "value": value,
                    "currency": "CNY",
                    "type": "项目投资",
                    "original_text": "人民币1亿元",
                },
            )

    client = Client()
    result = LiveClassifyAgent(client).analyze(
        Event(event_id="e1", summary="项目投资"),
        "公司拟投资人民币1亿元建设基地。",
    )
    assert client.calls == 2
    assert result.amount.value == 100000000
    assert result.amount.currency == "CNY"


def test_persistent_amount_mismatch_is_retained_as_pending_not_false_value():
    class Client:
        calls = 0

        def generate_json(self, prompt, schema):
            self.calls += 1
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="人民币1亿元")],
                amount={
                    "value": 10000000,
                    "currency": "CNY",
                    "type": "项目投资",
                    "original_text": "人民币1亿元",
                },
            )

    client = Client()
    result = LiveClassifyAgent(client).analyze(
        Event(event_id="e1", summary="项目投资"),
        "公司拟投资人民币1亿元建设基地。",
    )
    assert client.calls == 2
    assert result.amount.value is None
    assert result.amount.undisclosed is False
    assert result.amount.verification_status == "pending"
    assert result.amount.original_text == "人民币1亿元"


def test_classify_agent_does_not_assume_cny_from_bare_yuan_unit():
    class Client:
        def generate_json(self, prompt, schema):
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="投资100万元")],
                amount={
                    "value": 1000000,
                    "currency": "CNY",
                    "type": "项目投资",
                    "original_text": "投资100万元",
                },
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e1", summary="项目投资"), "公司投资100万元建设基地。"
    )
    assert result.amount.currency is None


def test_unrecognized_amount_notation_is_marked_for_review():
    class Client:
        def generate_json(self, prompt, schema):
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="人民币一亿元")],
                amount={
                    "value": 100000000,
                    "currency": "CNY",
                    "type": "项目投资",
                    "original_text": "人民币一亿元",
                },
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e1", summary="项目投资"),
        "公司拟投资人民币一亿元建设基地。",
    )
    assert result.amount.value == 100000000
    assert result.amount.currency is None
    assert result.amount.verification_status == "pending"


def test_undecodable_chinese_amount_is_not_reported_as_undisclosed():
    class Client:
        def generate_json(self, prompt, schema):
            return OpportunityDraft(
                event_id="ignored",
                category="投资并购类",
                event_summary="项目投资",
                evidence=[Evidence(text="投资人民币一亿元")],
                amount={
                    "value": None,
                    "currency": "CNY",
                    "type": "项目投资",
                    "original_text": "投资人民币一亿元",
                    "undisclosed": True,
                },
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e1", summary="项目投资"),
        "公司拟投资人民币一亿元建设基地。",
    )
    assert result.amount.value is None
    assert result.amount.undisclosed is False
    assert result.amount.currency is None
    assert result.amount.verification_status == "pending"


def test_classify_agent_repairs_currency_conflicting_with_source():
    class Client:
        calls = 0

        def generate_json(self, prompt, schema):
            self.calls += 1
            currency = "CNY" if self.calls == 1 else "USD"
            return OpportunityDraft(
                event_id="ignored",
                category="融资新增类",
                event_summary="美元借款",
                evidence=[Evidence(text="美元100万元")],
                amount={
                    "value": 1000000,
                    "currency": currency,
                    "type": "借款",
                    "original_text": "美元100万元",
                },
            )

    client = Client()
    result = LiveClassifyAgent(client).analyze(
        Event(event_id="e1", summary="美元借款"),
        "公司获得美元100万元借款。",
    )
    assert client.calls == 2
    assert result.amount.currency == "USD"


def test_persistent_currency_mismatch_is_retained_as_pending():
    class Client:
        def generate_json(self, prompt, schema):
            return OpportunityDraft(
                event_id="ignored",
                category="融资新增类",
                event_summary="美元借款",
                evidence=[Evidence(text="美元100万元")],
                amount={
                    "value": 1000000,
                    "currency": "CNY",
                    "type": "借款",
                    "original_text": "美元100万元",
                },
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e1", summary="美元借款"),
        "公司获得美元100万元借款。",
    )
    assert result.amount.value == 1000000
    assert result.amount.currency is None
    assert result.amount.verification_status == "pending"


def test_marketing_agent_does_not_receive_the_full_announcement():
    from agents.live import LiveMarketingAgent
    from core.models import MarketingAnalysis

    class Client:
        prompt = None

        def generate_json(self, prompt, schema):
            self.prompt = prompt
            return MarketingAnalysis(suggested_action="核实项目计划")

    client = Client()
    draft = OpportunityDraft(
        event_id="e1",
        category="采购类",
        event_summary="采购生产设备",
        evidence=[Evidence(text="公司拟采购生产设备")],
    )
    result = LiveMarketingAgent(client).analyze(
        draft, "PRIVATE_SENTINEL: 不应被营销阶段重复发送的长公告正文"
    )
    assert result.suggested_action == "核实项目计划"
    assert "PRIVATE_SENTINEL" not in client.prompt
    assert "公司拟采购生产设备" in client.prompt


@pytest.mark.parametrize(
    ("category", "guidance"),
    [
        ("采购类", "付款安排"),
        ("销售类", "回款安排"),
        ("投资并购类", "交易结构"),
        ("融资新增类", "实际提款"),
        ("增值类", "流动性约束"),
        ("报告类", "财务变化"),
        ("其他类", "新设子公司"),
    ],
)
def test_marketing_agent_runs_category_specific_analysis(category, guidance):
    from agents.live import LiveMarketingAgent
    from core.models import MarketingAnalysis

    class Client:
        prompt = None

        def generate_json(self, prompt, schema):
            self.prompt = prompt
            return MarketingAnalysis(
                analysis_outcome="no_clear_entry_point",
                analysis_summary="公告信息不足，暂未识别明确切入点。",
            )

    client = Client()
    draft = OpportunityDraft(
        event_id="e1",
        category=category,
        event_summary="测试事件",
        evidence=[Evidence(text="公告披露的测试事实")],
    )
    result = LiveMarketingAgent(client).analyze(
        draft, "PRIVATE_SENTINEL: 不应发送的完整公告正文"
    )

    assert result.analysis_outcome == "no_clear_entry_point"
    assert guidance in client.prompt
    assert category in client.prompt
    assert "PRIVATE_SENTINEL" not in client.prompt


def test_shareholder_stock_increase_is_not_classified_as_company_value_management():
    from agents.live import LiveClassifyAgent
    from core.models import Event, OpportunityDraft

    quote = "第一大股东及一致行动人计划增持公司股份，金额不低于5000万元。"

    class Client:
        def generate_json(self, prompt, schema):
            return OpportunityDraft(
                category="增值类",
                event_summary="第一大股东计划增持公司股份。",
                amount={"value": None, "undisclosed": True, "type": "增持金额"},
                evidence=[Evidence(text=quote)],
            )

    result = LiveClassifyAgent(Client()).analyze(
        Event(event_id="e1", summary=quote, evidence=[Evidence(text=quote)]),
        quote,
    )

    assert result.category == "其他类"
    assert result.subcategory == "股东增持或减持"
