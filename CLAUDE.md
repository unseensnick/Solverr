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
- **playwright-captcha only ever touches a throwaway page.** Preparing a solver injects init scripts (one rewrites `Element.prototype.attachShadow`) that are required to click into a Turnstile's closed shadow root, but a Cloudflare interstitial will not clear while they are present, and Playwright cannot remove an init script. So the stealth engine solves interstitials on the context's own clean page and opens a second, disposable page only for a widget that needs clicking. Verified live: interstitial clears in ~3s without them and never in 40s with them; Turnstile fills its token in ~4s with them and never without.

## Key decisions (WHY)

- **Fork on FlareSolverr, not Byparr.** FlareSolverr's Chrome engine already clears the target sites and has sessions; Python 3.11 + a vendored undetected_chromedriver let the Camoufox/Playwright stack coexist. Byparr pins Python 3.14, too new for undetected_chromedriver.
- **Reliability is dominated by IP reputation, not the tool.** A residential proxy (`PROXY_URL`) is the biggest lever; warm-session cookie reuse is the second.
- **The consuming client keeps one shared session and never destroys it**, so the server-side reaper is what prevents leaked browsers (especially the heavier Camoufox ones).

## Where things live

- `src/flaresolverr.py` — entrypoint: logging setup (note the `force=True`), server, reaper start.
- `src/flaresolverr_service.py` — controller: `/v1` commands, engine selection + fallback, per-host memory, session commands.
- `src/engines/` — `base.py` (Engine + SolveResult), `chrome_engine.py`, `stealth_engine.py`.
- `src/async_runtime.py`, `src/session_reaper.py`, `src/sessions.py` — stealth event loop, idle reaper, Chrome session store.
- `src/detection.py` (shared challenge/title/selector lists), `src/config.py` (env), `src/postform.py`, `src/dtos.py`.
- `.claude/rules/workflow.md` — CHANGELOG + commit rules, release-cut. `code-quality.md` — coding principles. `security.md` / `error-handling.md` — path-scoped to `src/`.

## Don'ts

- Don't change the `"FlareSolverr is ready!"` banner (`flaresolverr_service.py`): clients detect session support by that string.
- Don't add `linux/386` / `linux/arm/v7` to the Docker build: Camoufox has no build for them.
