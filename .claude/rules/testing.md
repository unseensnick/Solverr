---
alwaysApply: true
---

# Testing

- Verify behavior, not implementation. Don't assert mock call counts when output values would do.
- Run the specific test file after changes, not the full suite. Faster feedback, fewer tokens.
- Flaky test? Fix it or delete it. Never retry to make it pass.
- Prefer real implementations. Mock only at system boundaries (network, filesystem, clock, randomness).
- One assertion per test. Test names describe behavior. Arrange-Act-Assert. No `if` or loops in tests.
- Never assert only that a mock was called without verifying arguments.
- This project uses `unittest` + `webtest` (`src/tests.py`); the full suite launches a real browser and hits live sites, so it's slow and network-dependent. For fast feedback on non-solving changes, prefer `uv run --no-project python -m py_compile ...` and small targeted `unittest` runs over the whole suite.
