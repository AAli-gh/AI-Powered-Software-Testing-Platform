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
buttons on `/` are counted.

## When the scan exercises controls (`interact=True`)

Clicking each safe button and submitting each form adds:

| URL | control | kind | severity |
|---|---|---|---|
| `/` | `#dead` ("Does nothing") | dead_button | medium |
| `/interactive.html` | `#boom` ("Break") | js_error | medium (click throws) |
| `/interactive.html` | `#signup` (POST `/submit` → 500) | form_error | high |

Controls that are *not* flagged: `#works` (navigates), `#fetcher` (issues a request), `#mutate`
(changes the page), and the submit input (submits the form). `#danger` ("Delete account") is
never clicked — its destructive label is recognised and skipped.
