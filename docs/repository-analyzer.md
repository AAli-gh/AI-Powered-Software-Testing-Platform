# Repository analyzer

Phase 2 of the pipeline. Given a repository directory, the analyzer produces a structured
**profile**: languages, declared dependencies, frameworks, test setup, entry points,
configuration, Docker and CI/CD.

It is fully **deterministic**. It does not use an LLM, it never imports or executes code from the
repository, it makes no network calls and starts no subprocesses. The same files always produce
the same profile.

Code: `src/testmind/analyzers/repository/`. Stored per run in `repository_analyses.profile`
(JSONB) and served by `GET /api/v1/runs/{id}/analysis` and `…/analysis/files`.

## Pipeline

```text
walker ──► files (safe inventory)
  │
  ├─► languages        extension / file name → language, line counts, primary language
  ├─► python_scanner   ast.parse each .py once → imports, app objects, __main__, TestCase, test_*
  ├─► dependencies     pyproject / requirements / setup.cfg / setup.py / Pipfile
  ├─► frameworks       weighted evidence → Django, FastAPI, Flask, pytest, unittest
  ├─► entry_points     console scripts, manage.py, __main__.py, ASGI/WSGI apps, scripts
  └─► infrastructure   config files, Dockerfiles (FROM + ARG), compose services, CI jobs
```

| Module | Responsibility |
|---|---|
| `walker.py` | Traversal and bounded, symlink-safe reads |
| `languages.py` | Language mapping and statistics |
| `python_scanner.py` | AST facts per Python file |
| `dependencies.py` | Declared dependencies from every supported manifest |
| `frameworks.py` | Framework/test-framework detection and test-file classification |
| `entry_points.py` | How the project can be started |
| `infrastructure.py` | Config files, Docker, CI/CD |
| `analyzer.py` | Orchestration, `ANALYZER_VERSION` |
| `models.py` | The Pydantic result model (`RepositoryProfile`) |

## What is detected, and how

### Files and languages
- Every regular file is listed with its size, language and line count. Binary files and
  files over `max_read_bytes` are listed without being read.
- Skipped: `.git`, virtualenvs, `node_modules`, `__pycache__`, build/dist output, tool caches,
  `*.egg-info`, and anything matched by the root `.gitignore` (Git's own matching rules).
- The **primary language** is the programming language with the most lines. Markup and data
  formats (Markdown, YAML, JSON, TOML, …) never count as primary.

### Dependencies

| Source | Groups |
|---|---|
| `pyproject.toml` `[project] dependencies` | runtime |
| `[project.optional-dependencies]` | optional (`dev`/`test`/`lint`… → development) |
| `[dependency-groups]` (PEP 735) | same naming rule. `{include-group}` tables are skipped |
| `[tool.poetry.*]` dependencies, dev-dependencies, groups | runtime / development / by group name |
| `requirements*.txt`, `*-requirements.txt`, `*_requirements.txt`, `requirements/*.txt` | by file name (`-dev`, `test`, `ci`, … → development) |
| `setup.cfg` `install_requires`, `extras_require` | runtime / by extra name |
| `setup.py` | only **literal** `install_requires` / `extras_require` (lists, tuples or sets of strings). A dynamic value is reported as an issue and never executed |
| `Pipfile` | `packages` → runtime, `dev-packages` → development |

- Names are normalised (PEP 503) and extras and markers are kept.
- `-r` / `--requirement` includes are followed inside the repository only. Absolute paths,
  URLs and `..` escapes are refused. Cycles and depth are bounded.
- An included file keeps **its own** group: `-r requirements.txt` inside
  `requirements-dev.txt` contributes runtime dependencies.

### Frameworks: evidence and confidence

Each detection lists the concrete facts that support it. Confidence is the capped sum of the
weights of those facts, so no single weak signal gives high confidence:

| Fact | Weight |
|---|---|
| Declared dependency | 0.5 |
| Imported in project code | 0.3 |
| Structural signal (see below) | 0.4 |

Detections below 0.3 are not reported.

| Framework | Structural signals |
|---|---|
| Django | `DJANGO_SETTINGS_MODULE` **assigned** (`environ.setdefault(...)` / item assignment), module-level `INSTALLED_APPS`, `get_wsgi_application()` / `get_asgi_application()` |
| FastAPI | `FastAPI(...)` instance |
| Flask | `Flask(...)` instance (module level or inside an app factory) |
| pytest | `pytest.ini`, `conftest.py`, `[tool.pytest…]`, `[tool:pytest]`, tox `[pytest]`, plus module-level `test_*` functions in test files (+0.3) |
| unittest | `import unittest` (+0.3) and `…TestCase` subclasses in test files (+0.7) |

Structural signals come from the AST, not from text search. A file that only *mentions*
`"DJANGO_SETTINGS_MODULE"` in a string does not count. This was a real false positive found
by running the analyzer on TestMind itself, and it now has a regression test.

### Test files
A Python file is a test file if its name is `test_*.py`, `*_test.py` or `tests.py`, or if it
lives under a `test/` or `tests/` directory and contains `TestCase` subclasses or module-level
`test_*` functions. `conftest.py` and `__init__.py` are never test files.

### Entry points

| Kind | Source |
|---|---|
| `console_script` | `[project.scripts]`, `[tool.poetry.scripts]`, `setup.cfg` `console_scripts` |
| `django_manage` | `manage.py` that sets `DJANGO_SETTINGS_MODULE` |
| `package_main` | `pkg/__main__.py` (target `pkg`) |
| `script_main` | Other non-test modules with `if __name__ == "__main__":` |
| `asgi_app` | `FastAPI()`, `Starlette()`, `get_asgi_application()` → `module:attr` |
| `wsgi_app` | `Flask()`, `get_wsgi_application()` → `module:attr`, or `module:factory()` for app factories |

A leading `src/` directory is dropped from module paths (`src/todo/main.py` → `todo.main`).

### Infrastructure
- **Config files:** a curated list of well-known names (`pyproject.toml`, `tox.ini`,
  `.pre-commit-config.yaml`, `alembic.ini`, lock files, …).
- **Docker:** Dockerfiles (`Dockerfile*`, `*.dockerfile`), base images from `FROM` with `ARG`
  defaults substituted. Build-stage aliases and `scratch` are excluded. Services from all
  Compose files.
- **CI/CD:** GitHub Actions (jobs), GitLab CI (jobs, excluding reserved keys and hidden `.jobs`),
  CircleCI (jobs), Jenkins, Azure Pipelines, Travis CI, Bitbucket Pipelines.

### Issues
Problems that do not stop the analysis are collected in `issues`. Examples: syntax errors,
invalid TOML/YAML/INI, invalid requirement lines, dynamic `setup.py` values, refused includes,
and files too large to analyse. An analysis with issues still succeeds.

## Safety and limits

| Concern | Control |
|---|---|
| Executing hostile code | Nothing is imported or run. Python is parsed with `ast`, `setup.py` values are read with `ast.literal_eval`, and YAML uses `yaml.safe_load` (a test proves `!!python/object` tags are rejected) |
| Escaping the repository | Symlinks are never followed or listed. Files are opened with `O_NOFOLLOW` (a file swapped for a symlink after listing is refused). Includes cannot leave the repository |
| Resource exhaustion | `AnalysisLimits`: `max_files` (20 000, sets `truncated`), `max_read_bytes` per file (1 MB), `max_depth` (32). A file that grows past the limit after listing is not read |
| Encoding | Invalid UTF-8 is replaced, never fatal. NUL bytes mark a file as binary |

## Output and versioning

`ANALYZER_VERSION` (currently `1.0.0`) is stored with every analysis, so profiles produced by
different analyzer versions can be told apart when comparing runs (regression analysis,
Phase 8).

## Not covered yet (by design)

- API endpoint discovery (Phase 10).
- Functions, classes, complexity and static analysis (Phase 3, the code analyzer).
- Framework detection for non-Python ecosystems (languages are still counted).
- Nested `.gitignore` files. Only the root one is applied.
