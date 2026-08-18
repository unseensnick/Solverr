# Changelog

Solverr follows its own [Semantic Versioning](https://semver.org/), starting at 1.0.0. It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) 3.5.0; for history before this fork, see the upstream releases.

## [Unreleased]

### Fixes

- **A solved page is no longer handed back while the challenge is still running.** Cloudflare briefly removes the challenge from the page between rounds, and that gap could be mistaken for the challenge being over, which returned the waiting page instead of the site. A solve now confirms the challenge is really gone, and waits for the page it was hiding to load. Solving takes about a second longer as a result.

- **A session now browses through the proxy the request asked for.** A session named by a request before it existed, or rebuilt after its lifetime ran out, was created without one. Requests kept succeeding, so there was nothing to notice: they simply went out from the server's own address instead of through the proxy.

### Changes

- **Sites that show a checkbox to click are more likely to clear on the Camoufox engine.** The checkbox could only be found when the site embedded the widget itself; on Cloudflare's own full-page challenge it was invisible to the solver, so those requests fell through to the other engine or ran out of time. It is now located a second way that works on both.

### Other

- Updated the stealth browser to invisible-playwright 0.7.2. Measured against the previous version over 18 challenge solves: same success rate, same timings.

## [1.3.0]

### Additions

- **`LANG` now sets the browser language on both engines.** Only Chrome read it before. The value is normalized to a language tag first, so `en_US.UTF-8` reaches the browser as `en-US` instead of verbatim, and a value that isn't a language is ignored with a warning rather than passed through.

- **`BROWSER_TIMEZONE` pins the browser to a timezone.** Set it to an IANA zone like `Europe/Berlin` and both engines use it with no lookup, which is also how an offline deployment avoids the egress check. Left unset, the zone is derived from the exit IP as before.

- **`BROWSER_GEO` sets the browser's language and timezone together.** One tag like `de-DE` puts both engines in German and in `Europe/Berlin`, with no lookup at all, which is the shortest way to match a proxy that always exits the same country. The chosen timezone is written to the log, and `LANG` and `BROWSER_TIMEZONE` each still win for their own half.

### Fixes

- **A solve no longer fails because the browser's timezone could not be worked out.** Behind a proxy, an unreachable address-lookup service used to abort the whole request; it now falls back to the container's `TZ` with a warning in the log.
- **Both engines report the same timezone and the same language.** Camoufox followed the exit IP while Chrome reported the container's timezone and its own build's language, so the same request could place the browser in two different countries depending on which engine answered it, and a fallback could change it mid-session. Both now come from one lookup. Chrome also reports the two-entry `navigator.languages` a desktop browser sends, instead of a single entry.
- **A URL that returns JSON now comes back as JSON, whichever engine solved it.** The stealth engine used to return the browser's built-in JSON viewer page instead of the payload, so the same request gave usable JSON through Chrome and markup through Camoufox.
- **A solved response always reports the User-Agent that fetched it.** When the browser's user agent could not be read at startup, every response from that browser came back with an empty `userAgent`, which breaks reusing its cookies.

### Other

- The image ships the geolocation database used to work out the browser's timezone, instead of downloading it on the first solve. That adds about 120 MB to the image and removes a download from a fresh container's first request. The copy refreshes with each release.

## [1.2.2]

### Fixes

- **Interactive Turnstile checkboxes are clicked again on the stealth engine, and one that stays unsolved is no longer reported as solved.** The click had stopped working, and the engine answered "Challenge solved!" with an empty `turnstile_token` instead of failing, so a request never fell back to the other engine. A page gated by a widget now either comes back with its token or fails over to Chrome.

### Other

- The stealth browser now installs from a published release on PyPI (`invisible-playwright` 0.6.1) instead of a git checkout, so a rebuild resolves to a fixed version. The image no longer ships `git`.

## [1.2.1]

### Fixes

- **Solverr no longer opens `file://` or `data:` URLs, which could be used to read files from inside the container.** Anything able to reach the API could ask the browser for a local file and get the contents back in `solution.response`. Only `http://` and `https://` are accepted now.

### Other

- The image reports container health, so a wedged instance shows as unhealthy instead of up. The check only proves the API is answering, not that solving works.

## [1.2.0]

### Additions

- **A URL that serves a PDF now comes back as the actual file.** The stealth engine returns the PDF Base64-encoded with `solution.contentType: application/pdf` instead of the browser viewer's HTML, and the passthrough serves it under its real content type.

### Fixes

- **Cookies look the same whichever engine solved the request.** The stealth engine used to report an expiry the Chrome engine never did, so a client that stored them saw two different shapes for the same site. If you persist cookies, read `expiry` (absent for session cookies), the field FlareSolverr has always returned.
- **Interactive Turnstile checkboxes are now clicked reliably on the stealth engine.** Non-interactive interstitials already solved; this is the click-to-verify kind, which previously ran out the clock.
- **Destroying a session now closes every browser it opened**, instead of leaving one running until the idle reaper caught it.
- **A request that names a session stays on that session's browser.** It used to open a second one whenever another engine had last cleared that host, so the warmed-up cookies went unused.
- **A busy browser is no longer closed out from under an in-flight request** when the idle reaper or the session cap kicks in.
- **A challenge that resists solving now hands over to the other engine** rather than failing the whole request with a timeout.
- **A browser that fails to launch no longer leaks**, and the request explains what happened instead of returning an empty error.
- **A challenge that clears at just the wrong moment no longer fails the request.** Checking the page while it was navigating to the real content errored out, right at the point the solve had actually worked.

### Other

- The stealth engine's browser build is pinned to an exact commit, so rebuilding an image can't silently pick up a broken one.

## [1.1.1]

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
