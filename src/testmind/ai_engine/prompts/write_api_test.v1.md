The test suite of this $framework application does not test the following endpoint well
enough.

Endpoint: $method $path
Path parameters: $params
Reads a request body: $body
Gap: $gap

What the new tests must do: $goal

How a test obtains the application:
$client

The handler (file `$handler_path`):
```python
$handler
```

An existing test module of the project that calls the application (for style and set-up;
its fixtures from conftest.py are NOT available to your module, create what you need in
the module itself):
```python
$example
```

Write a pytest test module with one or a few focused tests of this endpoint. Assert the exact
status code and the essential parts of the response. Every test must pass against the
application as it is.
