## Summary

<!-- What does this change, and why? Link the issue it addresses. -->

## How it was tested

<!-- The browser-free suite result, and anything you tried against a live site if the change touches solving. -->

## Checklist

See [CONTRIBUTING.md](../CONTRIBUTING.md) for what each item means.

- [ ] The browser-free suite passes: `PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src`
- [ ] Commit messages follow `type(scope): summary`, at most 72 characters, with no em dash, no bare `#N`, and no AI attribution
- [ ] No names of target sites and no scraping vocabulary in commits, docs, or code comments
- [ ] A change a client can notice lands for both engines, or the pull request names the capability one engine does not have
- [ ] The `/v1` request and response shape is unchanged, apart from new optional fields
- [ ] `CHANGELOG.md` has an `[Unreleased]` entry if someone running Solverr could notice the change, and none otherwise
