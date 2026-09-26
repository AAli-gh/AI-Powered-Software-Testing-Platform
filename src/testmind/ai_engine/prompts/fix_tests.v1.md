The test module you wrote for `$module` did not pass validation.

Problem report:
$feedback

Your previous test module:
```python
$code
```

Return the complete corrected test module. Fix mistakes in the tests themselves (wrong
imports, wrong expectations about documented or obviously intended behaviour, invalid pytest
usage). If a failure shows a genuine defect in `$module`, keep the test asserting the correct
behaviour and mark it `@pytest.mark.xfail(reason="possible bug: <one sentence>", strict=True)`
instead of weakening it. Remove any test you cannot make correct.
