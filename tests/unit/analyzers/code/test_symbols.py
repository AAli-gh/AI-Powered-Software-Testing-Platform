from pathlib import Path

from testmind.analyzers.code.models import ParameterKind
from tests.unit.analyzers.code.helpers import analyze_source

SOURCE = '''
"""Module docstring.

More text."""
import os
from typing import Any


@decorator
async def fetch(
    url: str, /, retries: int = 3, *args: Any, timeout: float | None = None, key, **kw
) -> bytes:
    """Fetch a URL.

    Long description."""
    if retries < 0:
        raise ValueError("retries")
    try:
        pass
    except OSError:
        raise
    raise errors.Custom


def _helper():
    def inner():
        pass
    return inner


class Order(Base, metaclass=Meta):
    """An order."""

    def total(self) -> int:
        return 1

    @property
    def _secret(self):
        return 2

    def __init__(self):
        pass

    class Line:
        def price(self): ...
'''


def test_function_details(tmp_path: Path) -> None:
    profile = analyze_source(tmp_path, SOURCE, "src/shop/api.py")
    fetch = next(f for f in profile.functions if f.name == "fetch")
    assert fetch.module == "shop.api"
    assert fetch.is_async is True
    assert fetch.is_method is False
    assert fetch.is_public is True
    assert fetch.decorators == ["decorator"]
    assert fetch.returns == "bytes"
    assert fetch.docstring == "Fetch a URL."
    assert fetch.raises == ["ValueError", "<re-raise>", "errors.Custom"]
    assert fetch.complexity == 3
    assert fetch.complexity_rank == "A"
    assert [(p.name, p.kind, p.annotation, p.default) for p in fetch.parameters] == [
        ("url", ParameterKind.POSITIONAL_ONLY, "str", None),
        ("retries", ParameterKind.POSITIONAL_OR_KEYWORD, "int", "3"),
        ("args", ParameterKind.VAR_POSITIONAL, "Any", None),
        ("timeout", ParameterKind.KEYWORD_ONLY, "float | None", "None"),
        ("key", ParameterKind.KEYWORD_ONLY, None, None),
        ("kw", ParameterKind.VAR_KEYWORD, None, None),
    ]


def test_qualnames_and_visibility(tmp_path: Path) -> None:
    profile = analyze_source(tmp_path, SOURCE)
    functions = {f.qualname: (f.is_method, f.is_public) for f in profile.functions}
    assert functions == {
        "fetch": (False, True),
        "_helper": (False, False),
        "_helper.<locals>.inner": (False, False),
        "Order.total": (True, True),
        "Order._secret": (True, False),
        "Order.__init__": (True, True),
        "Order.Line.price": (True, True),
    }


def test_classes(tmp_path: Path) -> None:
    profile = analyze_source(tmp_path, SOURCE)
    order = next(c for c in profile.classes if c.name == "Order")
    assert order.bases == ["Base"]
    assert order.docstring == "An order."
    assert order.methods == ["total", "_secret", "__init__"]
    assert [c.qualname for c in profile.classes] == ["Order", "Order.Line"]


def test_module_summary(tmp_path: Path) -> None:
    module = analyze_source(tmp_path, SOURCE).modules[0]
    assert module.docstring == "Module docstring."
    assert module.functions == 7
    assert module.classes == 2
    assert [(i.module, i.names) for i in module.imports] == [("os", []), ("typing", ["Any"])]


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    source = "from . import a\nfrom .b import c\nfrom ..d import e\nfrom .... import too_far\n"
    module = analyze_source(tmp_path, source, "pkg/sub/mod.py").modules[0]
    assert [(i.module, i.names, i.relative) for i in module.imports] == [
        ("pkg.sub", ["a"], True),
        ("pkg.sub.b", ["c"], True),
        ("pkg.d", ["e"], True),
    ]


def test_relative_imports_in_package_init(tmp_path: Path) -> None:
    module = analyze_source(tmp_path, "from .models import X\n", "pkg/__init__.py").modules[0]
    assert module.module == "pkg"
    assert module.imports[0].module == "pkg.models"


def test_long_source_text_is_truncated(tmp_path: Path) -> None:
    default = "x" * 300
    profile = analyze_source(tmp_path, f"def f(a='{default}'): pass\n")
    value = profile.functions[0].parameters[0].default
    assert value is not None
    assert len(value) == 200
    assert value.endswith("…")


def test_empty_docstring(tmp_path: Path) -> None:
    profile = analyze_source(tmp_path, 'def f():\n    """   """\n')
    assert profile.functions[0].docstring is None


def test_raised_names_are_unique_and_in_source_order(tmp_path: Path) -> None:
    source = (
        "def f(x):\n    if x:\n        raise KeyError\n    raise ValueError('a')\n"
        "    raise KeyError\n"
    )
    assert analyze_source(tmp_path, source).functions[0].raises == ["KeyError", "ValueError"]


def test_module_line_count_ignores_unicode_separators(tmp_path: Path) -> None:
    assert analyze_source(tmp_path, 'x = "a\u2028b"\ny = 1\n').modules[0].lines == 2
