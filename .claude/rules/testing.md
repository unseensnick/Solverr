---
alwaysApply: true
---

# Testing

- Verify behavior, not implementation. Don't assert mock call counts when output values would do.
- **A rule that must hold for both engines is pinned once, not twice.** Prefer a test over the shared spine both engines call; where the engines are genuinely separate, write one case in `src/test_engine_conformance.py`, which drives each engine through `src/engine_fakes.py`, instead of a hand-maintained pair. A pair drifts. Full rule: [engine-layer.md](engine-layer.md).
- Run the specific test module after a change (`PYTHONPATH=src uv run --no-project python -m unittest test_request_validation`), then the whole browser-free suite before calling it done: `PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src`. Never a hand-picked list of modules as the final check: it skips the conformance suite.
- Flaky test? Fix it or delete it. Never retry to make it pass.
- Prefer real implementations. Mock only at system boundaries (network, filesystem, clock, randomness). Patch a polling sleep with a plain function, never a `MagicMock`: the mock records every call and a polling wait turns into unbounded memory that looks like a hang.
- One assertion per test. Test names describe behavior. Arrange-Act-Assert. No `if` or loops in tests; parameterize instead. Here that means `self.subTest` over a fixed tuple of cases or engines, the shape the conformance suite uses, and the one sanctioned loop.
- Never assert only that a mock was called without verifying arguments.
- **A new test is not done until it has failed.** Delete the production clause it names, see it red, restore the clause (engine-layer.md, "Verify by mutation").
- `src/tests.py` is upstream's suite: it launches a real browser and hits live sites, so it is slow and network-dependent, and it cannot run in CI. The `src/test_*.py` modules are the browser-free suite CI runs on every pull request. Neither can tell you whether a page still clears a real challenge; that is `/live-check`.
