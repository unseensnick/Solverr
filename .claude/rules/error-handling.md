---
paths:
  - "src/**"
---

# Error Handling

- Engines raise on failure; the controller (`flaresolverr_service.py`) turns that into the FlareSolverr error shape (`status: "error"`, `message`, HTTP 500). Keep that shape; don't leak raw tracebacks into the response body.
- Preserve the existing error messages the controller matches on (e.g. `"Error solving the challenge. ..."`, `"session ... not found"`): clients and the fallback logic depend on them.
- Don't swallow errors silently. Browser/teardown paths that intentionally ignore failures (`_teardown`, reaper) must still `logging.debug(..., exc_info=True)`, never a bare `except: pass`.
- Solving is best-effort with retry built in: the controller falls back to the other engine on failure or an unsolved-challenge page. Add new failure handling at that layer, not by silencing an engine.
- Every coroutine on the stealth event loop must be awaited or scheduled through `async_runtime`; no floating tasks that drop exceptions.
