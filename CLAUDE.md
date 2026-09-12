# Solverr

FlareSolverr fork with two solving engines and automatic fallback. Cloudflare/DDoS-GUARD bypass proxy speaking the FlareSolverr `/v1` API on port 8191. Python 3.14, `bottle` + `waitress` (synchronous WSGI). Lives alongside its two upstreams as read-only reference: `../FlareSolverr` (the Chrome engine's origin) and `../Byparr` (the Camoufox stack's origin).

## Commands

```bash
docker compose up -d --build         # build + run (image bundles both browsers, ~2.3 GB)
docker logs -f solverr               # logs (set LOG_LEVEL=debug for more)
PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src   # browser-free suite, seconds; CI runs it
bash .githooks/tests/run.sh          # the git hooks still reject what they claim to
bash .claude/hooks/tests/run-all.sh  # the Claude Code guard hooks, against their fixtures
uv run --no-project python -m py_compile src/*.py src/engines/*.py   # quick compile check
```

Python only through uv; there is no system Python. `src/tests.py` is upstream's suite: it needs a real browser and live sites. Whether a page still clears a real challenge is `/live-check`, never the unit tests.

## Working approach

- **Memory and `Handoff.md` are hypotheses, not facts.** A memory that names a function, file or flag is true only if it still exists in current code. When one turns out stale, surface it for pruning instead of acting on it.
- **Plan steps carry their check inline**, as `1. <step> -> verify: <check>`, so a step nothing can check is visible before it is built.
- **Reply length.** Default replies are a few sentences: the answer or outcome, the detail that matters, done. A full report is for when the owner asks for one, or for a `/scout` or `/code-research` deliverable, which has its own cap in [.claude/rules/plan-output.md](.claude/rules/plan-output.md).

## Architecture in brief

Two engines behind one interface: `chrome` (Selenium + vendored undetected_chromedriver, the default) and `stealth` (Camoufox via invisible_playwright, on one background event-loop thread). The controller falls back between them and remembers per host which one cleared it. What both engines must do the same way lives once in the shared spine (`assembly.py`, `pipeline.py`, `budget.py`, `sessions.py`); each engine is an adapter over a clearing core derived from its upstream. Several constraints look wrong until you know what they were measured against (the coordinate Turnstile click, no `page.evaluate` against a challenge page, the second look before a challenge counts as cleared, the even `maxTimeout` split, the `quote()` calls in `postform.py`): read [.claude/rules/architecture.md](.claude/rules/architecture.md) before touching any of them. It loads on its own when you work in `src/`.

## Key decisions (WHY)

- **Fork on FlareSolverr, not Byparr.** FlareSolverr's Chrome engine already clears the target sites and has sessions, and its vendored undetected_chromedriver lets the Camoufox/Playwright stack run beside it in one Python 3.14 image.
- **Reliability is dominated by IP reputation, not the tool.** A residential proxy (`PROXY_URL`) is the biggest lever; warm-session cookie reuse is the second.
- **The consuming client keeps one shared session and never destroys it**, so the server-side reaper is what prevents leaked browsers (especially the heavier Camoufox ones).

## Where things live

- `src/flaresolverr.py`: entrypoint. Logging setup (note the `force=True`), server, reaper start.
- `src/flaresolverr_service.py`: controller. `/v1` commands, engine selection and fallback, per-host memory, session commands.
- `src/assembly.py`, `src/pipeline.py`, `src/budget.py`: the shared spine. What a response contains and in what order, the page verdict and the navigate-cookies-reload order, the solve deadline. The first two are sans-io generators (they yield what to read, the engine supplies how) because one engine is synchronous and the other asynchronous; see `.claude/rules/engine-layer.md` before reshaping them.
- `src/engines/`: `base.py` (Engine + SolveResult), `chrome_engine.py`, `stealth_engine.py`. Each is an adapter over an upstream-derived clearing core, which is the one thing the spine never takes over.
- `src/async_runtime.py`, `src/session_reaper.py`, `src/sessions.py`: stealth event loop, idle reaper, and the `SessionStore` both engines use (each holds its own instance; the lifecycle rules live once).
- `src/detection.py` (shared challenge/title/selector lists), `src/geo.py` (browser timezone and language for both engines), `src/config.py` (env, including `env_proxy`), `src/postform.py`, `src/redact.py` (what the `/v1` request and response lines, the Chrome proxy debug line and the geo warning strip before they are written), `src/dtos.py` (request DTOs plus the type validation that makes their annotations binding).
- `src/engine_fakes.py`: drives either engine browser-free from one neutral `World`, for `test_engine_conformance.py`. Imported, not collected.
- `.claude/rules/engine-layer.md`: **the law for anything touching an engine**. Write-once and its one exit, which code is upstream's and which is ours, capability slots, the pin-once ladder, and how deep the seam goes per surface. Loads every session.
- `.claude/rules/architecture.md`: the non-obvious architecture and its measured constraints, path-scoped to `src/`.
- `.claude/rules/workflow.md`: CHANGELOG and commit rules, merging, release-cut, public-facing naming, the git hooks and every check they run. `code-quality.md`: coding principles. `testing.md`: test rules and commands. `security.md` / `error-handling.md`: path-scoped to `src/`. `plan-output.md`: how a findings report or plan is structured. `prose-style.md`: sentence-level writing for every output.
- `CONTRIBUTING.md` and `.github/pull_request_template.md`: the same standard, written for outside contributors. Keep them in step with `workflow.md`.
- `docs/dev/engine-layer-architecture.md`: the rationale behind the law. The divergence measurements against both upstreams, the target seam, the sequencing, and every ruling with the evidence it rests on. Read it before designing anything forward-looking.
- `docs/dev/upstream-sync.md`: what has been taken from FlareSolverr and Byparr, through which commit, and every deliberate divergence with its reasoning. Read it before calling something drift.
- `.githooks/`: tracked `commit-msg` and `pre-commit` hooks, plus `tests/run.sh`, which proves each rule still rejects a real violation. Activate with `git config core.hooksPath .githooks`. CI runs the same checks (the Standards and Tests workflows).
- `.claude/hooks/`: the guards that screen tool calls before they run, so an unexplained `Blocked:` message comes from here. `block-dangerous-commands.sh` covers **both Bash and PowerShell** (matching only one lets a command through the other tool) and refuses a push to `main`, a bare force push (`--force-with-lease` is allowed), merging a PR (`gh pr merge` or through `gh api`), reading secret files through the shell, and the usual destructive deletes. Merging is always the owner's call.
- `.claude/agents/`: the four review subagents to spawn with the `Agent` tool: `code-reviewer`, `doc-reviewer`, `performance-reviewer`, `security-reviewer`. `/pr-review` runs all four in parallel.

## Skills

- `/scout`: investigate one non-trivial task, then produce its plan, grounded in `file:line` citations. Use before porting from an upstream or touching the engines, sessions, or the controller.
- `/code-research`: fan-out research for a broad question spanning many files. `/scout` is for one concrete task.
- `/deep-audit`: read-only, many-agent audit of a whole range (default the branch against `main`) before a PR or a release. Maps the range and stops for approval, runs fourteen lenses including engine parity and two-ends tracing, refutes every finding before it counts, mutation-checks the range's new tests, and reports. Refuted findings go in its ledger. It never fixes anything.
- `/upstream-audit`: compare against FlareSolverr and Byparr, classify every difference as covered, missing, or deliberate, and check `/v1` compatibility and the dependency pins. Proposes the ledger update.
- `/live-check`: verify a change against live challenges through an isolated container. The unit tests cannot tell you whether a page still clears; this can.
- `/release`: cut a version end to end: decide the bump, preflight, tag, then verify the workflows and the published image digests.
- `/session-handoff`: rewrite `Handoff.md` from verified state, then bring the CHANGELOG, dependent docs, and memory store in line with it.
- `/pr-review`: review changes via the four specialist agents in parallel.
- `/tighten`: trim verbose docs and WHAT comments without losing vital info. Always plans first.
- `/context-budget`: what this `.claude/` config costs per turn.

## Don'ts

- Don't change the `"FlareSolverr is ready!"` banner (`flaresolverr_service.py`): clients detect session support by that string.
- Don't add `linux/386` / `linux/arm/v7` to the Docker build: Camoufox has no build for them.
