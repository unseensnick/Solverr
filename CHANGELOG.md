# Changelog

Solverr follows its own [Semantic Versioning](https://semver.org/), starting at 1.0.0. It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) 3.5.0; for history before this fork, see the upstream releases.

## [Unreleased]

### Fixes

- **Following a site's own links through the passthrough now works.** A root-relative link (a details page, the next page) is routed to the default mirror instead of being refused, so downloads and pagination succeed.

### Other

- Passthrough no longer logs a traceback when a client disconnects mid-response.

## [1.1.0]

### Additions

- **A built-in passthrough proxy lets clients that would re-fetch the URL use the solved page directly.** Enable `PASSTHROUGH_ENABLED` with a host allow-list and point the client at the passthrough port; off by default.

### Fixes

- **Solved pages no longer trigger a redundant second solve.** Cloudflare's post-clearance beacon was mistaken for an unsolved challenge, so many requests fell back to the other engine and solved twice; sites that carry the beacon now solve about twice as fast.

## [1.0.0]

### Additions

- **A second solving engine clears the newer Cloudflare challenges Chrome can't.** Camoufox (an anti-detect Firefox) plus playwright-captcha handles Turnstile and Managed Challenges that headless Chromium gives up on.
- **Requests fall back to the other engine automatically when one is blocked or returns an unsolved page.** Solverr also remembers which engine cleared each host and tries it first next time.
- **Pick the engine per request with a new `engine` field** (`chrome`, `stealth`, or `auto`).
- **Optional paid CAPTCHA fallback for hard challenges.** Set `CAPTCHA_SOLVER` + `CAPTCHA_API_KEY` (2captcha/CapSolver) and Solverr escalates to it only when free solving fails; dormant otherwise.

### Changes

- **Idle browser sessions are now cleaned up on their own.** A background reaper closes sessions left idle past a timeout and caps how many run at once, so abandoned browsers no longer pile up.

### Other

- Dual-browser Docker image (Chromium + Camoufox) with a ghcr publishing workflow for amd64/arm64.
- Rewrote the README and repo workflows/issue templates for the fork; switched to Solverr's own SemVer.
- Fixed logging being suppressed after the stealth libraries loaded (no output reached `docker logs`).
- Relicensed under GPL-3.0 (Byparr's copyleft license, the stricter of the two upstreams); FlareSolverr's MIT notice preserved in `NOTICE`.
- Base image updated to Python 3.14.
