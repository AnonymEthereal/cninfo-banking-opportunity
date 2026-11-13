from datetime import date
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from core.company_directory import QUICK_COMPANIES
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
    assert any(button.label == "下载 XLSX" for button in downloads)


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

