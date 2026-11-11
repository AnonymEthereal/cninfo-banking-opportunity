# Repository Guidelines

## Project Structure & Module Organization

This Python MVP converts CNINFO announcements into traceable banking opportunities and XLSX/HTML reports. Read `需求文档.md`, `SPEC.md`, and `TECH_DESIGN.md` before changing business behavior; record and resolve conflicts instead of silently choosing new requirements.

- `app.py`: Streamlit upload, scan controls, results, and downloads.
- `core/`: domain models, validation, configuration, calendar rules, storage, task management, and batch orchestration.
- `collectors/` and `parsers/`: CNINFO integration and document extraction.
- `agents/`: event extraction, classification, marketing analysis, and live-model adapters.
- `exporters/`: reports generated from the shared opportunity model.
- `tests/`: pytest unit and integration scenarios.
- `scripts/`: offline acceptance, live acceptance, diagnosis, and batch recovery commands.
- `data/`: runtime inputs, source documents, checkpoints, results, and reports; distinguish these artifacts from test fixtures.

## Build, Test, and Development Commands

Run commands from the repository root, preferably inside a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
python -m pytest -q
python scripts/acceptance.py
```

The last command runs offline fixture acceptance. Inspect live scripts before executing them: they can use historical windows, write artifacts, and incur configured LLM charges. There is no separate build step or configured formatter/linter.

## Coding Style & Naming Conventions

Use four-space indentation, UTF-8, `snake_case` functions/modules, `PascalCase` classes, and uppercase constants. Add type hints at service boundaries. Keep UI logic separate from collection and analysis. Use Pydantic models for structured data and stable error codes for failures. Preserve announcement IDs and source evidence; represent undisclosed amounts as `null`, never fabricated values.

## Testing Guidelines

Name tests `tests/test_*.py` and functions `test_*`. Use temporary directories and fake HTTP/LLM clients for deterministic tests. Cover failures, empty results, multiple events, checkpoint recovery, deduplication, and report consistency. No numeric coverage threshold is configured. Report live verification separately from fixture results; passing pytest alone does not establish MVP acceptance.

## Commit & Pull Request Guidelines

This checkout has no Git metadata, so no historical commit convention can be confirmed. Use concise imperative subjects, optionally prefixed with `feat:`, `fix:`, or `test:`. PRs should describe behavior changes, relevant specification references, validation performed, and remaining limitations. Include screenshots for UI changes and redact credentials from artifacts.

## Security & Configuration

Configuration precedence is environment variables, `.env`, then `.env.example`. Keep real credentials in local `.env` or environment variables; shared examples must contain placeholders. Never log keys or publish local configuration. Preserve previous acceptance batches and record provenance when retrying failures. Scan-only PDFs may remain explicit parsing failures within MVP scope.
