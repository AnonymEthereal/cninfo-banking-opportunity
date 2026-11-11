from datetime import date
from pathlib import Path
from core.models import Company, DateRange, ScanStatus
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager

class NoopCollector:
    def collect(self, company, date_range): return []

def test_task_manager_runs_empty_scan(tmp_path: Path):
    manager = TaskManager(ScanOrchestrator(NoopCollector(), tmp_path))
    scan = manager.orchestrator.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026,1,1), end_date=date(2026,1,2)))
    future = manager.submit(scan.scan_id, (lambda *args: {"announcement_id":"x","events":[]}, lambda *args: None, lambda *args: None))
    result = future.result(timeout=5)
    assert result.status in (ScanStatus.COMPLETED, "completed")
    manager.shutdown()

def test_task_manager_does_not_duplicate_running_task(tmp_path: Path):
    manager = TaskManager(ScanOrchestrator(NoopCollector(), tmp_path))
    scan = manager.orchestrator.create([Company(stock_code="1", company_name="A")], DateRange(start_date=date(2026,1,1), end_date=date(2026,1,2)))
    agents = (lambda *args: {"announcement_id":"x","events":[]}, lambda *args: None, lambda *args: None)
    first = manager.submit(scan.scan_id, agents)
    second = manager.submit(scan.scan_id, agents)
    assert first is second
    first.result(timeout=5)
    manager.shutdown()
