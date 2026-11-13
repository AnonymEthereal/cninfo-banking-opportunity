from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.company_directory import QUICK_COMPANIES

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_app_renders_without_network_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("core.company_input_ui.cached_directory", lambda: QUICK_COMPANIES)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.run()
    assert not app.exception
    assert any(button.label == "检索公告" for button in app.button)
