The existing test suite of `$module` (file `$path`) does not notice the following change to the
code. Every test that executes the changed line still passes with it.

Function: $function (line $line)
Original code:    $original
Changed to:       $replacement

The line in context (the original code):
```python
$context
```

Tests that execute this line today:
$tests

Write a pytest test module with one or a few focused tests that PASS on the original code and
FAIL when the change above is applied. Test the documented or obviously intended behaviour
that the change breaks (for example the exact boundary or value), not implementation details.
If you believe the change cannot be observed from outside (an equivalent change), still write
the most specific test you can and say so in a comment at the top of the module.

Full source of `$path`:
```python
$source
```
