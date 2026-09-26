import pytest

from testmind.ai_engine.test_generator.code_checks import (
    check_code,
    count_tests,
    extract_code,
    suspected_bugs,
)

GOOD = (
    "import pytest\nfrom shop.pricing import total\n\ndef test_total():\n    assert total(1) == 1\n"
)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (f"Here you go:\n```python\n{GOOD}```\nDone.", GOOD),
        (f"```py\n{GOOD}```", GOOD),
        (f"```\n{GOOD}```", GOOD),
        (f"```python\nold = 1\n```\nfixed:\n```python\n{GOOD}```", GOOD),
        (GOOD, GOOD),
        ("I cannot write tests for this.", None),
    ],
)
def test_extract_code(reply: str, expected: str | None) -> None:
    assert extract_code(reply) == expected


def test_good_code_has_no_problems() -> None:
    assert check_code(GOOD, "shop.pricing") == []


@pytest.mark.parametrize(
    "code",
    [
        "import shop.pricing\ndef test_a(): pass\n",
        "from shop import pricing\ndef test_a(): pass\n",
        "from shop.pricing.sub import x\ndef test_a(): pass\n",
    ],
)
def test_target_import_forms(code: str) -> None:
    assert check_code(code, "shop.pricing") == []


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("def test_a(:\n", "SyntaxError"),
        ("from shop.pricing import total\n\ndef helper(): pass\n", "No test functions"),
        ("from shop.other import x\ndef test_a(): pass\n", "never imported"),
        (
            "from shop.pricing import t\nimport socket\ndef test_a(): pass\n",
            "Forbidden import: socket",
        ),
        (
            "from shop.pricing import t\nimport urllib.request\ndef test_a(): pass\n",
            "Forbidden import: urllib",
        ),
        (
            "from shop.pricing import t\nfrom subprocess import run\ndef test_a(): pass\n",
            "Forbidden import: subprocess",
        ),
        (
            "from shop.pricing import t\nimport os\ndef test_a():\n    os.system('x')\n",
            "Forbidden call: os.system()",
        ),
        ("from shop.pricing import t\ndef test_a():\n    eval('1')\n", "Forbidden call: eval()"),
    ],
)
def test_problems(code: str, problem: str) -> None:
    assert any(problem in p for p in check_code(code, "shop.pricing"))


def test_calls_without_simple_names_are_ignored() -> None:
    code = "from shop.pricing import t\n\ndef test_a():\n    handlers[0]()\n    (lambda: 1)()\n"
    assert check_code(code, "shop.pricing") == []


def test_http_status_is_allowed() -> None:
    code = "from http import HTTPStatus\nfrom shop.pricing import t\ndef test_a(): pass\n"
    assert check_code(code, "shop.pricing") == []


def test_count_tests() -> None:
    assert count_tests("def test_a(): pass\nasync def test_b(): pass\ndef x(): pass\n") == 2
    assert count_tests("def (") == 0


def test_suspected_bugs() -> None:
    code = """
import pytest

@pytest.mark.xfail(reason="possible bug: divides by zero", strict=True)
def test_zero(): ...

@pytest.mark.xfail(reason="flaky on windows")
def test_other(): ...

@pytest.mark.xfail(strict=True)
def test_no_reason(): ...

@pytest.mark.parametrize("x", [1])
def test_param(x): ...
"""
    bugs = suspected_bugs(code)
    assert [(b.test, b.reason) for b in bugs] == [("test_zero", "possible bug: divides by zero")]
    assert suspected_bugs("def (") == []
