# Expected failure groups

Run with TestMind's generated module `tests/testmind/test_gen.py`
(`assert average(1, 0) == 0`). The golden test is
`tests/unit/failure_engine/test_analyzer.py`, and the fixture it reads
(`tests/unit/failure_engine/data/capture.json`) is re-checked against a live sandbox run
by `tests/sandbox/test_failure_capture.py`.

| Group | Category | Tests | Deterministic blame |
|---|---|---|---|
| `ZeroDivisionError` at `shop/stock.py:5` (`average`) | exception_in_code | `test_zero_division`, `test_zero_division_again`, `TestGroup.test_method`, generated `test_gen` | — |
| assertion at `tests/test_stock.py:47` | assertion_failure | `test_param[1]`, `test_param[2]` | — |
| assertion at `tests/test_stock.py:7` | assertion_failure | `test_assertion` | — |
| `JSONDecodeError` inside `json`, called from `shop/stock.py:9` | exception_in_library | `test_library` | — |
| `IndexError` at `tests/test_stock.py:24` | exception_in_test | `test_in_test` | — |
| `RuntimeError` (caused by `ValueError`) at `shop/stock.py:23` | exception_in_code | `test_chain` | — |
| `KeyError` in fixture `broken` | fixture_error | `test_fixture` | — |
| `xfail(strict=True)` passed | unexpected_pass | `test_strict_xpass` | test |
| `ModuleNotFoundError: not_installed_package` | import_error | `tests/test_missing.py` | environment |
| `SyntaxError` in `tests/test_syntax.py` | syntax_error | `tests/test_syntax.py` | test |
