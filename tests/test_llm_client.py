import json

from core.llm_client import OpenAICompatibleClient
from core.models import MarketingAnalysis

class FakeClient:
    def __init__(self): self.closed = False
    def close(self): self.closed = True

class FakeResponse:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass
    def json(self): return {"choices": [{"message": {"content": self.content}}]}

class RecordingTransport:
    def __init__(self):
        self.requests = []
    def post(self, url, **kwargs):
        self.requests.append(kwargs)
        return FakeResponse(json.dumps({"suggested_action": "跟进"}))
    def close(self): pass

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
