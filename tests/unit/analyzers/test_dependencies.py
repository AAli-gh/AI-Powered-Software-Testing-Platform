import textwrap

import pytest

from testmind.analyzers.repository.dependencies import DependencyCollector
from testmind.analyzers.repository.models import Dependency, DependencyGroup

RUNTIME, DEV, OPTIONAL = (
    DependencyGroup.RUNTIME,
    DependencyGroup.DEVELOPMENT,
    DependencyGroup.OPTIONAL,
)


def collect(files: dict[str, str]) -> DependencyCollector:
    contents = {path: textwrap.dedent(text) for path, text in files.items()}
    collector = DependencyCollector(contents.get)
    collector.collect(contents)
    return collector


def summary(collector: DependencyCollector) -> set[tuple[str, str, DependencyGroup]]:
    return {(d.name, d.specifier, d.group) for d in collector.dependencies}


def test_pyproject_pep621_optional_and_dependency_groups() -> None:
    collector = collect(
        {
            "pyproject.toml": """
            [project]
            dependencies = ["Django>=5", "requests[socks]>=2 ; python_version >= '3.11'"]
            [project.optional-dependencies]
            postgres = ["psycopg>=3"]
            test = ["pytest>=8"]
            [dependency-groups]
            dev = ["ruff", {include-group = "test"}]
            docs = ["mkdocs"]
            """
        }
    )
    assert summary(collector) == {
        ("django", ">=5", RUNTIME),
        ("requests", ">=2", RUNTIME),
        ("psycopg", ">=3", OPTIONAL),
        ("pytest", ">=8", DEV),
        ("ruff", "", DEV),
        ("mkdocs", "", OPTIONAL),
    }
    requests = next(d for d in collector.dependencies if d.name == "requests")
    assert requests.extras == ["socks"]
    assert requests.marker == 'python_version >= "3.11"'


def test_flit_legacy_metadata() -> None:
    # fastapi's pyproject.toml in 2020 (flit < 3.2), as in BugsInPy.
    collector = collect(
        {
            "pyproject.toml": """
            [tool.flit.metadata]
            module = "fastapi"
            requires = ["starlette ==0.13.2", "pydantic >=0.32.2,<2.0.0"]
            requires-python = ">=3.6"
            [tool.flit.metadata.requires-extra]
            test = ["pytest >=4.0.0", "requests"]
            doc = ["mkdocs"]
            """
        }
    )
    assert summary(collector) == {
        ("starlette", "==0.13.2", RUNTIME),
        ("pydantic", "<2.0.0,>=0.32.2", RUNTIME),
        ("pytest", ">=4.0.0", DEV),
        ("requests", "", DEV),
        ("mkdocs", "", OPTIONAL),
    }


def test_poetry_tables() -> None:
    collector = collect(
        {
            "pyproject.toml": """
            [tool.poetry.dependencies]
            python = "^3.11"
            Flask = "^3.0"
            celery = {version = "^5.4", extras = ["redis"]}
            anything = "*"
            [tool.poetry.dev-dependencies]
            black = "^24"
            [tool.poetry.group.test.dependencies]
            pytest = "^8"
            [tool.poetry.group.docs.dependencies]
            sphinx = "^7"
            """
        }
    )
    assert summary(collector) == {
        ("flask", "^3.0", RUNTIME),
        ("celery", "^5.4", RUNTIME),
        ("anything", "", RUNTIME),
        ("black", "^24", DEV),
        ("pytest", "^8", DEV),
        ("sphinx", "^7", OPTIONAL),
    }
    assert next(d for d in collector.dependencies if d.name == "celery").extras == ["redis"]


def test_requirements_syntax() -> None:
    collector = collect(
        {
            "requirements.txt": """
            # comment
            fastapi>=0.110  # inline comment
            Uvicorn[standard]==0.29.0 \\
                --hash=sha256:abc
            -e .
            --index-url https://example.invalid/simple
            git+https://github.com/org/repo.git#egg=pkg
            https://example.invalid/pkg.whl
            pydantic ; python_version >= "3.11"
            """
        }
    )
    assert summary(collector) == {
        ("fastapi", ">=0.110", RUNTIME),
        ("uvicorn", "==0.29.0", RUNTIME),
        ("pydantic", "", RUNTIME),
    }
    assert collector.issues == []


def test_include_keeps_the_included_files_own_group() -> None:
    """Regression: -r requirements.txt inside requirements-dev.txt holds runtime dependencies."""
    collector = collect(
        {
            "requirements.txt": "django>=5\n",
            "requirements-dev.txt": "-r requirements.txt\npytest\n",
        }
    )
    assert summary(collector) == {("django", ">=5", RUNTIME), ("pytest", "", DEV)}


def test_nested_include_relative_to_including_file() -> None:
    collector = collect(
        {
            "requirements/dev.txt": "--requirement=base.txt\nruff\n",
            "requirements/base.txt": "flask\n",
        }
    )
    assert summary(collector) == {("flask", "", RUNTIME), ("ruff", "", DEV)}


@pytest.mark.parametrize(
    ("path", "group"),
    [
        ("requirements.txt", RUNTIME),
        ("requirements-dev.txt", DEV),
        ("requirements_test.txt", DEV),
        ("test_requirements.txt", DEV),  # cookiecutter
        ("dev-requirements.txt", DEV),
        ("prod_requirements.txt", RUNTIME),
        ("requirements/ci.txt", DEV),
        ("requirements/base.txt", RUNTIME),
        ("requirements/special.txt", RUNTIME),  # regression: "ci" inside a word is not CI
        ("requirements/production.txt", RUNTIME),
    ],
)
def test_requirements_group_from_file_name(path: str, group: DependencyGroup) -> None:
    assert summary(collect({path: "attrs\n"})) == {("attrs", "", group)}


def test_include_cycle_is_reported() -> None:
    collector = collect({"requirements.txt": "-r requirements.txt\nattrs\n"})
    assert summary(collector) == {("attrs", "", RUNTIME)}
    assert "cycle" in collector.issues[0].message


@pytest.mark.parametrize("target", ["../outside.txt", "/etc/requirements.txt", "https://x/r.txt"])
def test_includes_leaving_the_repository_are_refused(target: str) -> None:
    collector = collect({"requirements.txt": f"-r {target}\n"})
    assert collector.dependencies == []
    assert "Ignored include" in collector.issues[0].message


def test_missing_include_is_silently_empty() -> None:
    assert collect({"requirements.txt": "-r missing.txt\n"}).dependencies == []


def test_invalid_requirement_is_reported() -> None:
    collector = collect({"requirements.txt": "not a valid ! requirement\nattrs\n"})
    assert summary(collector) == {("attrs", "", RUNTIME)}
    assert "Invalid requirement" in collector.issues[0].message


def test_setup_cfg() -> None:
    collector = collect(
        {
            "setup.cfg": """
            [options]
            install_requires =
                Flask>=3
                # comment
                click
            [options.extras_require]
            test = pytest
            redis = redis>=5
            """
        }
    )
    assert summary(collector) == {
        ("flask", ">=3", RUNTIME),
        ("click", "", RUNTIME),
        ("pytest", "", DEV),
        ("redis", ">=5", OPTIONAL),
    }


def test_invalid_setup_cfg_is_reported() -> None:
    collector = collect({"setup.cfg": "no section header\n"})
    assert "Invalid setup.cfg" in collector.issues[0].message


def test_setup_py_static_literals_only() -> None:
    collector = collect(
        {
            "setup.py": """
            from setuptools import setup
            setup(
                name="pkg",
                install_requires=["requests>=2", "attrs"],
                extras_require={
                    "dev": ["pytest"], "yaml": ("pyyaml",), "tests": {"mock", "tox"}
                },
            )
            """
        }
    )
    assert summary(collector) == {
        ("requests", ">=2", RUNTIME),
        ("attrs", "", RUNTIME),
        ("pytest", "", DEV),
        ("pyyaml", "", OPTIONAL),
        ("mock", "", DEV),  # a set literal, as PySnooper's setup.py has
        ("tox", "", DEV),
    }


def test_dynamic_setup_py_is_reported_not_executed() -> None:
    collector = collect(
        {"setup.py": "import setuptools\nsetuptools.setup(install_requires=read_reqs())\n"}
    )
    assert collector.dependencies == []
    assert collector.issues[0].message == "install_requires is not a static literal"


def test_setup_py_names_bound_once_to_a_literal_are_resolved() -> None:
    # The layout of thefuck's setup.py (found evaluating TestMind on BugsInPy).
    collector = collect(
        {
            "setup.py": """
            from setuptools import setup
            install_requires = ['psutil', 'colorama']
            extras_require = {':python_version<"3.4"': ['pathlib2']}
            setup(name='pkg', install_requires=install_requires, extras_require=extras_require)
            """
        }
    )
    assert summary(collector) == {
        ("psutil", "", RUNTIME),
        ("colorama", "", RUNTIME),
        ("pathlib2", "", OPTIONAL),
    }
    assert collector.issues == []


@pytest.mark.parametrize(
    "changed",
    [
        "requires = ['b']",  # bound twice
        "requires += ['b']",
        "requires.append('b')",
        "for requires in [['b']]: pass",
        "def f():\n    global requires\n    requires = ['b']",
    ],
)
def test_setup_py_names_that_may_change_are_not_guessed(changed: str) -> None:
    source = f"from setuptools import setup\nrequires = ['a']\n{changed}\n"
    collector = collect({"setup.py": source + "setup(install_requires=requires)\n"})
    assert collector.dependencies == []
    assert collector.issues[0].message == "install_requires is not a static literal"


def test_setup_py_unknown_and_non_literal_names_are_reported() -> None:
    for body in ("setup(install_requires=undefined)", "x = read()\nsetup(install_requires=x)"):
        collector = collect({"setup.py": "from setuptools import setup\n" + body + "\n"})
        assert collector.dependencies == []
        assert collector.issues[0].message == "install_requires is not a static literal"


def test_unparseable_setup_py_is_reported() -> None:
    collector = collect({"setup.py": "setup(\n"})
    assert "Cannot parse setup.py" in collector.issues[0].message


def test_pipfile() -> None:
    collector = collect(
        {
            "Pipfile": """
            [packages]
            requests = "*"
            django = {version = ">=5"}
            [dev-packages]
            pytest = ">=8"
            """
        }
    )
    assert summary(collector) == {
        ("requests", "", RUNTIME),
        ("django", ">=5", RUNTIME),
        ("pytest", ">=8", DEV),
    }


def test_invalid_toml_is_reported() -> None:
    collector = collect({"pyproject.toml": "[project\n"})
    assert collector.dependencies == []
    assert "Invalid TOML" in collector.issues[0].message


def test_unreadable_manifest_is_skipped() -> None:
    collector = DependencyCollector(lambda _: None)
    for name in ["pyproject.toml", "requirements.txt", "setup.cfg", "setup.py", "Pipfile"]:
        assert collector.collect([name]) == []
    assert collector.issues == []


def test_unrelated_files_are_ignored() -> None:
    assert collect({"README.md": "django>=5", "notes.txt": "flask"}).dependencies == []


def test_duplicates_in_one_file_are_merged() -> None:
    assert len(collect({"requirements.txt": "attrs\nattrs>=1\n"}).dependencies) == 1


def test_names_are_normalised() -> None:
    names = [
        d.name for d in collect({"requirements.txt": "Zope.Interface\nPy_YAML\n"}).dependencies
    ]
    assert names == ["zope-interface", "py-yaml"]


def test_dependency_model_is_immutable() -> None:
    dependency = Dependency(name="x", specifier="", group=RUNTIME, source="r.txt")
    with pytest.raises(ValueError, match="frozen"):
        dependency.name = "y"  # type: ignore[misc]


def test_setup_py_edge_cases() -> None:
    collector = collect(
        {
            "setup.py": """
            setup(version=get_version(), install_requires="requests", extras_require=["bad"])
            make()(install_requires=["ignored"])
            """
        }
    )
    assert summary(collector) == {("requests", "", RUNTIME)}
    assert collector.issues == []  # non-literal `version` is irrelevant, not an issue


def test_non_list_values_are_ignored() -> None:
    collector = collect({"pyproject.toml": "[project]\ndependencies = 42\n"})
    assert collector.dependencies == []


def test_backslash_on_last_line_and_dot_segments() -> None:
    collector = collect(
        {
            "requirements/dev.txt": "-r ./base.txt\nruff \\",
            "requirements/base.txt": "flask\n",
        }
    )
    assert summary(collector) == {("flask", "", RUNTIME), ("ruff", "", DEV)}


def test_include_from_subdirectory_to_parent() -> None:
    # Only the including file is a manifest here, so `attrs` can only come from the include.
    contents = {"requirements/dev.txt": "-r ../shared/base.txt\n", "shared/base.txt": "attrs\n"}
    collector = DependencyCollector(contents.get)
    collector.collect(["requirements/dev.txt"])
    assert [(d.name, d.source) for d in collector.dependencies] == [("attrs", "shared/base.txt")]
