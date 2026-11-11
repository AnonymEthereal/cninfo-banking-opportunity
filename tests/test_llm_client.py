from core.llm_client import OpenAICompatibleClient

class FakeClient:
    def __init__(self): self.closed = False
    def close(self): self.closed = True

def test_llm_client_closes_transport():
    transport = FakeClient()
    client = OpenAICompatibleClient("k", "https://example", "m", client=transport)
    client.close()
    assert transport.closed
