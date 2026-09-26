"""Result model of a repository analysis (stored as JSON, returned by the API)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileEntry(_Model):
    path: str = Field(description="POSIX path relative to the repository root")
    size_bytes: int
    language: str | None
    lines: int | None = Field(description="None when the file is binary or too large to read")
    is_test: bool = False


class LanguageStat(_Model):
    name: str
    files: int
    lines: int
    bytes: int
    percentage: float = Field(description="Share of all counted lines, 0-100")


class DependencyGroup(StrEnum):
    RUNTIME = "runtime"
    DEVELOPMENT = "development"
    OPTIONAL = "optional"


class Dependency(_Model):
    name: str = Field(description="Normalised distribution name (PEP 503)")
    specifier: str = Field(description="Version specifier as declared, may be empty")
    group: DependencyGroup
    source: str = Field(description="File the dependency was declared in")
    extras: list[str] = Field(default_factory=list)
    marker: str | None = None


class DetectionCategory(StrEnum):
    WEB_FRAMEWORK = "web_framework"
    TEST_FRAMEWORK = "test_framework"


class Detection(_Model):
    name: str
    category: DetectionCategory
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(description="Human-readable facts that support the detection")


class EntryPointKind(StrEnum):
    CONSOLE_SCRIPT = "console_script"
    DJANGO_MANAGE = "django_manage"
    PACKAGE_MAIN = "package_main"
    SCRIPT_MAIN = "script_main"
    ASGI_APP = "asgi_app"
    WSGI_APP = "wsgi_app"


class EntryPoint(_Model):
    kind: EntryPointKind
    path: str
    target: str | None = Field(default=None, description="e.g. 'pkg.cli:main' or 'app' variable")


class DockerInfo(_Model):
    dockerfiles: list[str] = Field(default_factory=list)
    base_images: list[str] = Field(default_factory=list)
    compose_files: list[str] = Field(default_factory=list)
    compose_services: list[str] = Field(default_factory=list)


class CIConfig(_Model):
    provider: str
    path: str
    jobs: list[str] = Field(default_factory=list)


class Issue(_Model):
    """Something the analyser could not process (the analysis still succeeds)."""

    path: str
    message: str


class RepositorySummary(_Model):
    """Everything except the per-file inventory (which can hold thousands of entries)."""

    file_count: int
    total_size_bytes: int
    truncated: bool = Field(description="True when max_files was reached")
    languages: list[LanguageStat]
    primary_language: str | None
    dependencies: list[Dependency]
    frameworks: list[Detection]
    test_frameworks: list[Detection]
    test_files: list[str]
    entry_points: list[EntryPoint]
    config_files: list[str]
    docker: DockerInfo
    ci: list[CIConfig]
    issues: list[Issue]
    django_settings_module: str | None = Field(
        default=None, description="Value of DJANGO_SETTINGS_MODULE set by the project, if any"
    )


class RepositoryProfile(RepositorySummary):
    files: list[FileEntry]

    def summary(self) -> RepositorySummary:
        return RepositorySummary.model_validate(self.model_dump(exclude={"files"}))
