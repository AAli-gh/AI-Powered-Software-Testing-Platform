"""Versioned prompt templates.

Prompts live in files named `<name>.v<N>.md`, never inline in code. A prompt is changed by adding
a new version file; the version used is recorded with every result so outputs stay traceable.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from string import Template


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: int
    template: Template

    @property
    def id(self) -> str:
        return f"{self.name}.v{self.version}"

    def render(self, **values: str) -> str:
        """Substitute `$placeholders`; a missing value is an error, never silently empty."""
        return self.template.substitute(values)


def load_prompt(name: str, version: int) -> Prompt:
    text = resources.files(__package__).joinpath(f"{name}.v{version}.md").read_text("utf-8")
    return Prompt(name=name, version=version, template=Template(text))
