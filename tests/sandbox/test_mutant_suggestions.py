"""AI tests for surviving mutants, judged by the real sandbox (only the model is scripted).

examples/shipping_rates leaves 8 mutants alive (EXPECTED_MUTATION.md). The suggester must accept
a test only when it passes on the original code AND kills its mutant.
"""

import pytest

from testmind.mutation_engine.models import MutationReport
from testmind.mutation_engine.suggester import MutantTestSuggester, SuggestionStatus
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from tests.doubles import ScriptedLLM
from tests.sandbox.test_suite_runner import EXAMPLES, analyse

ROOT = EXAMPLES / "shipping_rates"


def module(*body: str) -> str:
    return (
        "```python\nfrom shipping.rates import is_free, shipping_cost\n\n\n"
        + "\n\n".join(body)
        + "\n```"
    )


WEAK_BOUNDARY = module("def test_ten_kg():\n    assert shipping_cost(10) > 0")
BOUNDARY = module("def test_no_discount_at_exactly_ten_kg():\n    assert shipping_cost(10) == 25")
SURCHARGE = module(
    "def test_express_adds_fifteen():\n    assert shipping_cost(2, express=True) == 24"
)
WRONG_THRESHOLD = module("def test_threshold():\n    assert is_free(100, member=False) is False")
THRESHOLD = module("def test_threshold():\n    assert is_free(100, member=False) is True")


@pytest.fixture(scope="module")
def image(sandbox: DockerSandbox) -> str:
    repository, _ = analyse(ROOT)
    return RunnerImageBuilder(sandbox.image).ensure(repository.dependencies).tag


def only(report: MutationReport, *targets: tuple[int, str]) -> MutationReport:
    chosen = [m for m in report.mutants if (m.line, m.original) in targets]
    return report.model_copy(update={"mutants": chosen})


def test_suggestions_are_judged_by_the_sandbox(
    sandbox: DockerSandbox, shipping: MutationReport, image: str
) -> None:
    targets = only(shipping, (6, "weight > 10"), (9, "15"), (15, "total >= 100"))
    llm = ScriptedLLM([WEAK_BOUNDARY, BOUNDARY, SURCHARGE, WRONG_THRESHOLD, THRESHOLD])
    suggester = MutantTestSuggester(llm, sandbox, image=image, max_tokens=500, max_mutants=5)
    result = suggester.suggest(ROOT, targets)

    outcomes = {s.original: [a.outcome for a in s.attempts] for s in result.suggestions}
    assert outcomes == {
        # passes on the code, but the >= 10 mutant also passes it: rejected, then fixed
        "weight > 10": ["mutant_survives", "accepted"],
        "15": ["accepted"],
        # asserted the wrong behaviour: fails on the original code, then fixed
        "total >= 100": ["fails_on_original", "accepted"],
    }
    assert all(s.status is SuggestionStatus.ACCEPTED for s in result.suggestions)
    assert result.accepted == 3
    assert "ALSO passes when `weight > 10` is changed to `(weight >= 10)`" in llm.requests[1].prompt
    assert "does not pass on the ORIGINAL code" in llm.requests[4].prompt
