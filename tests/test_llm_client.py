import json

import pytest

from core.llm_client import LLMError, OpenAICompatibleClient
from core.models import MarketingAnalysis


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


class RecordingTransport:
    def __init__(self, *responses):
        self.responses = list(responses) or [json.dumps({"suggested_action": "跟进"})]
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append(kwargs)
        return FakeResponse(self.responses.pop(0))

    def close(self):
        pass


def test_llm_client_closes_transport():
    transport = FakeClient()
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport)
    client.close()
    assert transport.closed


def test_generate_json_serializes_schema_into_prompt():
    transport = RecordingTransport()
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport)
    result = client.generate_json("测试", MarketingAnalysis)
    assert isinstance(result, MarketingAnalysis)
    prompt = transport.requests[0]["json"]["messages"][0]["content"]
    assert '"suggested_action"' in prompt


def test_generate_json_accepts_fenced_json():
    transport = RecordingTransport('```json\n{"bank_products": ["项目贷款"]}\n```')
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport)
    result = client.generate_json("测试", MarketingAnalysis)
    assert result.bank_products == ["项目贷款"]
    assert len(transport.requests) == 1


def test_schema_failure_is_repaired_with_feedback():
    transport = RecordingTransport(
        json.dumps({"bank_products": 123}),
        json.dumps({"bank_products": ["项目贷款"]}),
    )
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport)
    result = client.generate_json("测试", MarketingAnalysis)
    assert result.bank_products == ["项目贷款"]
    assert len(transport.requests) == 2
    assert "did not match the JSON schema" in transport.requests[1]["json"]["messages"][0]["content"]


def test_invalid_json_is_repaired_then_rejected():
    transport = RecordingTransport("not json", "still not json")
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport, repairs=1)
    with pytest.raises(LLMError) as raised:
        client.generate_json("测试", MarketingAnalysis)
    assert raised.value.code == "LLM_INVALID_JSON"
    assert len(transport.requests) == 2


def test_schema_repair_can_be_disabled():
    transport = RecordingTransport(json.dumps({"bank_products": 123}))
    client = OpenAICompatibleClient(
        "k", "https://example", "m", client=transport, repairs=0
    )
    with pytest.raises(LLMError) as raised:
        client.generate_json("测试", MarketingAnalysis)
    assert raised.value.code == "SCHEMA_VALIDATION_FAILED"
    assert len(transport.requests) == 1
