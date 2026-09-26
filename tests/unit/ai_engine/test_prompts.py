import pytest

from testmind.ai_engine.prompts import load_prompt


@pytest.mark.parametrize(
    ("name", "placeholders"),
    [
        ("test_generation_system", {}),
        ("generate_tests", {"module", "path", "targets", "kinds", "findings", "source"}),
        ("fix_tests", {"module", "feedback", "code"}),
    ],
)
def test_prompts_render_with_exactly_their_placeholders(name: str, placeholders: set[str]) -> None:
    prompt = load_prompt(name, 1)
    assert prompt.id == f"{name}.v1"
    rendered = prompt.render(**{p: f"<{p}>" for p in placeholders})
    assert all(f"<{p}>" in rendered for p in placeholders)
    assert "$" not in rendered


def test_missing_placeholder_is_an_error() -> None:
    with pytest.raises(KeyError):
        load_prompt("fix_tests", 1).render(module="m")


def test_values_are_inserted_verbatim() -> None:
    rendered = load_prompt("fix_tests", 1).render(module="m", feedback="$HOME ${x}", code="x = '$'")
    assert "$HOME ${x}" in rendered
    assert "x = '$'" in rendered


def test_unknown_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("generate_tests", 99)
