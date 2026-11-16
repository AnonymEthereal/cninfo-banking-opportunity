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
