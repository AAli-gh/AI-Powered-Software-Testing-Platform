# Intentional defects

| Rule | Where | Defect |
|---|---|---|
| TM014 | `stock.API_TOKEN` | Hard-coded secret |
| TM040 | `stock.average_price` | Divides by `count` without validating it |
| TM020 | `stock.add_item` | Mutable default argument |
| TM041 | `stock.parse_quantity` | `int(raw)` without error handling |
| TM015 | `stock.restock` | `assert` used for input validation |
| TM021 | `stock.status_label` | `is` comparison with a string literal |
| TM022 | `stock.remove_item` | Unreachable `print` after `return` |
| TM023 | `stock.py` | `json`, `os` imported but unused |
| TM011 | `io.load_snapshot` | `pickle.loads` on caller data |
| TM013 | `io.load_config` | `yaml.load` without a safe Loader |
| TM016 | `io.fetch_prices` | `requests.get` without timeout |
| TM012 | `io.backup` | `subprocess.run(..., shell=True)` |
| TM010 | `io.evaluate_rule` | `eval` of a function argument (critical) |
| TM001 + TM002 | `io.read_quantity` | Bare `except:` that swallows everything |
| TM030 | `pricing.price_for` | Cyclomatic complexity > 10 |

Not defects (must **not** be reported): `io.safe_ratio` (division guarded by
`except ZeroDivisionError`), `pricing.apply_discount` (divides by a constant and validates input).
