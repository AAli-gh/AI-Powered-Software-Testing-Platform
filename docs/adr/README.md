# Architecture Decision Records

Short records of significant decisions: the context, the decision and its consequences.

| # | Decision | Status |
|---|---|---|
| [0001](0001-technology-stack.md) | Technology stack | Accepted |
| [0002](0002-single-package-src-layout.md) | Single `testmind` package with `src/` layout | Accepted |
| [0003](0003-sandboxed-execution.md) | Execute repository code only in sandbox containers | Accepted (implementation in Phases 5 and 13) |
| [0004](0004-test-against-real-services.md) | Test against real PostgreSQL/Redis instead of mocks | Accepted |
| [0005](0005-evidence-based-deterministic-detection.md) | Deterministic, evidence-weighted detection for repository analysis | Accepted |
| [0006](0006-llm-proposes-tools-decide.md) | The LLM proposes, deterministic tools decide (test generation) | Accepted |
| [0007](0007-own-function-coverage-mapping.md) | TestMind maps coverage onto functions itself | Accepted |
| [0008](0008-structured-failure-capture.md) | Capture failures as structured data inside the sandbox | Accepted |
| [0009](0009-read-only-git-with-dulwich.md) | Read git history with dulwich, never with the git command line | Accepted |
| [0010](0010-single-container-mutation-driver.md) | Run all mutants of a job in one sandbox container | Accepted |
| [0011](0011-observe-api-usage-inside-the-application.md) | Observe API usage inside the application, and cross-check static discovery with it | Accepted |
