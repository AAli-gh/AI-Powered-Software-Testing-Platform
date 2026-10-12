# Expected issues

The web scanner (served so that `/crash.html` returns 500, `/api/price` returns 500, and
`/missing.html` is absent) should report:

| URL | kind | severity |
|---|---|---|
| `/missing.html` | not_found | medium |
| `/crash.html` | server_error | high |
| `/catalog.html` | js_error | medium (`undefinedFunction is not defined`) |
| `/` | failed_request | medium (`/api/price` → 500) |

`https://example.com/` is out of scope and only recorded as denied, never visited. The two
buttons on `/` are counted; the dead-button check (16b) will flag `#dead`.
