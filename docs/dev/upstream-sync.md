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
| Byparr | `25194c3` | 2026-08-08 | The 2026-08-07/08 batch brought one behavior change worth having (CSP stripping, declined below), one Solverr already had (a networkidle timeout is non-fatal: every `wait_for_load_state` in `stealth_engine.py` is already wrapped), and one that does not apply (`maxTimeout` in milliseconds is Byparr adopting our contract). The rest is the `/load` endpoint, test-only dependency churn, and CI signing we have no equivalent of. |

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
- **CSP headers are left alone.** Playwright's Firefox transport runs `page.evaluate` as a main-world `eval()`, so a page whose CSP omits `unsafe-eval` blocks it (Chromium's CDP path is exempt, which is why the Chrome engine never sees this). Byparr strips the CSP headers off every document with `route.fetch` + `route.fulfill`, which sends the main document through Playwright's HTTP stack rather than Firefox's, changing the TLS and HTTP/2 fingerprint on the request Cloudflare inspects hardest, and forces them to track the final URL by hand because Juggler only routes the first request of a redirect chain. Solverr's own code does not need it: the user agent is read once on the blank page at context start, and the Turnstile token is read with `get_attribute`. Two mechanisms were measured on 2026-08-08 against the one place CSP did bite, playwright-captcha's shadow-root traversal, and neither helped: the `security.csp.enable=False` launch pref does not disable enforcement on Firefox 151 at all, and header stripping does not reach the policy that blocks, which belongs to the cross-origin Turnstile iframe rather than the page. Solverr solved that by not needing eval (see the widget click below), so there is nothing left to port.
- **The Turnstile checkbox is clicked by coordinate, and playwright-captcha is not in the free path at all.** Byparr hands the widget to playwright-captcha's ClickSolver. Its traversal into the widget's closed shadow root runs `evaluate_handle` (`solvers/click/common/shadow_root.py:53-55`), which the iframe's CSP blocks, so every nudge logged "call to eval() blocked by CSP" and the widget went unsolved. Solverr instead finds the widget through the frame tree (which sees an iframe inside a closed shadow root), takes its box with `frame_element().bounding_box()`, and clicks the checkbox with `page.mouse`. All protocol-level, so no CSP applies. That also retires the throwaway click page for the free path, since nothing injects init scripts there any more, and with it the race that let a widget be declared solved before it had rendered.
- **No `/load` endpoint.** Byparr serves Open WebUI's external web loader (article extraction via trafilatura). Solverr's public surface is `/v1` plus the optional passthrough; a second content-extraction API is a different product.
- **No `maxTimeout` alias in milliseconds.** Byparr added one for FlareSolverr drop-in compatibility. Solverr is native to that contract and already reads `maxTimeout` as milliseconds.

## Inherited from FlareSolverr, byte-identical

These need no review until upstream changes them. Verified identical on 2026-07-25:

- `src/undetected_chromedriver/` (the whole vendored package)
- `src/tests.py`, `src/tests_sites.py` (they carry upstream's site list; leave as-is so the files stay mergeable, and do not add to them)
- `html_samples/*.html`
- `src/utils.py`, `src/bottle_plugins/`

## Taken

- **URL scheme validation** (v1.2.1). Byparr enforces `http(s)://` at its model layer; Solverr validates in the controller before any engine sees the URL (`_validate_url`, `src/flaresolverr_service.py`). Worth knowing why it mattered: on the published 1.2.0 image a `file:///etc/passwd` request returned the file in `solution.response`, verified directly.
- **A Docker healthcheck** (v1.2.1). Byparr's shape, adapted: it honors `${PORT:-8191}` and uses the IPv4 literal, since `localhost` resolves to `::1` on an IPv6-enabled network while the server listens on IPv4 (the same trap Byparr hit in `885a24c`). FlareSolverr still has none.
- **The stealth browser from PyPI, pinned exactly.** Byparr moved `invisible-playwright` off a git dependency to a PyPI floor of `>=0.6.1` (`a0c4b1d`, `e298eb8`). Solverr pins `==0.6.1` rather than a floor, for the reason the old commit pin existed: this package carries the patched Firefox, so an unattended bump changes solving behavior while the build stays green. The commit pin it replaces was never as reproducible as it looked: it fixed `invisible-playwright` at 0.3.0 but left `invisible-core` to float, so both images resolve 18.13.0 today and a build tomorrow would not. 0.6.1 pins `invisible-core==18.13.0` outright. `git` left the image with it.

## Known gaps, not yet taken

- **The paid CAPTCHA API path still depends on main-world `eval`.** It injects its token with `page.evaluate` (`captchas/*/apply.py`), which the Turnstile iframe's CSP blocks under Firefox, so the escalation cannot land a token even when the service returns one. Only fires when `CAPTCHA_SOLVER` and `CAPTCHA_API_KEY` are set. The free click path had the same problem and no longer uses playwright-captcha at all (see the divergence above).
- **Real `solution.headers` on the stealth engine.** Byparr returns the response headers; Solverr sets `{}` to match the Chrome engine's `todo`. Now that the engine tracks the main-frame response for PDF detection, this is close to free. The catch: filling it in for stealth only would reintroduce the engine asymmetry that the v1.2.0 cookie fix removed, so it needs either a Chrome-side answer or a documented asymmetry.

## When you take something

1. Port it, adapting to the contract constraints above.
2. Add the CHANGELOG entry as usual.
3. Update the audited-through row here, and add a deliberate-divergence entry if you took part of a change but not all of it.
