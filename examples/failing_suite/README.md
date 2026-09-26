# failing_suite (intentionally failing tests)

A tiny package whose test suite **fails on purpose, in every way TestMind's failure analysis
distinguishes**, so that the analysis has real pytest failures to work on. The expected result
is listed in [`EXPECTED_FAILURES.md`](EXPECTED_FAILURES.md). Do not "fix" these tests: they are
a fixture.
