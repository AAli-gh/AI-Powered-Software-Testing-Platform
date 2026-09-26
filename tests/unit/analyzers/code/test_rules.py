from pathlib import Path

import pytest

from testmind.analyzers.code.models import FindingCategory, Severity
from testmind.analyzers.code.rules import RULES
from tests.unit.analyzers.code.helpers import analyze_source, findings, rule_ids


# ------------------------------------------------------------------ dangerous patterns
@pytest.mark.parametrize(
    ("source", "severity"),
    [
        ("def run(code):\n    return eval(code)\n", Severity.CRITICAL),
        ("def run():\n    exec('x = 1')\n", Severity.HIGH),
        ("eval(open('f').read())\n", Severity.HIGH),
    ],
)
def test_eval_exec(tmp_path: Path, source: str, severity: Severity) -> None:
    [finding] = findings(tmp_path, source, "TM010")
    assert finding.severity is severity
    assert finding.category is FindingCategory.DANGEROUS_PATTERN


def test_eval_shadowed_by_import_is_not_builtin(tmp_path: Path) -> None:
    assert rule_ids(tmp_path, "from mylib import eval\neval('x')\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "import pickle\npickle.loads(b)\n",
        "import pickle as p\np.load(f)\n",
        "from pickle import loads\nloads(b)\n",
        "import marshal\nmarshal.loads(b)\n",
    ],
)
def test_unsafe_deserialisation(tmp_path: Path, source: str) -> None:
    assert rule_ids(tmp_path, source) == ["TM011"]


def test_pickle_dumps_is_fine(tmp_path: Path) -> None:
    assert rule_ids(tmp_path, "import pickle\npickle.dumps(x)\n") == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(cmd, shell=True)\n",
        "import subprocess as sp\nsp.check_output(cmd, shell=True)\n",
        "import os\nos.system('ls')\n",
        "from os import popen\npopen('ls')\n",
    ],
)
def test_shell_execution(tmp_path: Path, source: str) -> None:
    assert rule_ids(tmp_path, source) == ["TM012"]


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['ls'])\n",
        "import subprocess\nsubprocess.run(cmd, shell=False)\n",
        "import subprocess\nsubprocess.run(cmd, shell=flag)\n",
    ],
)
def test_subprocess_without_shell_true_is_fine(tmp_path: Path, source: str) -> None:
    assert rule_ids(tmp_path, source) == []


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("import yaml\nyaml.load(s)\n", True),
        ("import yaml\nyaml.load(s, Loader=yaml.FullLoader)\n", True),
        ("import yaml\nyaml.unsafe_load(s)\n", True),
        ("import yaml\nyaml.load(s, Loader=yaml.SafeLoader)\n", False),
        ("import yaml\nyaml.load(s, yaml.CSafeLoader)\n", False),
        ("import yaml\nyaml.safe_load(s)\n", False),
    ],
)
def test_yaml_load(tmp_path: Path, source: str, flagged: bool) -> None:
    assert (rule_ids(tmp_path, source) == ["TM013"]) is flagged


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("import requests\nrequests.get(url)\n", True),
        ("import requests\nrequests.request('GET', url)\n", True),
        ("import requests\nrequests.get(url, timeout=5)\n", False),
        ("import requests\nrequests.post(url, **options)\n", False),
        ("import httpx\nhttpx.get(url)\n", False),
    ],
)
def test_requests_timeout(tmp_path: Path, source: str, flagged: bool) -> None:
    assert (rule_ids(tmp_path, source) == ["TM016"]) is flagged


def test_mktemp(tmp_path: Path) -> None:
    assert rule_ids(tmp_path, "import tempfile\ntempfile.mktemp()\n") == ["TM017"]


@pytest.mark.parametrize(
    "source",
    [
        'API_KEY = "placeholder-value"\n',
        'db_password = "hunter2hunter2"\n',
        'settings.SECRET = "placeholder-value"\n',
        'token: str = "placeholder-value"\n',
    ],
)
def test_hard_coded_secret(tmp_path: Path, source: str) -> None:
    [finding] = findings(tmp_path, source, "TM014")
    assert finding.confidence == 0.6


@pytest.mark.parametrize(
    "source",
    [
        'PASSWORD = os.environ["PASSWORD"]\n',
        'TOKEN_HEADER = "Authorization"\n',
        'password = "short"\n',
        'secret = "a phrase with spaces"\n',
        'secret = ""\n',
        "api_key = 12345678901\n",
        'key_path = "/etc/ssl/private.pem"\n',
    ],
)
def test_not_a_hard_coded_secret(tmp_path: Path, source: str) -> None:
    assert findings(tmp_path, source, "TM014") == []


# ------------------------------------------------------------------ error handling
def test_bare_except_that_swallows(tmp_path: Path) -> None:
    source = "try:\n    f()\nexcept:\n    pass\n"
    result = {f.rule_id: f.severity for f in analyze_source(tmp_path, source).findings}
    assert result == {"TM001": Severity.MEDIUM, "TM002": Severity.HIGH}


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        ("except Exception:\n    pass", {"TM002": Severity.HIGH}),
        ("except (ValueError, BaseException):\n    ...", {"TM002": Severity.HIGH}),
        ("except KeyError:\n    pass", {"TM002": Severity.LOW}),
        ('except KeyError:\n    """ignored on purpose"""', {"TM002": Severity.LOW}),
        ("except Exception:\n    return None", {"TM003": Severity.LOW}),
        ("except Exception as exc:\n    raise Wrapped() from exc", {}),
        ("except Exception:\n    logger.exception('failed')", {}),
        ("except OSError:\n    continue", {}),
        ("except KeyError:\n    value = None", {}),
    ],
)
def test_exception_handlers(tmp_path: Path, handler: str, expected: dict[str, Severity]) -> None:
    source = f"def f(xs):\n    for x in xs:\n        try:\n            g()\n{_indent(handler, 8)}\n"
    result = {f.rule_id: f.severity for f in analyze_source(tmp_path, source).findings}
    assert result == expected


def _indent(text: str, spaces: int) -> str:
    lines = text.splitlines()
    return "\n".join(
        [" " * spaces + lines[0], *(" " * (spaces + 4) + line.strip() for line in lines[1:])]
    )


# ------------------------------------------------------------------ potential bugs
@pytest.mark.parametrize(
    "signature",
    ["def f(a=[]): pass", "def f(a={}): pass", "def f(*, a=set()): pass", "def f(a=dict()): pass",
     "def f(a={1}): pass"],
)  # fmt: skip
def test_mutable_default(tmp_path: Path, signature: str) -> None:
    assert rule_ids(tmp_path, signature + "\n") == ["TM020"]


@pytest.mark.parametrize(
    "signature", ["def f(a=None): pass", "def f(a=()): pass", "def f(*, a=frozenset()): pass"]
)
def test_immutable_default_is_fine(tmp_path: Path, signature: str) -> None:
    assert rule_ids(tmp_path, signature + "\n") == []


@pytest.mark.parametrize(
    ("expression", "flagged"),
    [
        ("x is 'a'", True), ("x is not 1", True), ("x is []", True), ("x is f'{y}'", True),
        ("x is None", False), ("x is True", False), ("x is ...", False), ("x is y", False),
        ("x == 'a'", False),
    ],
)  # fmt: skip
def test_identity_with_literal(tmp_path: Path, expression: str, flagged: bool) -> None:
    assert (rule_ids(tmp_path, f"result = {expression}\n") == ["TM021"]) is flagged


def test_assert_in_production_code(tmp_path: Path) -> None:
    [finding] = findings(tmp_path, "def f(x):\n    assert x > 0\n", "TM015")
    assert finding.severity is Severity.LOW


# ------------------------------------------------------------------ dead code
@pytest.mark.parametrize("terminator", ["return 1", "raise ValueError", "continue", "break"])
def test_unreachable_code(tmp_path: Path, terminator: str) -> None:
    source = f"def f(xs):\n    for x in xs:\n        {terminator}\n        print(x)\n"
    [finding] = findings(tmp_path, source, "TM022")
    assert finding.line == 4
    assert finding.symbol == "f"


def test_code_after_conditional_return_is_reachable(tmp_path: Path) -> None:
    source = "def f(x):\n    if x:\n        return 1\n    return 2\n"
    assert findings(tmp_path, source, "TM022") == []


def test_unused_imports(tmp_path: Path) -> None:
    source = """
    from __future__ import annotations
    import os
    import os.path
    import json as js
    from typing import Any, TYPE_CHECKING
    from x import used_in_all, used_in_annotation, re_exported as re_exported
    from y import *

    __all__ = ["used_in_all"]

    def f(a: "used_in_annotation") -> Any:
        return os.getcwd()
    """
    messages = [f.message for f in findings(tmp_path, source, "TM023")]
    assert messages == [
        "'json' is imported but never used",
        "'TYPE_CHECKING' is imported but never used",
    ]


def test_guarded_imports_and_package_init_are_not_reported(tmp_path: Path) -> None:
    guarded = "try:\n    import ujson\nexcept ImportError:\n    ujson = None\n"
    assert findings(tmp_path, guarded, "TM023") == []
    assert rule_ids(tmp_path, "import os\n", path="pkg/__init__.py") == []


def test_invalid_strings_do_not_break_name_collection(tmp_path: Path) -> None:
    source = 'import os\nA = "not ( valid"\nB = "' + "x" * 300 + '"\nC = "  "\n'
    assert [f.rule_id for f in findings(tmp_path, source, "TM023")] == ["TM023"]


def test_unused_private_function(tmp_path: Path) -> None:
    source = """
    def _unused(): pass
    def _used(): pass
    @register
    def _registered(): pass
    def __dunder__(): pass
    def public(): return _used()
    """
    assert [f.message for f in findings(tmp_path, source, "TM024")] == [
        "Private function _unused() is never used in its module"
    ]


# ------------------------------------------------------------------ complexity
def test_high_complexity(tmp_path: Path) -> None:
    branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(25))
    medium = "\n".join(f"    if y == {i}:\n        return {i}" for i in range(10))
    source = f"def big(x):\n{branches}\n    return -1\n\ndef mid(y):\n{medium}\n    return -1\n"
    result = {f.symbol: (f.severity, f.message) for f in findings(tmp_path, source, "TM030")}
    assert result == {
        "big": (
            Severity.HIGH,
            "big() has cyclomatic complexity 26 (> 10); hard to test exhaustively",
        ),
        "mid": (
            Severity.MEDIUM,
            "mid() has cyclomatic complexity 11 (> 10); hard to test exhaustively",
        ),
    }


# ------------------------------------------------------------------ missing validation
@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("def avg(total, count):\n    return total / count\n", True),
        ("def f(a, b: int):\n    return a // b\n", True),
        ("def f(a, b: float | None):\n    return a % b\n", True),
        ("def f(a, b):\n    if b == 0:\n        return 0\n    return a / b\n", False),
        ("def f(a, b):\n    if not b:\n        return 0\n    return a / b\n", False),
        ("def f(a, b):\n    return a / b if b else 0\n", False),
        ("def f(a, b):\n    assert b\n    return a / b\n", False),
        (
            "def f(a, b):\n    try:\n        return a / b\n"
            "    except ZeroDivisionError:\n        return 0\n",
            False,
        ),
        ("def f(root: Path, name: str):\n    return root / name\n", False),
        ("def f(root: Path, name):\n    return root / name\n", False),
        ("def f(a):\n    return a / 100\n", False),
        ("def f(name):\n    return 'hello %s' % name\n", False),
        ("class K:\n    def f(self, other):\n        return self / other\n", True),
    ],
)
def test_division_by_parameter(tmp_path: Path, source: str, flagged: bool) -> None:
    assert (len(findings(tmp_path, source, "TM040")) == 1) is flagged


def test_division_rule_reports_the_parameter(tmp_path: Path) -> None:
    [finding] = findings(tmp_path, "def avg(total, count):\n    return total / count\n", "TM040")
    assert finding.message.startswith("'count' is used as a divisor")
    assert finding.confidence == 0.5
    assert finding.symbol == "avg"
    assert finding.evidence == "return total / count"


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("def f(raw):\n    return int(raw)\n", True),
        ("import json\ndef f(body):\n    return json.loads(body)\n", True),
        ("from uuid import UUID\ndef f(v):\n    return UUID(v)\n", True),
        (
            "def f(raw):\n    try:\n        return int(raw)\n"
            "    except ValueError:\n        return 0\n",
            False,
        ),
        ("def f(raw):\n    if raw.isdigit():\n        return int(raw)\n    return 0\n", False),
        ("def f(raw):\n    return int(raw.strip())\n", False),
        ("def f():\n    return int('3')\n", False),
    ],
)
def test_unguarded_conversion(tmp_path: Path, source: str, flagged: bool) -> None:
    assert (len(findings(tmp_path, source, "TM041")) == 1) is flagged


def test_match_guard_counts_as_validation(tmp_path: Path) -> None:
    source = "def f(a, b):\n    match a:\n        case 1 if b:\n            return a / b\n"
    assert findings(tmp_path, source, "TM040") == []


# ------------------------------------------------------------------ suppression
@pytest.mark.parametrize(
    ("comment", "suppressed"),
    [
        ("# testmind: ignore", True),
        ("# testmind: ignore[TM011]", True),
        ("# testmind: ignore[TM010, TM011]", True),
        ("# testmind: ignore[TM010]", False),
        ("# noqa", False),  # TM011 has no flake8 equivalent
    ],
)
def test_ignore_comments(tmp_path: Path, comment: str, suppressed: bool) -> None:
    source = f"import pickle\npickle.loads(b)  {comment}\n"
    assert (findings(tmp_path, source, "TM011") == []) is suppressed


@pytest.mark.parametrize(
    ("comment", "suppressed"),
    [("# noqa", True), ("# noqa: F401", True), ("# NOQA:E501,F401", True), ("# noqa: E501", False)],
)
def test_noqa_for_equivalent_rules(tmp_path: Path, comment: str, suppressed: bool) -> None:
    assert (findings(tmp_path, f"import os  {comment}\n", "TM023") == []) is suppressed


def test_every_reported_rule_is_in_the_catalog() -> None:
    assert all(rule_id.startswith("TM") and len(rule_id) == 5 for rule_id in RULES)


def test_line_numbers_match_python_despite_unusual_line_breaks(tmp_path: Path) -> None:
    """Regression: str.splitlines() treats \\x0c and \\u2028 as line breaks; Python does not."""
    source = 'MARKER = "a\u2028b"  # \x0c form feed\nimport pickle\npickle.loads(b)\n'
    [finding] = findings(tmp_path, source, "TM011")
    assert finding.line == 3
    assert finding.evidence == "pickle.loads(b)"


def test_handler_with_computed_exception_type(tmp_path: Path) -> None:
    source = "try:\n    f()\nexcept errors()[0]:\n    pass\n"
    result = {f.rule_id: f.severity for f in analyze_source(tmp_path, source).findings}
    assert result == {"TM002": Severity.LOW}


def test_nested_function_parameters_are_scoped(tmp_path: Path) -> None:
    source = "def outer(code):\n    def inner(x):\n        return eval(code)\n    return inner\n"
    [finding] = findings(tmp_path, source, "TM010")
    assert finding.severity is Severity.HIGH  # `code` is not a parameter of `inner`
    assert finding.symbol == "outer.<locals>.inner"
