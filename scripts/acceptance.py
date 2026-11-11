import json
import sys
import tempfile
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.models import Company, DateRange, Announcement, EventExtractionResult
from core.scanner import ScanOrchestrator
from agents.event_agent import MockEventAgent
from agents.classify_agent import MockClassifyAgent
from agents.marketing_agent import MockMarketingAgent

class AcceptanceCollector:
    def collect(self, company, date_range):
        if company.stock_code == "000020":
            raise RuntimeError("fixture collection failure")
        return [Announcement(announcement_id=f"ann-{company.stock_code}", stock_code=company.stock_code, company_name=company.company_name, title="fixture announcement", publish_date=date_range.start_date, source_url="https://example.invalid/source")]

    def download(self, announcement, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        import fitz
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "fixture announcement body")
            document.save(destination)
        return destination

def run():
    with tempfile.TemporaryDirectory(prefix="opportunity-radar-") as folder:
        root = Path(folder)
        companies = [Company(stock_code=str(i), company_name=f"Company {i}") for i in range(1, 21)]
        runner = ScanOrchestrator(AcceptanceCollector(), root)
        scan = runner.create(companies, DateRange(start_date=date(2026, 11, 1), end_date=date(2026, 11, 6)))
        runner.run_collection(scan.scan_id)
        runner.prepare_analysis(scan.scan_id)
        runner.run_agents(scan.scan_id, MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
        final = runner.run_export(scan.scan_id)
        report_dir = root / "reports" / scan.scan_id
        checks = {
            "companies_20": final.counts.companies == 20,
            "collection_failure_visible": final.counts.collection_failed == 1,
            "xlsx_created": (report_dir / "report.xlsx").exists(),
            "html_created": (report_dir / "report.html").exists(),
            "terminal_status": getattr(final.status, "value", final.status) in {"completed", "completed_with_errors"},
        }
        result = {"scan_id": scan.scan_id, "checks": checks, "passed": all(checks.values()), "status": str(final.status), "counts": final.counts.dict()}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(run())
