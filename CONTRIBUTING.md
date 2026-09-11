# Contributing to Solverr

Solverr is a personal fork, maintained in spare time, so a pull request may sit for a while or come back with changes. Bug reports are always useful. For anything bigger than a small fix, open an issue first so the approach can be agreed on before you put in the work.

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting a bug

Use the [bug report form](https://github.com/unseensnick/Solverr/issues/new?template=bug_report.yml). Say which image tag you run, which engine was involved, and whether you use a proxy, and attach a log taken with `LOG_LEVEL=debug`. Check the README's Troubleshooting section first: a site that blocks your IP address fails on every solver, and a residential proxy fixes that where no code change can.

## Setting up

You need [uv](https://docs.astral.sh/uv/) and Git. Solverr does not use a system Python; everything runs through uv.

```bash
git clone https://github.com/unseensnick/Solverr.git
cd Solverr
git config core.hooksPath .githooks
uv venv --python 3.14
uv pip install -r requirements.txt -r test-requirements.txt
```

The `core.hooksPath` line turns on the same commit checks CI runs, so a problem shows up when you commit instead of after you push. To run the full service with both browsers, use Docker: `docker compose up -d --build`.

## Running the tests

```bash
PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src
```

This is the browser-free suite. It takes seconds, and CI runs it on every pull request. It cannot tell you whether a page still clears a real challenge, so if your change touches solving, say in the pull request what you tried against a live site. The maintainer runs a live check before anything touching solving is released.

## The two engines

Every request is served by one of two engines: `chrome` (Selenium with undetected-chromedriver, from FlareSolverr) or `stealth` (Camoufox, from Byparr). The rule that keeps them from drifting apart:

- **A change a client can notice lands for both engines in the same pull request.** That covers the `/v1` response, request parameters, and what a configuration variable does. The only exception is a browser-automation capability one engine genuinely does not have; name it in the pull request.
- **Put a shared rule in the shared code, not in one engine.** `src/assembly.py`, `src/pipeline.py`, `src/budget.py` and `src/sessions.py` hold what both engines must do the same way.
- **Test it once for both.** A behaviour both engines must share gets a test in `src/test_engine_conformance.py`, which runs it against each engine.

The full rule, with its reasoning, is [.claude/rules/engine-layer.md](.claude/rules/engine-layer.md).

## Keeping the API compatible

Solverr is a drop-in replacement for FlareSolverr, so clients built for FlareSolverr must keep working:

- Add optional fields only. Never rename, retype, or remove a field in the request or the response.
- Keep the `"FlareSolverr is ready!"` banner exactly as it is. Clients detect session support by it.
- Only `http://` and `https://` URLs may reach a browser.

## Commit messages

CI checks every commit in a pull request with [.githooks/commit-msg](.githooks/commit-msg), so these are hard requirements, not style advice:

- **The subject is `type(scope): summary`.** The type is one of `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `build`, `ci`, `style`, `revert`. The scope is optional and names the area (`chrome`, `stealth`, `sessions`, `api`, `docker`). Write the summary in the imperative, in lower case, with no trailing period.
- **The subject is at most 72 characters.**
- **No em dash anywhere in the message.** Use commas, parentheses, periods, or colons.
- **No bare `#123`.** It silently links to an issue in this repository. Write `owner/repo#123` instead, for example `FlareSolverr/FlareSolverr#1626`.
- **No AI attribution.** No `Co-authored-by` trailer naming an AI tool and no "Generated with" footer. A `Co-authored-by` trailer for a person is fine.
- **No names of the sites you point Solverr at, and no scraping vocabulary.** Write `example-site.tld` or "a Cloudflare-gated site" instead.
- **A change that is more than a one-liner gets a body.** Lead with one or two plain sentences on what changed and why it matters, then bullets.

For example, `Fixed the cookie bug.` is rejected, and `fix(stealth): accept a cookie without a domain` passes.

If a commit is rejected, reword it with `git commit --amend`, or `git rebase -i` for an older one. If you would rather not, say so in the pull request: the maintainer can reword it when merging, and your name stays on the commit.

## The CHANGELOG

Add a bullet to `CHANGELOG.md` under `## [Unreleased]` only when someone running Solverr, or calling its API, could notice the change. Put it under `Additions`, `Changes`, or `Fixes`, and lead with a bold headline that says what the user gets and ends in a period. The bold headline is the entire release note, so anything a deployer must act on (a new variable, a changed default) goes inside it. Tests, CI, documentation and tooling changes get no entry. If you are unsure, leave it out and the maintainer will add it.

## Upstream code

Solverr still takes changes from both of its upstreams, [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) and [Byparr](https://github.com/ThePhaseless/Byparr). [docs/dev/upstream-sync.md](docs/dev/upstream-sync.md) records what has been taken and what is deliberately different; read it before porting something or calling a difference a bug. Some files are kept byte-identical to FlareSolverr so they stay mergeable, and should not be edited: `src/undetected_chromedriver/`, `src/tests.py`, `src/tests_sites.py`, `src/bottle_plugins/`, and `html_samples/`.

## How pull requests are merged

The maintainer merges with a merge commit, so your commits land on `main` as you wrote them, under your name.

## Claude Code configuration

`CLAUDE.md` and `.claude/` configure [Claude Code](https://claude.com/claude-code) for this repository. You do not need them to contribute; they hold the same rules as this file, in more detail.

## License

Solverr is licensed under the [GNU General Public License v3.0](LICENSE), and contributions are accepted under the same license.
