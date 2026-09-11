# Changelog

Solverr follows its own [Semantic Versioning](https://semver.org/), starting at 1.0.0. It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) 3.5.0; for history before this fork, see the upstream releases.

## [Unreleased]

### Fixes

- **Form POST requests from Prowlarr work again, instead of failing with "Request parameter 'headers' must be a list".** 1.6.0 started checking the type of the deprecated `headers` field, which Solverr has never read and which clients send as an object; it is accepted in any shape again and still ignored.

## [1.6.0]

### Additions

- **`RESPONSE_HEADERS=true` returns the page's real response headers in `solution.headers` instead of an empty map.** Both engines or neither, so the answer never depends on which one solved the request. Off by default, because the field has been empty since the fork and turning it on changes what the Chrome engine's browser does on every request.
- **Slow sites can be given more patience per attempt with `BROWSER_WAIT_TIMEOUT` (default 1 second).** Applies to the Chrome engine, which waits a fixed moment for the challenge to clear before retrying; the stealth engine already polls until the request's own deadline. Raising it never extends `maxTimeout`.

### Fixes

- **Cookies a page sets from its own JavaScript during `waitInSeconds` are now returned instead of missed.** Both engines read the cookie jar before that wait, so a request could get back the page that set a cookie together with a cookie list that did not have it.
- **A `tabs_till_verify` request no longer loses the Turnstile checkbox after its first failed attempt.** Each retry added another focusable element to the page, which moved the checkbox further along the tab order every time, so only the first attempt could reach it.
- **A `tabs_till_verify` request now finds a Turnstile widget that appears after the page has loaded, instead of answering "Challenge not detected!" with no token.** The widget was looked for once, the instant navigation finished, which is before the site has injected it; it now gets up to 5 seconds to show up. A read that races the page re-rendering no longer fails the whole request either.
- **A Turnstile checkbox that never yields a token no longer holds the request for its entire `maxTimeout`.** Pressing now stops in time to return the page and let the other engine try, rather than retrying until the budget runs out.
- **A request that sends both `cookies` and `tabs_till_verify` now returns a token for the page it actually hands back.** The token was read before the cookie reload, so it described a document that had already been replaced.
- **A malformed `proxy` is now refused instead of quietly sending the request unproxied from the server's own address.** A proxy given as a plain string, or as an object with no `url`, used to read as "no proxy" and the solve went ahead and reported success, so nothing revealed that the traffic never went through it.
- **Proxy credentials are no longer left behind on disk when a browser fails to start.** The temporary extension carrying the proxy username and password was only cleaned up after a successful launch.
- **A browser behind an authenticated proxy now reports the timezone and language of the country it actually exits from.** The startup lookup that resolves them left the proxy credentials out, so with `PROXY_USERNAME` set it was refused and the fallback was cached for every request after it, leaving the browser claiming a country its address did not match.
- **Cookies sent as `{"name": ..., "value": ...}` now work on the Camoufox engine instead of failing the request.** That is the shape this README documents and the one FlareSolverr clients send, and it worked on the Chrome engine, so a request carrying one either wasted an engine attempt before falling back or failed outright when the Camoufox engine was pinned. Cookies without a domain now apply to the page being requested, which is what the Chrome engine already did.
- **A session is no longer closed out from under a request that just picked it up.** Two separate moments allowed it: replacing a session that had outlived its lifetime, where the check for whether anything was using it and the replacement were separate steps, and simply being handed a session, which stayed idle for an instant after being found and so could be closed by the idle cleanup before the request could claim it. Either one failed a solve with an error the caller could do nothing about. Both engines were affected, and the stealth engine did not lock the first check at all.
- **A request parameter of the wrong type is now refused by name instead of misbehaving or failing obscurely.** `"returnOnlyCookies": "false"` was truthy and dropped the response body, a numeric `url` reported a Python type rather than the parameter, and a bad `waitInSeconds` failed only after the challenge had already been solved. A misspelled parameter is now logged as ignored rather than passing silently.

### Other

- Bumped Selenium, requests, certifi, websockets, packaging, prometheus-client, and xvfbwrapper to the versions upstream FlareSolverr now pins.
- The two engines now share the rules they used to implement separately: how a response is assembled, what counts as a challenge, when the page is reloaded, and how much of the budget is kept back to answer with. No change to what a request returns; a change to one engine can no longer miss the other.

## [1.5.0]

### Changes

- **A request's `maxTimeout` is now capped at 180000 ms, raised with `MAX_TIMEOUT_MS`.** Nothing bounded it above, so a single request could hold a browser for as long as the caller asked, and because the session it was using counted as busy the whole time, the reaper could not reclaim that browser either. A larger value is clamped with a warning rather than refused, so callers already asking for more keep working.
- **The passthrough cache now holds at most 256 MB, set with `PASSTHROUGH_CACHE_MAX_BYTES`, and never caches a body larger than a quarter of that.** It expired bodies on age but never limited how many it held at once, so a client working through many pages inside one cache window could pin all of them in memory, and large non-HTML documents counted for far more than pages do. Entries closest to expiry are evicted first. Lower the ceiling far enough and ordinary pages stop being cached at all, which is the quarter rule doing its job rather than caching being broken.
- **Prometheus metrics, when enabled, now report at most 100 distinct domains, and every host past that as `other`.** Prometheus keeps one time series per label value for as long as the process runs and nothing evicted them, so pointing Solverr at many hosts grew the registry and the exported payload without limit. The exporter is off unless `PROMETHEUS_ENABLED=true`.

## [1.4.0]

### Fixes

- **A solved page is no longer handed back while the challenge is still running.** Cloudflare briefly removes the challenge from the page between rounds, and that gap could be mistaken for the challenge being over, which returned the waiting page instead of the site. A solve now confirms the challenge is really gone, and waits for the page it was hiding to load. Solving takes about a second longer as a result.

- **A `request.post` to a URL containing a double quote now reaches that URL.** The quote ended the form's target early, so the request went to a truncated address and was sent as a GET instead of a POST, losing both the rest of the URL and the method.

- **The passthrough now answers before an indexer app gives up on it.** `PASSTHROUGH_TIMEOUT_MS` drops from 120000 to 90000, under the roughly 100 seconds those apps wait. A solve that outlived that was recorded as an indexer failure and put the indexer into backoff, so one slow page disabled it for everything until the backoff cleared. Set the variable if you want the old value.

- **`maxTimeout` now covers the whole request, not each engine in turn.** When a request fell back to the other engine, that engine started a fresh full timeout, so a 60 second request could take 120. Measured against an unreachable host: 44.4 seconds for a 20 second request before, 21.6 after. Clients that set their own timeout, like the *arr apps, were hitting it while Solverr still thought it was inside the budget. With fallback on, each engine gets an even share of what is left, so raise `maxTimeout` if a site needs a long solve on a single engine.

- **A misspelled `engine` is refused instead of being answered by the default engine.** `engine: "stelth"` used to fall through and solve on whichever engine was primary, so a client that meant to pin one never found out it hadn't. The accepted values are `chrome`, `stealth`, and `auto`.

- **`session_ttl_minutes` has to be a positive number of minutes.** A negative value made every session compare as already expired, so the browser was rebuilt on every request and sessions quietly stopped being sessions.

- **A `url` has to start with `http://` or `https://` exactly.** `" https://example-site.tld"` and `"https:/example-site.tld"` were accepted and left to the browser to sort out; both are now refused at the door, like every other scheme.

- **Chrome keeps its popup fix when a proxy with a username and password is set.** The two browser flags involved were passed separately and the second replaced the first, so configuring an authenticated proxy silently switched the other one off.

- **A busy session is no longer closed out from under the request using it.** The cleanup that closes idle browsers, and the one that enforces the session cap, both judged a session only by when it was last handed out. Under load, or with a short `SESSION_TTL_MINUTES`, either could close the browser mid-request and fail it with an error the caller could do nothing about.

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
