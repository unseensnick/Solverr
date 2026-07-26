# Upstream sync ledger

Solverr is a standalone fork with two upstreams. This file is the single owner of one question: **what has been taken from each of them, through which commit, and what is deliberately different.** Nothing else records that, so an audit that does not update this file gets re-run from scratch next time.

Run `/upstream-audit` to refresh it. The reference clones are siblings of this repo, read-only:

| Upstream | Clone | Role |
|---|---|---|
| FlareSolverr | `../FlareSolverr` | The base. The Chrome engine, the `/v1` API, sessions, and most of the surrounding code come from here. |
| Byparr | `../Byparr` | The origin of the stealth stack (Camoufox via invisible_playwright plus playwright-captcha). |
| byparr-proxy | `../byparr-proxy` | Idea credit only for the passthrough. No code taken. |

## Audited through

| Upstream | Commit | Date audited | Notes |
|---|---|---|---|
| FlareSolverr | `0f05ed8` | 2026-07-25 | Fork point was `bb6f439` (2026-07-10), off FlareSolverr 3.5.0. Chrome engine, sessions, and utils reviewed; see the inherited list below. |
| Byparr | `c42a353` | 2026-07-25 | Everything through the 2026-07-04 batch is either taken or deliberately declined. The only newer commit is a ruff dev-dependency bump, which does not apply (no ruff here). |

## Deliberately different

Cite one of these instead of re-arguing it. Change one only when the owner asks.

- **`solution.status` is hardcoded 200.** Byparr returns the real upstream status. Clients reject non-2xx, and the Chrome engine hardcodes 200 too, so a Cloudflare 403 challenge page would otherwise surface as a solve failure. A genuine block is raised as an error by the denied-detection path instead.
- **A timeout is HTTP 500 in the FlareSolverr error shape**, not Byparr's 408. Required by the `/v1` contract.
- **`solution.contentType` is emitted only when the body is not HTML.** Byparr always emits it, defaulting to `text/html`. Emitting it unconditionally would change every response, and the contract allows additive optional fields only.
- **Per-request proxy comes from the `proxy` field in the request body**, not Byparr's `X-Proxy-*` headers. Contract again.
- **playwright-captcha only ever runs on a throwaway page.** Byparr attaches its solver to the page it navigates. Preparing a solver injects an init script that rewrites `Element.prototype.attachShadow`, and a Cloudflare interstitial will not clear while that patch is present. Measured: cleared in 3.1s without it, never in 40s with it, while a Turnstile widget filled its token in 4.5s with it and never without. Playwright cannot remove an init script, hence the second page.
- **Sessions, the reaper, and per-host engine memory are Solverr-only.** Byparr launches a browser per request.
- **Detection lists are shared across both engines** from `src/detection.py`, carrying FlareSolverr's full title and selector sets. Byparr has a single challenge title and no access-denied handling.
- **Base image is `python:3.14-slim-bookworm`**, not Byparr's `ubuntu:24.04`. No effect on solving.

## Inherited from FlareSolverr, byte-identical

These need no review until upstream changes them. Verified identical on 2026-07-25:

- `src/undetected_chromedriver/` (the whole vendored package)
- `src/tests.py`, `src/tests_sites.py` (they carry upstream's site list; leave as-is so the files stay mergeable, and do not add to them)
- `html_samples/*.html`
- `src/utils.py`, `src/bottle_plugins/`

## Taken

- **URL scheme validation** (v1.2.1). Byparr enforces `http(s)://` at its model layer; Solverr validates in the controller before any engine sees the URL (`_validate_url`, `src/flaresolverr_service.py`). Worth knowing why it mattered: on the published 1.2.0 image a `file:///etc/passwd` request returned the file in `solution.response`, verified directly.
- **A Docker healthcheck** (v1.2.1). Byparr's shape, adapted: it honors `${PORT:-8191}` and uses the IPv4 literal, since `localhost` resolves to `::1` on an IPv6-enabled network while the server listens on IPv4 (the same trap Byparr hit in `885a24c`). FlareSolverr still has none.

## Known gaps, not yet taken

- **Real `solution.headers` on the stealth engine.** Byparr returns the response headers; Solverr sets `{}` to match the Chrome engine's `todo`. Now that the engine tracks the main-frame response for PDF detection, this is close to free. The catch: filling it in for stealth only would reintroduce the engine asymmetry that the v1.2.0 cookie fix removed, so it needs either a Chrome-side answer or a documented asymmetry.

## When you take something

1. Port it, adapting to the contract constraints above.
2. Add the CHANGELOG entry as usual.
3. Update the audited-through row here, and add a deliberate-divergence entry if you took part of a change but not all of it.
