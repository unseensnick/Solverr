# Solverr

FlareSolverr fork with two solving engines and automatic fallback. Cloudflare/DDoS-GUARD bypass proxy speaking the FlareSolverr `/v1` API on port 8191. Python 3.14, `bottle` + `waitress` (synchronous WSGI). Lives alongside its two upstreams as read-only reference: `../FlareSolverr` (the Chrome engine's origin) and `../Byparr` (the Camoufox stack's origin).

## Commands

```bash
docker compose up -d --build         # build + run (image bundles both browsers, ~2.3 GB)
docker logs -f solverr               # logs (set LOG_LEVEL=debug for more)
uv run --no-project python -m py_compile src/*.py src/engines/*.py   # quick compile check
uv run python -m unittest src.tests  # test suite (unittest + webtest; needs a browser)
```

## Architecture (non-obvious)

- Two engines behind one interface (`engines/base.py`): `chrome` (Selenium + vendored undetected_chromedriver, the default) and `stealth` (Camoufox via invisible_playwright + playwright-captcha). The controller auto-falls-back between them and remembers per-host which one cleared it.
- The stealth engine is async Playwright running on ONE background event-loop thread (`async_runtime.py`); persistent Camoufox contexts (sessions) live there so their cookies survive across requests. The server itself is synchronous.
- Sessions: each engine keeps its own pool; a background reaper (`session_reaper.py`) closes idle browsers. Solve once, reuse the cookie many times.
- Escalation ladder for an `auto` request: Chrome → Camoufox click-solve → (optional, dormant) paid CAPTCHA API.
- **A Turnstile checkbox is clicked by coordinate, with no JS evaluation.** The widget's iframe sits in a closed shadow root, so `query_selector` cannot find it, but `page.frames` lists it anyway; `frame_element().bounding_box()` gives its rect and `page.mouse` clicks the checkbox. This exists because playwright-captcha's shadow-root traversal uses `evaluate_handle`, and the iframe's CSP blocks eval under Firefox, which silently broke widget solving. Cloudflare's own interstitial builds the widget itself and its frame reports an empty URL, so when no frame matches, the rect comes from the nearest ancestor `div` of the token input instead, which is in the light DOM. Do not reach for `page.evaluate` to measure any of this: running page scripts against a live challenge makes Cloudflare reissue it.
- **A challenge is only over once a clear reading survives a second look.** Cloudflare drops the challenge markup while it issues the next round, so believing the first clear reading returns an intermediate challenge page.
- **`maxTimeout` is one budget for the whole request, split evenly across the planned engines.** It used to be handed to each engine in full, so a fallback could take twice as long as asked and trip the caller's own timeout. An even share is what makes the fallback reachable: giving the first engine everything let it spend the lot, and a request that used to succeed in 133s failed at 120s with the second engine skipped. A quick first engine costs the fallback nothing, since the fallback inherits everything unspent.
- **The POST form is carried to the browser as a `data:text/html,` URL, so its fields are percent-encoded and must stay that way** (`postform.py`). The browser URL-decodes the document before the HTML parser sees it, so a value holding a bare `%` or `#` is otherwise re-read as an escape or truncates the document at the fragment. The `quote()` calls look like double-encoding and are not: removing them breaks POST for those values, measured against a live echo service.
- **Solverr resolves the browser's timezone and language itself (`geo.py`), and hands both engines the same pair.** Left alone, the stealth stack resolves both from the exit IP on every launch, inside the library, uncached, and raises behind a proxy when the lookup fails, which kills the launch; Chrome derived neither, so the two engines disagreed about the country. Passing concrete values returns before that fatal branch. They travel together because the pairing is what a site checks. Chrome follows via `Emulation.setTimezoneOverride` (which moves its ICU clock rather than patching `Intl` in the page) and `--accept-lang`. A failed lookup falls back to `TZ` and `en-US`: a wrong zone still solves, no browser does not.
- **playwright-captcha only ever touches a throwaway page**, and only the paid escalation reaches it now. Preparing a solver injects init scripts (one rewrites `Element.prototype.attachShadow`) that a Cloudflare interstitial will not clear while they are present, and Playwright cannot remove an init script. Verified live: an interstitial clears in ~3s without them and never in 40s with them.

## Key decisions (WHY)

- **Fork on FlareSolverr, not Byparr.** FlareSolverr's Chrome engine already clears the target sites and has sessions; Python 3.11 + a vendored undetected_chromedriver let the Camoufox/Playwright stack coexist. Byparr pins Python 3.14, too new for undetected_chromedriver.
- **Reliability is dominated by IP reputation, not the tool.** A residential proxy (`PROXY_URL`) is the biggest lever; warm-session cookie reuse is the second.
- **The consuming client keeps one shared session and never destroys it**, so the server-side reaper is what prevents leaked browsers (especially the heavier Camoufox ones).

## Where things live

- `src/flaresolverr.py` — entrypoint: logging setup (note the `force=True`), server, reaper start.
- `src/flaresolverr_service.py` — controller: `/v1` commands, engine selection + fallback, per-host memory, session commands.
- `src/engines/` — `base.py` (Engine + SolveResult), `chrome_engine.py`, `stealth_engine.py`.
- `src/async_runtime.py`, `src/session_reaper.py`, `src/sessions.py` — stealth event loop, idle reaper, Chrome session store.
- `src/detection.py` (shared challenge/title/selector lists), `src/geo.py` (browser timezone for both engines), `src/config.py` (env), `src/postform.py`, `src/dtos.py`.
- `.claude/rules/workflow.md` — CHANGELOG + commit rules, release-cut, public-facing naming, git hooks. `code-quality.md` — coding principles. `security.md` / `error-handling.md` — path-scoped to `src/`. `plan-output.md` — how a findings report or plan is structured. `prose-style.md` — sentence-level writing for every output.
- `docs/dev/upstream-sync.md` — what has been taken from FlareSolverr and Byparr, through which commit, and every deliberate divergence with its reasoning. Read it before calling something drift.
- `docs/dev/loops.md` — the port loop's contract: what the manager and worker each own, what they may not do, the three verification gates, and the eligibility rules that keep the worker away from the engines.
- `.githooks/` — tracked commit-msg and pre-commit hooks. Activate with `git config core.hooksPath .githooks`.

## Skills

- `/scout` — investigate one non-trivial task, then produce its plan, grounded in `file:line` citations. Use before porting from an upstream or touching the engines, sessions, or the controller.
- `/upstream-audit` — compare against FlareSolverr and Byparr, classify every difference as covered, missing, or deliberate, and check `/v1` compatibility. Updates the sync ledger.
- `/port-scan` — manager for the upstream port loop. Triages new Byparr and FlareSolverr commits into labeled issues. No file-writing tools by design. `--dry-run` files nothing.
- `/audit-scan` — manager for the audit and bug-fix loop. Audits one dimension per run and files only findings that survived an attempt to refute them. Same containment. `--dry-run` files nothing.
- `/loop-work` — the worker both managers feed. Takes one `loop:ready` issue, works it in its own worktree and branch, fixes every site the issue lists, proves it with three gates, opens a draft PR. Never merges. `--dry-run` mutates nothing.
- `/live-check` — verify a change against live challenges through an isolated container. The unit tests cannot tell you whether a page still clears; this can.
- `/release` — cut a version end to end: decide the bump, preflight, tag, then verify the workflows and the published image digests.
- `/session-handoff` — rewrite `Handoff.md` from verified state, then bring the CHANGELOG, dependent docs, and memory store in line with it.
- `/pr-review` — review changes via the four specialist agents in parallel.
- `/tighten` — trim verbose docs and WHAT comments without losing vital info. Always plans first.
- `/context-budget` — what this `.claude/` config costs per turn.

## Don'ts

- Don't change the `"FlareSolverr is ready!"` banner (`flaresolverr_service.py`): clients detect session support by that string.
- Don't add `linux/386` / `linux/arm/v7` to the Docker build: Camoufox has no build for them.
