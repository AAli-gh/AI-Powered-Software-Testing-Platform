import textwrap

import pytest

from testmind.analyzers.repository.python_scanner import scan_python


def scan(source: str, path: str = "m.py"):  # type: ignore[no-untyped-def]
    return scan_python(path, textwrap.dedent(source))


def test_top_level_imports() -> None:
    facts = scan(
        """
        import os.path, json as j
        from django.db import models
        from . import sibling
        from .pkg import x
        """
    )
    assert facts.imports == {"os", "json", "django"}


def test_module_level_app_instances() -> None:
    facts = scan(
        """
        from fastapi import FastAPI
        import flask
        app = FastAPI()
        api: FastAPI = FastAPI(title="x")
        web = flask.Flask(__name__)
        """
    )
    assert [(a.framework, a.variable, a.factory) for a in facts.app_instances] == [
        ("fastapi", "app", None),
        ("fastapi", "api", None),
        ("flask", "web", None),
    ]


def test_app_factory_is_recorded() -> None:
    facts = scan(
        """
        def create_app():
            app = Flask(__name__)
            return app
        """
    )
    assert [(a.framework, a.variable, a.factory) for a in facts.app_instances] == [
        ("flask", None, "create_app")
    ]


def test_django_application_objects() -> None:
    facts = scan("application = get_asgi_application()\n")
    assert [(a.framework, a.variable) for a in facts.app_instances] == [
        ("django_asgi", "application")
    ]


@pytest.mark.parametrize("guard", ['if __name__ == "__main__":', "if '__main__' == __name__:"])
def test_main_guard(guard: str) -> None:
    assert scan(f"{guard}\n    pass\n").has_main_guard


@pytest.mark.parametrize(
    "source",
    [
        'if __name__ != "__main__":\n    pass\n',
        "if x == 1:\n    pass\n",
        "if a == b == c:\n    pass\n",
    ],
)
def test_not_a_main_guard(source: str) -> None:
    assert not scan(source).has_main_guard


def test_testcase_subclasses() -> None:
    facts = scan(
        """
        class A(unittest.TestCase): ...
        class B(TestCase): ...
        class C(django.test.TransactionTestCase): ...
        class NotATest(Base): ...
        class Generic(Something[int]): ...
        """
    )
    assert facts.testcase_classes == ["A", "B", "C"]


def test_counts_module_level_pytest_functions_only() -> None:
    facts = scan(
        """
        def test_a(): ...
        async def test_b(): ...
        def helper(): ...
        class TestGroup:
            def test_method(self): ...
        """
    )
    assert facts.pytest_style_tests == 2


@pytest.mark.parametrize(
    "source",
    [
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "site.settings")',
        'os.environ["DJANGO_SETTINGS_MODULE"] = "site.settings"',
    ],
)
def test_django_settings_module_is_detected(source: str) -> None:
    assert scan(source).sets_django_settings_module


@pytest.mark.parametrize(
    "source",
    [
        'KEY = "DJANGO_SETTINGS_MODULE"',  # regression: a detector naming the key is not Django
        "# DJANGO_SETTINGS_MODULE\n",
        'd.setdefault("OTHER", 1)',
        'os.environ["OTHER"] = "x"',
        "d.setdefault()",
    ],
)
def test_mentioning_the_settings_key_is_not_django(source: str) -> None:
    assert not scan(source).sets_django_settings_module


@pytest.mark.parametrize("source", ["INSTALLED_APPS = []", "INSTALLED_APPS: list[str] = []"])
def test_installed_apps(source: str) -> None:
    assert scan(source).defines_installed_apps


def test_installed_apps_inside_function_is_not_settings() -> None:
    assert not scan("def f():\n    INSTALLED_APPS = []\n").defines_installed_apps


@pytest.mark.parametrize("source", ["def broken(:\n", "x = 1\x00"])
def test_unparseable_source_is_reported_not_raised(source: str) -> None:
    facts = scan_python("bad.py", source)
    assert facts.syntax_error
    assert facts.imports == set()


def test_calls_without_a_simple_name_are_ignored() -> None:
    facts = scan("app = factories['flask']()\nimport os\n")
    assert facts.app_instances == []


def test_app_assigned_to_non_name_target_has_no_variable() -> None:
    facts = scan("holder.app = Flask(__name__)\na = b = FastAPI()\n")
    assert [(a.framework, a.variable) for a in facts.app_instances] == [
        ("flask", None),
        ("fastapi", None),
    ]
