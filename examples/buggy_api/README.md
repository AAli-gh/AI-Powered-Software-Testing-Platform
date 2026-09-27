# buggy_api

A Flask book catalogue whose tests only cover the happy path, and whose handlers have
deliberate defects. It exercises TestMind's API testing (Phase 10): endpoint discovery,
API coverage from the requests the tests send, and the deterministic probes. The exact
expected results are in [EXPECTED_API.md](EXPECTED_API.md).
