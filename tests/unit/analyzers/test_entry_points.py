from pathlib import Path

import pytest

from testmind.analyzers.repository.entry_points import module_name
from testmind.analyzers.repository.models import EntryPointKind
from tests.unit.analyzers.helpers import analyze


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("app/main.py", "app.main"),
        ("src/pkg/api.py", "pkg.api"),
        ("pkg/__init__.py", "pkg"),
        ("src.py", "src"),
        ("src/__init__.py", ""),
    ],
)
def test_module_name(path: str, module: str) -> None:
    assert module_name(path) == module


def entries(profile) -> set[tuple[EntryPointKind, str, str | None]]:  # type: ignore[no-untyped-def]
    return {(e.kind, e.path, e.target) for e in profile.entry_points}


def test_console_scripts_from_all_manifests(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "pyproject.toml": '[project.scripts]\nb = "pkg.b:main"\n'
            '[tool.poetry.scripts]\na = "pkg.a:run"\n',
            "setup.cfg": (
                "[options.entry_points]\nconsole_scripts =\n    c = pkg.c:main\n    junk\n"
            ),
        },
    )
    assert entries(profile) == {
        (EntryPointKind.CONSOLE_SCRIPT, "pyproject.toml", "a = pkg.a:run"),
        (EntryPointKind.CONSOLE_SCRIPT, "pyproject.toml", "b = pkg.b:main"),
        (EntryPointKind.CONSOLE_SCRIPT, "setup.cfg", "c = pkg.c:main"),
    }


@pytest.mark.parametrize(
    "files",
    [
        {"pyproject.toml": "[project\n"},
        {"setup.cfg": "garbage"},
        {"pyproject.toml": '[project]\nscripts = "not a table"\n'},
        {"pyproject.toml": 'tool = "not a table"\n'},
    ],
)
def test_broken_manifests_yield_no_scripts(tmp_path: Path, files: dict[str, str]) -> None:
    assert analyze(tmp_path, files).entry_points == []


def test_python_entry_points(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "manage.py": 'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "s")\n',
            "src/tool/__main__.py": "",
            "scripts/migrate.py": 'if __name__ == "__main__":\n    pass\n',
            "tests/test_x.py": 'if __name__ == "__main__":\n    pass\n',
            "setup.py": 'if __name__ == "__main__":\n    pass\n',
            "app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "web/factory.py": "def make():\n    return Flask(__name__)\n",
            "odd.py": "class Holder:\n    app = Flask(__name__)\n",
        },
    )
    assert entries(profile) == {
        (EntryPointKind.DJANGO_MANAGE, "manage.py", None),
        (EntryPointKind.PACKAGE_MAIN, "src/tool/__main__.py", "tool"),
        (EntryPointKind.SCRIPT_MAIN, "scripts/migrate.py", None),
        (EntryPointKind.ASGI_APP, "app.py", "app:app"),
        (EntryPointKind.WSGI_APP, "web/factory.py", "web.factory:make()"),
    }


def test_manage_py_without_django_is_not_an_entry_point(tmp_path: Path) -> None:
    assert analyze(tmp_path, {"manage.py": "print('custom tool')\n"}).entry_points == []
