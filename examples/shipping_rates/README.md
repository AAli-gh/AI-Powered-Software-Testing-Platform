# shipping_rates (weak tests)

Correct code with a test suite that **executes every line but checks too little**:
- no test sits on the 10 kg boundary;
- no test checks the exact express surcharge;
- nothing checks the 100 free-shipping threshold or rejects a weight of 0.

Line coverage looks good, but mutation testing shows what the tests would miss. The expected
survivors are listed in [`EXPECTED_MUTATION.md`](EXPECTED_MUTATION.md). Do not "fix" the tests:
they are a fixture.
