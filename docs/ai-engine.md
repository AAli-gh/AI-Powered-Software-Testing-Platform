# AI engine and test generation

Phase 4 introduces the first AI-driven stage: **generating pytest tests** for a repository's
code. The design rule is that the LLM *proposes* and deterministic tooling *decides*. Nothing
the model writes is trusted until static checks and a real pytest run in the sandbox accept
it.

Code: `src/testmind/ai_engine/` (LLM interface, prompts, test generator) and
`src/testmind/sandbox/` (isolated execution).

## Pipeline

```text
POST /api/v1/runs/{run_id}/test-generations   (run must be succeeded; LLM must be configured)
  → test_generations row (queued) → Celery task
  → Target selection (deterministic, from the Phase 3 code profile)
  → per module:
        prompt (versioned file) → LLM → extract code block
        → static checks ──fail──┐
        → pytest in the Docker sandbox ──fail──┐
        ↑                                       │
        └── fix prompt with the concrete problems (at most TESTMIND_GENERATION_MAX_ITERATIONS)
  → result stored: one test module per source module, with status, attempts, test counts,
    suspected bugs and token usage
```

### Target selection

Deterministic, from the code analysis:
- Candidates are public functions and methods. Private (`_x`), dunder, property, abstract
  and overload functions are excluded, as are entry scripts (`manage.py`, `setup.py`, …),
  migrations, test files and scripts.
- Priority: functions with **findings** first (they are the most likely to hide bugs), then
  higher **complexity**, then source order. The number of targets is capped
  (`max_targets`, default `TESTMIND_GENERATION_MAX_TARGETS`).
- Targets are grouped per module. One LLM conversation produces one test module, e.g.
  `tests/testmind/test_inventory_stock.py`.
- **Coverage-guided** (`coverage_execution_id`): only functions that the chosen execution did
  not fully cover, riskiest coverage gap first, and the prompt lists the lines and branches
  that never ran ([coverage.md](coverage.md#coverage-guided-test-generation)).

### What the model receives

The system prompt (`prompts/test_generation_system.v1.md`) contains the rules. The user prompt
(`prompts/generate_tests.v1.md`) contains the module name and path, each target's signature
(parameters with kinds, annotations and defaults), its complexity, the exceptions it raises,
its docstring, the requested test kinds (unit / edge cases / negative), the module's findings
from static analysis (as hints, not certainties), and the full module source. Findings like
TM040 ("`count` is used as a divisor without validation") steer the model towards the right
edge cases.

### Validation (the model never grades itself)

1. **Extraction.** The last fenced Python block in the reply is used.
2. **Static checks** (`code_checks.py`). The code must parse, contain `test_*` functions and
   import the module under test. Network/process modules (`socket`, `subprocess`,
   `requests`, `urllib`, …) and calls like `eval`, `exec`, `os.system` and `os.remove` are
   refused. This is defence in depth, because the sandbox already has no network.
3. **Execution** in the Docker sandbox, with results read from pytest's JUnit report.
4. On failure, the **concrete** problems (static problems, or failing test names with their
   assertion messages and trimmed tracebacks) are sent back with the fix prompt
   (`prompts/fix_tests.v1.md`), up to the iteration limit.

### Suspected bugs, not weakened tests

A test can fail because the *code* is wrong. A naive "fix the test until it passes" loop
would then rewrite the assertion to match the bug and hide it. The prompts forbid this: such
a test keeps asserting the correct behaviour and is marked
`@pytest.mark.xfail(reason="possible bug: …", strict=True)`. With `strict=True`, if the code
is later fixed the xfail turns into a failure, so the marker cannot rot silently. These tests
are reported as `suspected_bugs`, which are input for failure and bug analysis (Phase 7).

### File statuses

| Status | Meaning |
|---|---|
| `passed` | Ran in the sandbox. Every test passed or is an intended `xfail` |
| `failed` | Valid code that still fails after the last iteration |
| `rejected` | The model never produced code that passed the static checks |
| `environment_error` | The module under test imports a dependency that is not installed in the sandbox. The model cannot fix that, so it is not retried. Since Phase 5, generated tests run in the project's runner image, so this means the dependency could not be installed |
| `unverified` | Statically valid, but no sandbox was available to run it |
| `llm_error` | The provider failed or refused |

## LLM providers

`ai_engine/llm/base.py` defines the provider-neutral contract (`LLMRequest` → `LLMResponse`,
`LLMError`, `LLMRefusalError`). Nothing outside `ai_engine/llm` imports a vendor SDK.

| Provider | Status |
|---|---|
| **Anthropic** (`anthropic` SDK) | Implemented. Default model `claude-opus-5`. Uses server-side refusal fallbacks (`fallbacks="default"`, beta `server-side-fallback-2026-07-01`): if the primary model declines, the API re-runs the request on a fallback model, and the model that actually answered is recorded. `refusal` and `max_tokens` stop reasons become errors instead of silently using partial output |
| OpenAI, local models (Ollama) | **Not implemented.** The interface is provider-neutral, so adding one means one class implementing `complete()` |

**Credentials.** The API key is not a TestMind setting. The Anthropic SDK reads
`ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN` / an `ant auth login` profile) directly, so
the secret never passes through TestMind's configuration objects or logs. Without
credentials, `POST …/test-generations` answers **503 `llm_not_configured`**. The platform
does not pretend to generate anything.

## Prompts and versioning

Prompts are files named `<name>.v<N>.md` under `ai_engine/prompts/`, loaded by
`load_prompt(name, version)` and rendered with `string.Template`. A missing placeholder is an
error, and substituted values are inserted verbatim. A prompt is changed by **adding** a new
version file. The version used is stored with every generation (`prompt_version`), so
results stay traceable to the exact prompt text. Coverage-guided generations append the
separately versioned `coverage.v1.md` section and record `v1+coverage.v1`.

## Configuration: external AI is optional

External AI is **off by default**. Everything that detects problems works without it: running
tests, coverage, failure analysis, fault localisation, mutation testing, regression analysis
and API coverage. These are deterministic, and their accuracy is measured with AI off
([evaluation.md](evaluation.md)). An LLM only adds the features below, and each can be turned
off on its own:

| Feature (`TESTMIND_AI_FEATURES`) | What the LLM does | Without it |
|---|---|---|
| `test_generation` | writes tests for untested functions, checked in the sandbox | the coverage gaps are still listed |
| `failure_explanation` | a root-cause hypothesis, kept only if its evidence checks out | the deterministic groups, evidence and suspects |
| `mutation_suggestions` | tests that would kill surviving mutants | the surviving mutants are still listed |
| `api_suggestions` | tests for untested endpoints | the endpoint gaps are still listed |

A disabled feature's endpoint answers `503 llm_not_configured` with the reason.

| Variable | Default | Purpose |
|---|---|---|
| `TESTMIND_LLM_PROVIDER` | `disabled` | `disabled` or `anthropic` |
| `TESTMIND_AI_FEATURES` | all four | comma-separated features that may use the LLM |
| `TESTMIND_LLM_MODEL` | `claude-opus-5` | Model id |
| `TESTMIND_LLM_MAX_TOKENS` | `16000` | Max output tokens per call |
| `TESTMIND_LLM_TIMEOUT_SECONDS` | `600` | Per-request timeout (SDK retries included) |
| `TESTMIND_GENERATION_MAX_ITERATIONS` | `3` | Bound on generate + fix attempts per module |
| `TESTMIND_GENERATION_MAX_TARGETS` | `10` | Default cap on functions per generation |
| `TESTMIND_SANDBOX_*` | see `.env.example` | Image, timeout, memory, CPUs |

## How it is tested (and what is not)

- The generator loop, static checks, target selection, prompt rendering and JUnit parsing
  are unit-tested. The Anthropic provider is tested against a stand-in client with the exact
  `beta.messages.create` shape: request parameters, response mapping, refusal, truncation,
  and API/connection errors.
- The **real Docker sandbox** is exercised by `tests/sandbox/`: pass/fail/xfail outcomes,
  no network, read-only repository and root filesystem, non-root user, no host environment,
  and timeout kill. The **whole loop** is exercised too: a scripted model first writes a
  wrong expectation, receives the real pytest failure, and then returns a corrected module
  with an `xfail` for a real `ZeroDivisionError`.
- The real Celery worker is tested end to end without any double: with no API key it marks
  the generation `failed` with a clear message.
- **Not yet verified: a live call to the Anthropic API.** No API key was available in the
  development environment. The first run with a real key should be treated as that
  verification (see the Roadmap).
