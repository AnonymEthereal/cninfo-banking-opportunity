from datetime import datetime
from uuid import uuid4
from pathlib import Path
from time import sleep
import os
import json
from .config import data_dir
from .models import Announcement, Company, DateRange, Scan, ScanError, ScanStatus, Opportunity, EventExtractionResult
from .storage import atomic_write_json, read_model
from parsers.document import parse_document
from exporters.excel_exporter import export_xlsx
from exporters.html_exporter import export_html

class ScanOrchestrator:
    def __init__(self, collector, root: Path | None = None):
        self.collector, self.root = collector, (root or data_dir())

    def create(self, companies: list[Company], date_range: DateRange) -> Scan:
        now = datetime.now().astimezone()
        scan = Scan(scan_id="scan_" + uuid4().hex[:20], companies=companies, date_range=date_range, created_at=now, updated_at=now)
        scan.counts.companies = len(companies)
        self._save(scan)
        return scan

    def load(self, scan_id: str) -> Scan:
        return read_model(self.root / "results" / scan_id / "scan.json", Scan)

    def run_collection(self, scan_id: str) -> Scan:
        scan = self.load(scan_id)
        if scan.status not in (ScanStatus.QUEUED, ScanStatus.COLLECTING):
            return scan
        scan.status, scan.progress.stage = ScanStatus.COLLECTING, "collecting"
        scan.progress.total = len(scan.companies)
        self._save(scan)
        folder = self.root / "results" / scan.scan_id
        checkpoint_dir = folder / "collection"
        prior_path = folder / "announcements.json"
        prior = {item["announcement_id"]: item for item in json.loads(prior_path.read_text(encoding="utf-8"))["items"]} if prior_path.exists() else {}
        announcements = []
        scan.progress.completed = 0
        scan.counts.collection_failed = 0
        scan.errors = [error for error in scan.errors if error.scope != "company"]
        interval = max(0.0, float(os.getenv("COLLECTION_INTERVAL_SECONDS", "0.5")))
        for index, company in enumerate(scan.companies):
            if index and interval:
                sleep(interval)
            try:
                checkpoint = checkpoint_dir / f"{company.stock_code}.json"
                if checkpoint.exists():
                    items = [Announcement.model_validate(item) for item in json.loads(checkpoint.read_text(encoding="utf-8"))["items"]]
                else:
                    items = self.collector.collect(company, scan.date_range)
                    atomic_write_json(checkpoint, {"items": [item.model_dump() for item in items]})
                announcements.extend(Announcement.model_validate(prior.get(item.announcement_id, item.model_dump())) for item in items)
            except Exception as exc:
                scan.counts.collection_failed += 1
                scan.errors.append(ScanError(scope="company", code="COLLECTION_FAILED", message="公告检索失败。", details={"stock_code": company.stock_code, "company_name": company.company_name, "stage": "collecting", "error_type": type(exc).__name__, "http_status": getattr(getattr(exc, "response", None), "status_code", None), "action": "检查网络或来源服务后重试采集。"}))
            scan.progress.completed += 1
            unique = {item.announcement_id: item for item in announcements}
            scan.counts.announcements = len(unique)
            atomic_write_json(folder / "announcements.json", {"items": [item.model_dump() for item in unique.values()]})
            scan.updated_at = datetime.now().astimezone()
            self._save(scan)
        unique = {item.announcement_id: item for item in announcements}
        scan.counts.announcements = len(unique)
        scan.progress.stage, scan.updated_at = "collection_completed", datetime.now().astimezone()
        atomic_write_json(self.root / "results" / scan.scan_id / "announcements.json", {"items": [v.model_dump() for v in unique.values()]})
        self._save(scan)
        return scan

    def prepare_analysis(self, scan_id: str, retry_documents=False) -> Scan:
        scan = self.load(scan_id)
        scan.status, scan.progress.stage = ScanStatus.ANALYZING, "documents"
        scan.errors = [error for error in scan.errors if error.code != "DOCUMENT_PARSE_FAILED"]
        scan.counts.document_failed = 0
        path = self.root / "results" / scan.scan_id / "announcements.json"
        payload = __import__("json").loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
        announcements = [Announcement.model_validate(item) for item in payload.get("items", [])]
        scan.progress.total, scan.progress.completed = len(announcements), 0
        for announcement in announcements:
            try:
                if announcement.text_path and Path(announcement.text_path).exists() and Path(announcement.text_path).stat().st_size:
                    scan.progress.completed += 1
                    self._save(scan)
                    continue
                if not announcement.source_url:
                    raise ValueError("SOURCE_URL_MISSING")
                if announcement.content_path and not retry_documents:
                    file_path = Path(announcement.content_path)
                else:
                    file_path = self.root / "announcements" / announcement.announcement_id / "source.pdf"
                    self.collector.download(announcement, file_path)
                    announcement.content_path = str(file_path)
                text, pages = parse_document(file_path)
                announcement.text_path = str(self.root / "announcements" / announcement.announcement_id / "text.txt")
                Path(announcement.text_path).parent.mkdir(parents=True, exist_ok=True)
                Path(announcement.text_path).write_text(text, encoding="utf-8")
                atomic_write_json(self.root / "results" / scan.scan_id / "announcements.json", {"items": [a.model_dump() if a.announcement_id == announcement.announcement_id else a.model_dump() for a in announcements]})
            except Exception as exc:
                scan.counts.document_failed += 1
                scan.errors.append(ScanError(scope="announcement", announcement_id=announcement.announcement_id, code="DOCUMENT_PARSE_FAILED", message="公告正文获取或解析失败。", details={"stage": "documents", "error_type": type(exc).__name__, "title": announcement.title, "action": "查看原文，重试下载解析；扫描件目前不支持 OCR。"}))
            scan.progress.completed += 1
            scan.updated_at = datetime.now().astimezone()
            self._save(scan)
        return scan

    def run_agents(self, scan_id, event_agent, classify_agent, marketing_agent, per_announcement_timeout=600, task_timeout=28800, retry_failed=False):
        from .batch import run_batch
        scan = self.load(scan_id)
        if scan.status != ScanStatus.ANALYZING:
            return scan
        return run_batch(self, scan, (event_agent, classify_agent, marketing_agent), per_announcement_timeout, task_timeout, retry_failed=retry_failed)

    def run_export(self, scan_id: str) -> Scan:
        scan = self.load(scan_id)
        scan.status, scan.progress.stage = ScanStatus.EXPORTING, "exporting"
        result_dir = self.root / "results" / scan.scan_id
        import json
        payload = json.loads((result_dir / "opportunities.json").read_text(encoding="utf-8")) if (result_dir / "opportunities.json").exists() else {"items": []}
        opportunities = [Opportunity.model_validate(item) for item in payload.get("items", [])]
        report_dir = self.root / "reports" / scan.scan_id
        report_scan = scan.model_copy(deep=True)
        report_scan.status = ScanStatus.COMPLETED_WITH_ERRORS if scan.errors else ScanStatus.COMPLETED
        export_xlsx(opportunities, report_scan, report_dir / "report.xlsx")
        export_html(opportunities, report_scan, report_dir / "report.html")
        scan.status = ScanStatus.COMPLETED_WITH_ERRORS if scan.errors else ScanStatus.COMPLETED
        scan.progress.stage = "completed"
        scan.updated_at = datetime.now().astimezone()
        self._save(scan)
        return scan

    def save(self, scan: Scan) -> None:
        """Public persistence boundary used by the application service."""
        scan.updated_at = datetime.now().astimezone()
        atomic_write_json(self.root / "results" / scan.scan_id / "scan.json", scan)

    def _save(self, scan: Scan):
        self.save(scan)
