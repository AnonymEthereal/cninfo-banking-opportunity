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

## Git & Version Management

Mental model: `commit` is a snapshot, `branch` is a parallel line of work, `remote` is backup/collaboration. "Rolling back" always means "which snapshot do we return to". This checkout tracks `origin` (`AnonymEthereal/cninfo-banking-opportunity`, branch `main`); all project changes must go through Git, and pushes to `main` trigger a Streamlit Community Cloud redeploy.

### Daily minimal flow

```powershell
git status              # what changed
git diff                # what exactly changed
git add -A              # stage everything (ignored files stay ignored)
git commit -m "描述改了什么"
git push
```

Commit in small steps: one commit does one thing, so rollback stays precise. After a verified change, report the commit hash and push result to the user.

### Working on optimizations

```powershell
git pull                       # sync first to avoid conflicts
git switch -c feature/优化xxx   # isolate the work from main
# ...edit code...
git add -A
git commit -m "优化：把 X 改成 Y"
git push -u origin feature/优化xxx
```

Open a Pull Request on GitHub to merge back into `main`, keeping the main branch always usable; discard a failed experiment by deleting its branch. Do not rebase, force-push, or amend already pushed commits unless explicitly asked.

### Rollback by how far things went

1. Changed but not staged/committed: `git restore .`
2. Staged but not committed: `git restore --staged .` then `git restore .`
3. Committed but not pushed: `git reset --soft HEAD~1` (keeps changes staged, preferred), `git reset --mixed HEAD~1` (keeps changes in the working tree), or `git reset --hard HEAD~1` (discards commit and changes; dangerous).
4. Already pushed: `git revert <commit-hash>` to create an inverse commit and preserve history. Never `git reset` + force-push a shared branch; `revert` is the safe option.

### Recovery and remotes

```powershell
git reflog                 # every HEAD movement, including reset-away commits
git reset --hard <hash>    # return to that snapshot
git remote -v              # remote URLs
git fetch && git pull      # fetch remote updates
git tag v1.0.0; git push --tags   # version tags for releases
```

As long as it was committed, `reflog` can almost always recover it. Summary: not committed → `restore`; committed but unpushed → `reset`; already pushed → `revert`; lost track → `reflog`.

### Commit hygiene

- Run `python -m pytest -q` (and offline `python scripts/acceptance.py` when behavior changes) before committing.
- Verify with `git status` that `data/`, `__pycache__/`, `.env`, `.streamlit/secrets.toml`, generated reports, and credentials are not staged.
- Use concise imperative subjects, optionally prefixed with `feat:`, `fix:`, `test:`, `docs:`, or `chore:`. Describe behavior changes, specification references, validation performed, and remaining limitations in the body when non-trivial.
- PRs should describe behavior changes, relevant specification references, validation performed, and remaining limitations. Include screenshots for UI changes and redact credentials from artifacts.

## Security & Configuration

Configuration precedence is environment variables, `.env`, then `.env.example`. Keep real credentials in local `.env` or environment variables; shared examples must contain placeholders. Never log keys or publish local configuration. Preserve previous acceptance batches and record provenance when retrying failures. Scan-only PDFs may remain explicit parsing failures within MVP scope.
