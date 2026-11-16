import os
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from core.company_directory import QUICK_COMPANIES
from core.company_meta import DEFAULT_STATUSES, filter_companies, load_snapshot
from core.models import Announcement, Company, DateRange, Opportunity
from core.runtime import RuntimeLock
from core.scanner import ScanOrchestrator
from core.storage import atomic_write_json
from exporters.excel_exporter import export_xlsx
from exporters.html_exporter import export_html

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class NoopCollector:
    def collect(self, company, date_range):
        return []


def test_app_renders_without_network_directory(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    assert not app.exception
    assert any(button.label == "检索公告" for button in app.button)
    # The analysis mode is chosen after collection, not before it.
    assert not [
        control
        for control in app.get("segmented_control")
        if any("演示" in str(option) for option in (control.options or []))
    ]


def test_category_filter_adds_matching_companies(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    expected = filter_companies(
        load_snapshot(), boards=["北交所"], statuses=list(DEFAULT_STATUSES)
    )
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    assert not app.exception
    assert {tab.label for tab in app.tabs} >= {"搜索添加", "分类筛选", "批量粘贴", "Excel 导入"}

    app.multiselect(key="meta_boards").select("北交所").run()
    assert not app.exception
    add_all = next(button for button in app.button if button.key == "meta_add_all")
    assert f"{len(expected):,}" in add_all.label
    add_all.click().run()
    assert not app.exception
    selected = app.session_state["selected_companies"]
    assert len(selected) == len(expected)
    assert {item["stock_code"] for item in selected} == {item.stock_code for item in expected}


def test_category_filter_explains_directory_size_gap(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    directory = [
        Company(stock_code=f"{index:06d}", company_name=f"企业{index}")
        for index in range(7000)
    ]
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: directory)
    app = AppTest.from_file(str(APP_PATH), default_timeout=60)
    app.run()
    assert not app.exception
    # The searchable directory includes B shares, CDRs and NEEQ names, so the
    # picker states why its A-share count is smaller.
    assert any("B 股" in caption.value and "7,000" in caption.value for caption in app.caption)


def test_app_renders_completed_scan_and_reports(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = "completed"
    scan.analysis_mode = "mock"
    scan.counts.announcements = 0
    runner.save(scan)
    report_dir = tmp_path / "reports" / scan.scan_id
    export_xlsx([], scan, report_dir / "report.xlsx")
    export_html([], scan, report_dir / "report.html")

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["scan_id"] = scan.scan_id
    app.run()
    assert not app.exception
    assert any(metric.label == "企业" for metric in app.metric)
    downloads = app.get("download_button")
    assert any(button.label.startswith("下载 XLSX") for button in downloads)
    assert any(button.label.startswith("下载 HTML") for button in downloads)
    # The HTML report opens inside the page instead of only being downloadable.
    preview = app.toggle(key=f"preview_html_{scan.scan_id}")
    assert preview.value is True
    preview.set_value(False).run()
    assert not app.exception


def test_app_renders_running_scan_fragment(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = "collecting"
    scan.analysis_mode = "mock"
    runner.save(scan)
    lock = RuntimeLock(tmp_path)
    assert lock.acquire(scan.scan_id)
    try:
        app = AppTest.from_file(str(APP_PATH), default_timeout=30)
        app.session_state["scan_id"] = scan.scan_id
        app.run()
        assert not app.exception
        assert any(metric.label == "企业" for metric in app.metric)
    finally:
        lock.release(scan.scan_id)


def seed_scan(tmp_path, status: str, opportunities: int = 0):
    runner = ScanOrchestrator(NoopCollector(), tmp_path)
    scan = runner.create(
        [Company(stock_code="1", company_name="A")],
        DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
    )
    scan.status = status
    scan.analysis_mode = "mock"
    scan.counts.announcements = 1
    scan.counts.opportunities = opportunities
    runner.save(scan)
    folder = tmp_path / "results" / scan.scan_id
    atomic_write_json(
        folder / "announcements.json",
        {
            "items": [
                Announcement(
                    announcement_id="ann-1",
                    stock_code="000001",
                    company_name="甲公司",
                    title="采购项目公告",
                    publish_date=date(2026, 1, 1),
                    source_url="https://example.com/ann",
                ).model_dump()
            ]
        },
    )
    items = [
        Opportunity(
            opportunity_id=f"opp-{index}",
            announcement_id="ann-1",
            stock_code="000001",
            company_name="甲公司" if index == 0 else "乙公司",
            publish_date=date(2026, 1, 1),
            announcement_title="采购项目公告",
            category="采购类" if index == 0 else "投资并购类",
            event_summary=f"事件 {index}",
            source_url="https://example.com/ann",
            analysis_status="completed",
        ).model_dump()
        for index in range(opportunities)
    ]
    atomic_write_json(folder / "opportunities.json", {"items": items})
    return runner, scan


def test_app_renders_opportunity_browser(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    _, scan = seed_scan(tmp_path, "completed", opportunities=2)

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["scan_id"] = scan.scan_id
    app.run()
    assert not app.exception
    assert app.dataframe, "opportunity table should render"
    assert len(app.selectbox) >= 2, "category and status filters should render"
    assert any(metric.label == "商机" for metric in app.metric)
    assert any(button.label.startswith("重新分析") for button in app.button)


def test_app_renders_review_confirmation_controls(tmp_path, monkeypatch):
    st.cache_resource.clear()
    st.cache_data.clear()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    _, scan = seed_scan(tmp_path, "awaiting_review", opportunities=0)

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["scan_id"] = scan.scan_id
    app.run()
    assert not app.exception
    assert any(button.label.startswith("确认并开始分析") for button in app.button)
    assert app.get("segmented_control"), "analysis mode selector should render"


def test_app_reloads_stale_backend_modules(tmp_path):
    """A deploy that leaves old ``core`` modules loaded must self-heal.

    Runs in a subprocess: ``importlib.reload`` swaps module classes, which would
    otherwise leak into the other tests running in this process.
    """
    env = dict(os.environ, DATA_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    script = textwrap.dedent(
        """
        from datetime import date

        import httpx
        from streamlit.testing.v1 import AppTest

        from core.models import Announcement, Company, DateRange, Opportunity
        from core.scanner import ScanOrchestrator
        from core.storage import atomic_write_json


        class NoopCollector:
            def collect(self, company, date_range):
                return []


        runner = ScanOrchestrator(NoopCollector())
        scan = runner.create(
            [Company(stock_code="1", company_name="A")],
            DateRange(start_date=date(2026, 1, 1), end_date=date(2026, 1, 2)),
        )
        scan.status = "completed"
        scan.analysis_mode = "mock"
        scan.counts.announcements = 1
        scan.counts.opportunities = 1
        runner.save(scan)
        folder = runner.root / "results" / scan.scan_id
        atomic_write_json(
            folder / "announcements.json",
            {
                "items": [
                    Announcement(
                        announcement_id="ann-1",
                        stock_code="000001",
                        company_name="甲公司",
                        title="采购项目公告",
                        publish_date=date(2026, 1, 1),
                        source_url="https://example.com/ann",
                    ).model_dump()
                ]
            },
        )
        atomic_write_json(
            folder / "opportunities.json",
            {
                "items": [
                    Opportunity(
                        opportunity_id="opp-1",
                        announcement_id="ann-1",
                        stock_code="000001",
                        company_name="甲公司",
                        publish_date=date(2026, 1, 1),
                        announcement_title="采购项目公告",
                        category="采购类",
                        event_summary="事件 0",
                        source_url="https://example.com/ann",
                        analysis_status="completed",
                    ).model_dump()
                ]
            },
        )

        import core.app_service as app_service_module

        original = app_service_module.AppService.list_opportunities

        def legacy_list_opportunities(
            self, scan_id, categories=None, companies=None, statuses=None, limit=None, offset=0
        ):
            return original(
                self,
                scan_id,
                categories=categories,
                companies=companies,
                statuses=statuses,
                limit=limit,
                offset=offset,
            )

        # Drop the `query` parameter to mimic a class cached by the previous build.
        app_service_module.AppService.list_opportunities = legacy_list_opportunities

        original_confirm = app_service_module.AppService.confirm_analysis

        def legacy_confirm_analysis(self, scan_id, per_minutes=None, batch_hours=None):
            return original_confirm(self, scan_id, per_minutes, batch_hours)

        # A previous build also lacked the `mode` argument and the version marker.
        del app_service_module.AppService.API_VERSION
        app_service_module.AppService.confirm_analysis = legacy_confirm_analysis


        class OfflineClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def close(self):
                return None

            def get(self, *args, **kwargs):
                raise RuntimeError("network disabled in tests")

            def post(self, *args, **kwargs):
                raise RuntimeError("network disabled in tests")


        httpx.Client = OfflineClient

        app = AppTest.from_file("app.py", default_timeout=60)
        app.session_state["scan_id"] = scan.scan_id
        app.run()
        assert not app.exception, app.exception
        assert app.dataframe, "opportunity table should render after reload"
        assert any(metric.label == "商机" for metric in app.metric)
        print("RELOAD_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(APP_PATH.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELOAD_OK" in result.stdout


