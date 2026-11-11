import json
import time
from datetime import date
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator
from core.storage import atomic_write_json

def prepared(tmp_path):
    runner = ScanOrchestrator(None, tmp_path)
    scan = runner.create([Company(stock_code='1',company_name='A')],DateRange(start_date=date(2025,1,1),end_date=date(2025,1,1)))
    text = tmp_path/'text.txt'
    text.write_text('source')
    a = Announcement(announcement_id='a',stock_code='000001',company_name='A',title='title',publish_date=date(2025,1,1),source_url='https://example.com',text_path=str(text))
    atomic_write_json(tmp_path/'results'/scan.scan_id/'announcements.json',{'items':[a.dict()]})
    scan.status='analyzing'
    runner._save(scan)
    return runner,scan

def test_empty_result_checkpoint_survives_new_runner(tmp_path):
    runner,scan=prepared(tmp_path)
    class Empty:
        calls=0
        def analyze(self,*args):
            self.calls+=1
            return EventExtractionResult(announcement_id='a',events=[])
    agent=Empty()
    runner.run_agents(scan.scan_id,agent,None,None)
    ScanOrchestrator(None,tmp_path).run_agents(scan.scan_id,agent,None,None)
    assert agent.calls==1
    assert runner.load(scan.scan_id).progress.completed==1

def test_deadline_failure_is_checkpointed(tmp_path):
    runner,scan=prepared(tmp_path)
    class Slow:
        def analyze(self,*args):
            time.sleep(.02)
            return EventExtractionResult(announcement_id='a',events=[])
    result=runner.run_agents(scan.scan_id,Slow(),None,None,per_announcement_timeout=.001)
    assert result.errors[0].code=='AGENT_TIMEOUT'
    assert result.counts.analysis_failed==1

def test_task_budget_stops_new_calls(tmp_path):
    runner,scan=prepared(tmp_path)
    result=runner.run_agents(scan.scan_id,None,None,None,task_timeout=0)
    assert result.errors[0].code=='TASK_TIMEOUT'
