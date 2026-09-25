# ADR-0005: Deterministic, evidence-weighted detection for repository analysis

**Status:** Accepted

## Context

Phase 2 must identify languages, dependencies, frameworks, test setup and entry points. An
LLM could answer "what framework is this?", but its answers are not reproducible, cost money on
every run, and cannot be trusted as the basis for later deterministic stages (test selection,
regression comparison). The project rules require deterministic tools wherever they can do the
job.

## Decision

- Repository analysis is **purely deterministic**: file-system facts, `ast`, `tomllib`,
  `configparser`, `packaging` and `yaml.safe_load`. No LLM, no imports of analysed code, no
  subprocesses and no network.
- Every detection carries **evidence** (human-readable facts) and a **confidence** computed as
  the capped sum of fixed weights: dependency 0.5, import 0.3, structural signal 0.4. Anything
  below 0.3 is dropped.
- Structural signals come from the AST (assignments, calls, class bases), never from substring
  search.
- The result is a versioned document (`ANALYZER_VERSION`) stored as JSONB, one per run.

## Consequences

- Results are reproducible and explainable. Later AI stages can consume this profile as
  trusted context instead of re-deriving it.
- Unusual project layouts can be missed (for example dependencies computed at runtime in
  `setup.py`). Such cases are reported as `issues` rather than guessed.
- Weights are heuristics. They are covered by golden tests on real example repositories, so
  changing them is a visible, reviewed decision.
