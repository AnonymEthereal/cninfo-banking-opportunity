import pytest
from agents.live import check_evidence, EvidenceError, LiveClassifyAgent
from core.models import Evidence, Event, OpportunityDraft

def test_whitespace_only_normalization():
    check_evidence([Evidence(text='投资100万元')], '投资 100\n万元')
    with pytest.raises(EvidenceError):
        check_evidence([Evidence(text='投资200万元')], '投资100万元')

def test_amount_uses_same_source_matching():
    class Client:
        def generate_json(self,*args):
            return OpportunityDraft(event_id='e',category='投资并购类',event_summary='投资',evidence=[{'text':'投资100万元'}],amount={'value':1000000,'original_text':'投资100万元'})
    result=LiveClassifyAgent(Client()).analyze(Event(event_id='e',summary='投资'),'投资 100\n万元')
    assert result.amount.value==1000000
