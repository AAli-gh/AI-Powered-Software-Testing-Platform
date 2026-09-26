# Code analyzer

Phase 3 of the pipeline. For every non-test Python module of a repository, the code analyzer
extracts **symbols** (modules, functions, methods, classes, parameters, imports), computes
**cyclomatic complexity**, and reports **findings** from a catalog of deterministic AST rules.

Like the [repository analyzer](repository-analyzer.md), it is fully deterministic: `ast` only.
It never imports or executes the analysed code, and it uses no LLM. Its output is the
structured context that later phases build on:

| Consumer | Uses |
|---|---|
| Test generation (Phase 4) | Public functions, signatures (kinds, annotations, defaults), `raises`, complexity |
| Bug detection / failure analysis (Phase 7) | Findings with evidence |
| Test selection (Phase 8) | Resolved imports per module (dependency graph) |

Code: `src/testmind/analyzers/code/`. Stored per run in `code_analyses.profile` (JSONB).

## Scope

- Input: the Python files found by the repository analyzer, read through the same
  symlink-safe, size-bounded reader.
- **Test files are excluded** (only counted as `test_modules_skipped`). The classification
  comes from Phase 2, so findings like `assert` usage or `eval` in a test fixture are not
  reported.
- Files that cannot be read or parsed become `issues`. The analysis still succeeds.

## Symbols

| Item | Details |
|---|---|
| Function / method | `qualname` (Python-style, e.g. `Order.total`, `outer.<locals>.inner`), async, method, **public** (no `_private` part in its path, not nested in a function; dunders are public), decorators, return annotation, first docstring line, line span, complexity + rank, exception names raised directly in its body |
| Parameter | name, kind (`positional_only`, `positional_or_keyword`, `var_positional`, `keyword_only`, `var_keyword`), annotation and default as source text |
| Class | qualname, bases, decorators, docstring, method names |
| Module | dotted module name (`src/` layout dropped), line count, docstring, imports with **relative imports resolved** to absolute names |

## Cyclomatic complexity

McCabe complexity = 1 + decision points in the function's own body. Decision points are
`if`/`elif`, `for`, `while`, `except`, conditional expressions, each extra `and`/`or` operand,
each comprehension `for` and `if`, and each `case`. Nested functions, classes and lambdas are
measured separately.

Ranks follow the widely used scale: **A** 1–5, **B** 6–10, **C** 11–20, **D** 21–30,
**E** 31–40, **F** 41+.

## Rules

Every finding has a stable `rule_id`, a category, a severity, a **confidence**, the enclosing
symbol and the offending source line as **evidence**. The catalog is also served by
`GET /api/v1/code-rules`.

| Rule | Category | Severity | Confidence | Detects |
|---|---|---|---|---|
| TM001 | error_handling | medium | 1.0 | Bare `except:` |
| TM002 | error_handling | high (broad) / low | 1.0 | Handler whose body is only `pass`/`...`/a string (exception swallowed) |
| TM003 | error_handling | low | 0.5 | `except Exception` that neither re-raises nor logs |
| TM010 | dangerous_pattern | **critical** if the code comes from a parameter, else high | 1.0 | `eval` / `exec` (builtins only, not shadowed by imports) |
| TM011 | dangerous_pattern | high | 1.0 | `pickle`/`marshal`/`dill` loads, `shelve.open`, `jsonpickle.decode` |
| TM012 | dangerous_pattern | high | 1.0 | `subprocess.*(shell=True)`, `os.system`, `os.popen` |
| TM013 | dangerous_pattern | high | 1.0 | `yaml.load` without a safe loader, `yaml.unsafe_load` |
| TM014 | dangerous_pattern | high | 0.6 | String literal (≥ 8 chars, no spaces) assigned to `*password`, `*secret`, `*token`, `*api_key`, … |
| TM015 | potential_bug | low | 0.7 | `assert` in production code (removed by `python -O`) |
| TM016 | potential_bug | medium | 1.0 | `requests.*()` without `timeout` (and without `**kwargs`) |
| TM017 | dangerous_pattern | medium | 1.0 | `tempfile.mktemp` |
| TM020 | potential_bug | medium | 1.0 | Mutable default argument (`[]`, `{}`, `set()`, `dict()`, …) |
| TM021 | potential_bug | medium | 1.0 | `is` / `is not` with a literal |
| TM022 | dead_code | medium | 1.0 | Statements after `return` / `raise` / `continue` / `break` |
| TM023 | dead_code | low | 0.9 | Unused module-level import (not in `__init__.py`, not guarded by `try`/`if`, `import x as x` re-exports and `__all__` respected) |
| TM024 | dead_code | low | 0.6 | Undecorated private module function never referenced in its module |
| TM030 | complexity | medium (> 10) / high (> 20) | 1.0 | High cyclomatic complexity |
| TM040 | missing_validation | medium | **0.5** | Parameter used as a divisor without appearing in any condition, assert or `ZeroDivisionError` handler |
| TM041 | missing_validation | low | **0.4** | `int()`, `float()`, `json.loads()`, `uuid.UUID()`, `fromisoformat()` … on an unchecked parameter outside a matching `try` |

**Heuristics are labelled as such.** TM040 and TM041 cannot know every caller's guarantees,
so their confidence is at most 0.5 and their message says *possible*. Callers resolve names
through the module's imports (`from subprocess import run as r` → `subprocess.run`).

### Suppressing a finding

```python
result = pickle.loads(trusted_blob)  # testmind: ignore[TM011]
```

`# testmind: ignore` (all rules) and `# testmind: ignore[TM011,TM016]` are supported on the
reported line. A `# noqa` for the equivalent flake8/ruff code is also honoured (TM023↔F401,
TM001↔E722, TM020↔B006, TM010↔S307).

## Verification

- Every rule has positive **and** negative tests (`tests/unit/analyzers/code/test_rules.py`).
- [`examples/buggy_inventory`](../examples/buggy_inventory) contains one instance of each
  defect, listed in its `EXPECTED_FINDINGS.md`. A golden test parses that file and requires
  the analyzer to report **exactly** those `(rule, module)` pairs, while also checking that
  deliberately safe code in the same fixture is not reported.
- The four clean example repositories must produce zero findings.
- The analyzer is run on TestMind itself (dogfooding). Doing so found three false positives
  before release, and each is now a regression test: `pathlib`'s `/` treated as division,
  `# noqa: F401` side-effect imports, and handlers that log or `continue`. It also found two
  real bugs: nested functions were counted into their parent's complexity, and
  `str.splitlines()` shifted line numbers on files containing `\x0c`/` `.

## Not covered yet

- Cross-module analysis (unused public functions, call graph). The per-module import graph
  is recorded for Phase 8.
- Type inference: rules only see what the syntax says (annotations help TM040).
- Non-Python languages.
