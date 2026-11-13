import json
import re
from pathlib import Path

from .errors import InvalidStateError, NotFoundError, ServiceError
from .models import Announcement, Company, DateRange, Opportunity, Scan, ScanError, ScanStatus
from .scanner import ScanOrchestrator
from .storage import read_model
from .task_manager import TaskManager
from .view_models import (
    AnnouncementPage,
    OpportunityPage,
    ScanCapabilities,
    ScanSummary,
    ScanView,
)
from agents.classify_agent import MockClassifyAgent
from agents.event_agent import MockEventAgent
from agents.live import live_agents
from agents.marketing_agent import MockMarketingAgent

TERMINAL_STATUSES = {
    ScanStatus.COMPLETED.value,
    ScanStatus.COMPLETED_WITH_ERRORS.value,
}
REVIEW_STATUS = ScanStatus.AWAITING_REVIEW.value
KNOWN_MODES = {"mock", "live"}


class AppService:
    """Application boundary consumed by the UI.

    The service owns persistence, agent selection, submissions and queries so the
    Streamlit layer only renders view models and reacts to capability flags.
    """

    def __init__(self, orchestrator: ScanOrchestrator, task_manager: TaskManager | None = None):
        self.orchestrator = orchestrator
        self.task_manager = task_manager or TaskManager(orchestrator)

    # ------------------------------------------------------------------
    # Scan lifecycle
    # ------------------------------------------------------------------
    def start_scan(
        self,
        companies: list[Company],
        date_range: DateRange,
        analysis_mode: str = "mock",
    ) -> ScanView:
        if not companies:
            raise ServiceError("COMPANIES_REQUIRED", "请至少选择一家企业。")
        if analysis_mode not in KNOWN_MODES:
            raise ServiceError("INVALID_ANALYSIS_MODE", "分析模式必须是 mock 或 live。")
        scan = self.orchestrator.create(companies, date_range)
        scan.analysis_mode = analysis_mode
        scan.require_review = True
        self.orchestrator.save(scan)
        try:
            # Collection does not use agents; the review gate decides the real mode.
            self.task_manager.submit(scan.scan_id, self._mock_agents())
        except Exception as exc:
            failed = self.orchestrator.load(scan.scan_id)
            failed.status = ScanStatus.FAILED.value
            failed.errors.append(
                ScanError(
                    scope="task",
                    code="TASK_SUBMIT_FAILED",
                    message="任务提交失败，请检查运行环境后重试。",
                    details={"stage": failed.progress.stage, "error_type": type(exc).__name__},
                )
            )
            self.orchestrator.save(failed)
            raise ServiceError("TASK_SUBMIT_FAILED", "任务提交失败，请稍后重试。") from exc
        return self.get_scan_view(scan.scan_id)

    def get_scan_view(self, scan_id: str) -> ScanView:
        scan = self.load_scan(scan_id)
        running = self.task_manager.is_running(scan_id)
        root = Path(self.orchestrator.root)
        report_dir = root / "reports" / scan_id
        reports = {
            "xlsx": (report_dir / "report.xlsx").exists(),
            "html": (report_dir / "report.html").exists(),
        }
        announcements_total = scan.counts.announcements
        if scan.status == REVIEW_STATUS and announcements_total == 0:
            announcements_total = len(self._announcements(scan_id))
        mode_known = scan.analysis_mode in KNOWN_MODES
        can_confirm = (
            scan.status == REVIEW_STATUS
            and not running
            and mode_known
            and announcements_total > 0
        )
        can_retry_sources = (
            (scan.counts.collection_failed > 0 or scan.counts.document_failed > 0)
            and not running
            and mode_known
        )
        can_retry_failed = scan.counts.analysis_failed > 0 and not running and mode_known
        can_resume = (
            scan.status not in TERMINAL_STATUSES
            and scan.status != REVIEW_STATUS
            and not running
            and mode_known
        )
        can_download = scan.status in TERMINAL_STATUSES and any(reports.values())
        return ScanView(
            scan=scan,
            capabilities=ScanCapabilities(
                is_running=running,
                can_confirm=can_confirm,
                can_save_budget=not running,
                can_retry_sources=can_retry_sources,
                can_retry_failed=can_retry_failed,
                can_resume=can_resume,
                can_download=can_download,
            ),
            announcements_total=announcements_total,
            opportunities_total=scan.counts.opportunities,
            reports=reports,
        )

    def save_budgets(self, scan_id: str, per_minutes: int, batch_hours: int) -> ScanView:
        if per_minutes < 1 or batch_hours < 1:
            raise ServiceError("INVALID_BUDGET", "时间预算必须至少为 1。")
        scan = self.load_scan(scan_id)
        if self.task_manager.is_running(scan_id):
            raise InvalidStateError("SCAN_RUNNING", "任务运行中，无法修改时间预算。")
        scan.announcement_timeout = int(per_minutes * 60)
        scan.analysis_timeout = int(batch_hours * 3600)
        self.orchestrator.save(scan)
        return self.get_scan_view(scan_id)

    def set_legacy_mode(self, scan_id: str, mode: str) -> ScanView:
        if mode not in KNOWN_MODES:
            raise ServiceError("INVALID_ANALYSIS_MODE", "分析模式必须是 mock 或 live。")
        scan = self.load_scan(scan_id)
        if scan.analysis_mode != "unknown":
            raise InvalidStateError("SCAN_MODE_ALREADY_SET", "该任务已经保存分析模式。")
        scan.analysis_mode = mode
        self.orchestrator.save(scan)
        return self.get_scan_view(scan_id)

    def confirm_analysis(
        self,
        scan_id: str,
        per_minutes: int | None = None,
        batch_hours: int | None = None,
    ) -> ScanView:
        scan = self.load_scan(scan_id)
        if scan.status != REVIEW_STATUS:
            raise InvalidStateError("SCAN_NOT_AWAITING_REVIEW", "当前任务不在等待确认阶段。")
        if scan.analysis_mode not in KNOWN_MODES:
            raise ServiceError("ANALYSIS_MODE_UNKNOWN", "恢复前请先选择原任务使用的分析模式。")
        if scan.counts.announcements <= 0:
            raise ServiceError("NO_ANNOUNCEMENTS", "本次检索没有公告，无需启动分析。")
        if per_minutes is not None and batch_hours is not None:
            if per_minutes < 1 or batch_hours < 1:
                raise ServiceError("INVALID_BUDGET", "时间预算必须至少为 1。")
            scan.announcement_timeout = int(per_minutes * 60)
            scan.analysis_timeout = int(batch_hours * 3600)
        scan.analysis_confirmed = True
        self.orchestrator.save(scan)
        try:
            self._submit_scan(scan)
        except Exception:
            scan.analysis_confirmed = False
            self.orchestrator.save(scan)
            raise
        return self.get_scan_view(scan_id)

    def resume_scan(self, scan_id: str) -> ScanView:
        scan = self.load_scan(scan_id)
        if scan.status in TERMINAL_STATUSES or scan.status == REVIEW_STATUS:
            raise InvalidStateError("SCAN_NOT_RESUMABLE", "当前任务无需从检查点继续。")
        if scan.analysis_mode not in KNOWN_MODES:
            raise ServiceError("ANALYSIS_MODE_UNKNOWN", "恢复前请先选择原任务使用的分析模式。")
        self._submit_scan(scan)
        return self.get_scan_view(scan_id)

    def retry_sources(self, scan_id: str) -> ScanView:
        scan = self.load_scan(scan_id)
        if not (scan.counts.collection_failed or scan.counts.document_failed):
            raise InvalidStateError("NO_SOURCE_FAILURES", "当前任务没有可重试的采集或解析失败项。")
        if scan.analysis_mode not in KNOWN_MODES:
            raise ServiceError("ANALYSIS_MODE_UNKNOWN", "重试前请先选择原任务使用的分析模式。")
        self._submit_scan(scan, retry_sources=True)
        return self.get_scan_view(scan_id)

    def retry_failed_analysis(self, scan_id: str) -> ScanView:
        scan = self.load_scan(scan_id)
        if not scan.counts.analysis_failed:
            raise InvalidStateError("NO_ANALYSIS_FAILURES", "当前任务没有可重试的失败分析。")
        if scan.analysis_mode not in KNOWN_MODES:
            raise ServiceError("ANALYSIS_MODE_UNKNOWN", "重试前请先选择原任务使用的分析模式。")
        self._submit_scan(scan, retry_failed=True)
        return self.get_scan_view(scan_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def load_scan(self, scan_id: str) -> Scan:
        path = self._scan_dir(scan_id) / "scan.json"
        if not path.exists():
            raise NotFoundError("SCAN_NOT_FOUND", "找不到该扫描任务。")
        try:
            return read_model(path, Scan)
        except Exception as exc:
            raise ServiceError(
                "SCAN_DATA_CORRUPT",
                "任务状态文件无法读取，请从历史任务重新选择。",
                {"scan_id": scan_id, "error_type": type(exc).__name__},
            ) from exc

    def list_history(self, limit: int | None = 50) -> list[ScanSummary]:
        results = Path(self.orchestrator.root) / "results"
        if not results.exists():
            return []
        summaries: list[ScanSummary] = []
        for path in results.glob("*/scan.json"):
            try:
                scan = read_model(path, Scan)
            except Exception:
                # A corrupt historical task must not block the whole page.
                continue
            summaries.append(
                ScanSummary(
                    scan_id=scan.scan_id,
                    created_at=scan.created_at,
                    updated_at=scan.updated_at,
                    status=str(scan.status),
                    analysis_mode=scan.analysis_mode,
                    companies=scan.counts.companies,
                    announcements=scan.counts.announcements,
                    opportunities=scan.counts.opportunities,
                )
            )
        summaries.sort(key=lambda item: item.created_at, reverse=True)
        return summaries[:limit] if limit is not None else summaries

    def list_announcements(
        self,
        scan_id: str,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
    ) -> AnnouncementPage:
        items = self._announcements(scan_id)
        keyword = query.strip().casefold()
        if keyword:
            items = [
                item
                for item in items
                if keyword in f"{item.title}{item.company_name}{item.stock_code}".casefold()
            ]
        total = len(items)
        page = self._slice(items, limit, offset)
        return AnnouncementPage(items=page, total=total, offset=max(offset, 0), limit=limit)

    def list_opportunities(
        self,
        scan_id: str,
        categories: list[str] | None = None,
        companies: list[str] | None = None,
        statuses: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> OpportunityPage:
        items = self._opportunities(scan_id)
        facets = {
            "categories": sorted({item.category for item in items}),
            "companies": sorted({item.company_name for item in items}),
            "statuses": sorted({item.analysis_status for item in items}),
        }
        if categories is not None:
            items = [item for item in items if item.category in categories]
        if companies is not None:
            items = [item for item in items if item.company_name in companies]
        if statuses is not None:
            items = [item for item in items if item.analysis_status in statuses]
        items.sort(
            key=lambda item: (item.publish_date, item.company_name, item.opportunity_id),
            reverse=True,
        )
        total = len(items)
        return OpportunityPage(
            items=self._slice(items, limit, offset),
            total=total,
            offset=max(offset, 0),
            limit=limit,
            **facets,
        )

    def get_report_bytes(self, scan_id: str, fmt: str) -> bytes:
        if fmt not in {"xlsx", "html"}:
            raise ServiceError("INVALID_REPORT_FORMAT", "报告格式必须是 xlsx 或 html。")
        scan = self.load_scan(scan_id)
        if scan.status not in TERMINAL_STATUSES:
            raise ServiceError("REPORT_NOT_READY", "报告尚未生成。")
        path = Path(self.orchestrator.root) / "reports" / scan_id / f"report.{fmt}"
        if not path.exists():
            raise NotFoundError("REPORT_NOT_FOUND", "报告文件不存在。")
        return path.read_bytes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _scan_dir(self, scan_id: str) -> Path:
        if not isinstance(scan_id, str) or not re.fullmatch(r"scan_[A-Za-z0-9]+", scan_id):
            raise ServiceError("INVALID_SCAN_ID", "任务 ID 不合法。")
        return Path(self.orchestrator.root) / "results" / scan_id

    def _read_items(self, path: Path, model) -> list:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [model.model_validate(item) for item in payload.get("items", [])]
        except Exception as exc:
            raise ServiceError(
                "SCAN_DATA_CORRUPT",
                "任务数据文件无法读取，请重新选择历史任务。",
                {"path": str(path), "error_type": type(exc).__name__},
            ) from exc

    def _announcements(self, scan_id: str) -> list[Announcement]:
        return self._read_items(self._scan_dir(scan_id) / "announcements.json", Announcement)

    def _opportunities(self, scan_id: str) -> list[Opportunity]:
        items = self._read_items(self._scan_dir(scan_id) / "opportunities.json", Opportunity)
        return list({item.opportunity_id: item for item in items}.values())

    @staticmethod
    def _slice(items: list, limit: int | None, offset: int) -> list:
        if offset < 0:
            raise ServiceError("INVALID_PAGE", "分页偏移量不能为负数。")
        if limit is not None and limit < 0:
            raise ServiceError("INVALID_PAGE", "分页大小不能为负数。")
        if limit is None:
            return items[offset:]
        return items[offset : offset + limit]

    def _mock_agents(self):
        return (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())

    def _agents_for_scan(self, scan: Scan):
        if scan.analysis_mode == "mock":
            return self._mock_agents(), []
        if scan.analysis_mode == "live":
            try:
                client, agents = live_agents()
            except Exception as exc:
                raise ServiceError(
                    "LLM_CONFIG_MISSING",
                    "真实分析配置不完整，请设置 LLM_API_KEY、LLM_BASE_URL 和 LLM_MODEL。",
                ) from exc
            return agents, [client]
        raise ServiceError("ANALYSIS_MODE_UNKNOWN", "请先确认原任务使用的分析模式。")

    def _submit_scan(self, scan: Scan, retry_failed: bool = False, retry_sources: bool = False) -> None:
        agents, resources = self._agents_for_scan(scan)
        try:
            self.task_manager.submit(
                scan.scan_id,
                agents,
                resources=resources,
                retry_failed=retry_failed,
                retry_sources=retry_sources,
            )
        except Exception as exc:
            self._close_resources(resources)
            raise ServiceError("TASK_SUBMIT_FAILED", "任务提交失败，请稍后重试。") from exc

    @staticmethod
    def _close_resources(resources) -> None:
        for resource in resources or []:
            close = getattr(resource, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
