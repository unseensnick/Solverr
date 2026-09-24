# Solverr

[![Docker release](https://github.com/unseensnick/Solverr/actions/workflows/release-docker.yml/badge.svg?labelColor=27303D)](https://github.com/unseensnick/Solverr/actions/workflows/release-docker.yml) [![Container image](https://img.shields.io/github/v/release/unseensnick/Solverr?label=ghcr.io&logo=docker&logoColor=white&labelColor=27303D&color=2496ED)](https://github.com/unseensnick/Solverr/pkgs/container/solverr) [![License: GPL-3.0](https://img.shields.io/github/license/unseensnick/Solverr?labelColor=27303D&color=0877d2)](LICENSE)

Solverr is a proxy server to bypass Cloudflare and DDoS-GUARD protection. It fuses the two best open-source solvers into one service and switches between them automatically, so you get reliable solving **and** coverage of the newer challenge tiers.

- **Chrome engine** (default): the original [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) approach: [Selenium](https://www.selenium.dev) + [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) driving a real Chromium. Fast, session-capable, and clears most sites.
- **Stealth engine**: [Byparr](https://github.com/ThePhaseless/Byparr)'s stack: [Camoufox](https://github.com/daijro/camoufox) (an anti-detect Firefox that patches its fingerprint in compiled code) + [playwright-captcha](https://github.com/techinz/playwright-captcha). Clears the newer Cloudflare **Turnstile / Managed Challenges** that headless Chromium gives up on.

It speaks the exact FlareSolverr `/v1` API on port `8191`, so it is a drop-in replacement: existing clients (the *arr stack, manga/novel readers, etc.) work unchanged.

Beyond the two engines, it keeps **[sessions](#sessions--automatic-cleanup)** warm so repeat requests to a host skip the challenge, and adds an optional **[passthrough proxy](#passthrough-proxy)** for clients (indexer managers and the like) that re-fetch the URL themselves.

## Contents

- **Getting started**: [How it works](#how-it-works) · [Quick start](#quick-start) · [Installation](#installation)
- **Using it**: [Engines & fallback](#engines--fallback) · [Sessions & cleanup](#sessions--automatic-cleanup) · [API usage](#api-usage) · [Passthrough proxy](#passthrough-proxy)
- **Reference**: [Configuration](#configuration) · [Proxy & reliability](#proxy--reliability) · [Prometheus exporter](#prometheus-exporter) · [Troubleshooting](#troubleshooting)

## How it works

Solverr waits for requests in an idle state. When one arrives it opens the URL in a real browser, waits until the challenge is solved (or the timeout is hit), and returns the page HTML plus the cookies. Those cookies (e.g. `cf_clearance`, `__ddg2_`) can then be reused by any HTTP client to reach the site directly.

Each request picks an engine and **automatically falls back to the other** when the first is blocked, times out, or hands back an unsolved "Just a moment..." page. Solverr remembers which engine cleared each host and routes there first next time.

The escalation ladder for a normal request is:

```
Chrome engine  →  Camoufox click-solve  →  (optional) paid CAPTCHA API
```

> **Web browsers use a lot of memory.** Each session keeps a browser alive; sessionless requests launch one per request. On a low-RAM machine, avoid many concurrent requests. Solverr closes idle sessions automatically (see [Sessions & cleanup](#sessions--automatic-cleanup)).

## Quick start

Run the published image (no build) and send your first request. Save this as `docker-compose.yml`:

```yaml
services:
  solverr:
    image: ghcr.io/unseensnick/solverr:latest
    container_name: solverr
    ports:
      - "8191:8191"
    shm_size: 512mb
    restart: unless-stopped
    # See Configuration for the full environment list.
```

```bash
docker compose up -d
curl -sX POST 'http://localhost:8191/v1' \
  -H 'Content-Type: application/json' \
  --data '{ "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000 }'
```

The response contains the solved page HTML and cookies. See [Configuration](#configuration) to tune engines, sessions, proxy, and the passthrough.

## Installation

### Docker (recommended)

The browsers are bundled in the image, so Docker is the easiest path. [Quick start](#quick-start) runs the published image; the repo's `docker-compose.yml` is the same setup with the full annotated environment block.

The image reports its own health, so `docker ps` shows `healthy` once the API is answering (`starting` for the first 90 seconds). The check only proves the API is up, not that a solve would succeed, and Docker reports health without acting on it: `restart: unless-stopped` will not restart a container that is wedged but alive.

**Build the image locally** instead of pulling it:

```bash
docker compose up -d --build      # builds the dual-browser image (~2.3 GB)
```

**Docker CLI** (published image shown; run `docker build -t solverr .` first and swap the image name to run a local build):

```bash
docker run -d \
  --name=solverr \
  -p 8191:8191 \
  --shm-size=512m \
  --restart unless-stopped \
  ghcr.io/unseensnick/solverr:latest
```

On a Debian **host**, make sure `libseccomp2` is 2.5.x (`sudo apt-cache policy libseccomp2`) or the browser may fail to start; update it and restart the Docker daemon.

### From source

For development or unsupported architectures. Needs [uv](https://docs.astral.sh/uv/) and Python 3.14 (the version the image runs), plus both browsers if you want both engines:

```bash
# create the environment and install Python deps
uv venv --python 3.14
uv pip install -r requirements.txt

# Chrome engine: install Chrome or Chromium (+ Xvfb on Linux)
# Stealth engine: install Firefox libraries and fetch Camoufox
uv run --no-project playwright install-deps firefox
uv run --no-project python -m invisible_playwright fetch

uv run --no-project python src/flaresolverr.py
```

Set `STEALTH_ENGINE=false` to run Chrome-only and skip the Camoufox/Firefox setup entirely.

## Engines & fallback

A client can **force** an engine per request with the optional `engine` field; the `DEFAULT_ENGINE` env var only sets which engine is tried **first** when a request doesn't specify one.

| Request `engine` | Behaviour                                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| omitted / `auto` | Start on the engine that last cleared this host (or `DEFAULT_ENGINE`), then fall back to the other on failure. |
| `chrome`         | Chrome only, no fallback.                                                                                      |
| `stealth`        | Camoufox only, no fallback.                                                                                    |

Fallback triggers when an engine throws (blocked / timeout), or returns a page that still looks like an unsolved challenge. Set `ENGINE_FALLBACK=false` to disable it. Both attempts share the request's `maxTimeout`, so falling back never makes the caller wait longer than it asked for; if too little is left for a second browser to launch, Solverr stops and reports why the first engine failed.

> **`DEFAULT_ENGINE=chrome` does not disable fallback.** The "no fallback" rows apply only to the per-request `engine` field (a client forcing one engine). `DEFAULT_ENGINE` just picks the *primary*; the other engine is still used as fallback unless `ENGINE_FALLBACK=false`. Most FlareSolverr clients (the *arr apps, readers) don't send an `engine` field, so they always get the fallback path.

## Sessions & automatic cleanup

A **session** keeps a browser alive between requests. The cleared `cf_clearance` cookie stays in that browser's memory, so follow-up requests to the same host skip the challenge and return in 1–3 s instead of re-solving. This is the main reliability and speed lever: solve once, reuse the cookie many times.

Each engine keeps its own session pool under one shared session-id namespace; a session is bound to whichever engine created it, `DEFAULT_ENGINE` unless the request names one. Create one with `sessions.create` and pass its `session` id on later requests.

A session keeps the proxy it was created with. Its browser is rebuilt whenever its lifetime runs out, the idle cleanup closes it, the cap evicts it, or the other engine takes a request over, and every rebuild goes back out through that same proxy rather than through the server's own address. `sessions.destroy` forgets it.

A session is one browser with one page, so two requests naming it take it in turn rather than at once. The wait counts against the second request's own `maxTimeout`, and a request that waits longer than that is told the session was busy instead of being answered with the other request's page.

Clients often create a session and never destroy it (a mobile app can be killed before it could). To stop abandoned browsers leaking memory, Solverr runs a **background reaper** that:

- closes any session idle longer than `SESSION_TTL_MINUTES` (default 30). Idle time runs from the end of the last request on the session, so a long solve doesn't leave it looking idle the moment it finishes, and a session with a request on it is never reaped. Set it to `0` to switch idle reaping off.
- evicts the oldest-idle session once an engine exceeds `SESSION_MAX` (default 20). Set it to `0` to switch the cap off.

Whichever of the two you switch off, the reaper's startup line says so.

So `sessions.destroy` is good practice but optional: cleanup happens automatically.

## API usage

All requests are `POST http://localhost:8191/v1` with a JSON body and `Content-Type: application/json`.

<details>
<summary>Python & PowerShell examples</summary>

```python
import requests
r = requests.post("http://localhost:8191/v1", json={
    "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000,
})
print(r.text)
```

```powershell
$body = @{ cmd = "request.get"; url = "https://www.google.com/"; maxTimeout = 60000 } | ConvertTo-Json
irm -UseBasicParsing 'http://localhost:8191/v1' -Headers @{"Content-Type"="application/json"} -Method Post -Body $body
```

</details>

### `sessions.create`

Launches a browser that retains cookies until you `sessions.destroy` it (or the reaper closes it). Reusing the session avoids re-solving challenges and re-launching browsers.

| Parameter | Notes                                                                                                                                                                                             |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| session   | Optional. Session id to assign. A random UUID is used if omitted.                                                                                                                                |
| engine    | Optional. `chrome` or `stealth`; binds the session to that engine. Omitted or `auto`, it follows `DEFAULT_ENGINE`, so a Chrome session unless that names `stealth`. An id already live on either engine is reported back rather than opened a second time. |
| proxy     | Optional. Eg `"proxy": {"url": "http://127.0.0.1:8888"}`. Schema required (`http://`, `socks4://`, `socks5://`). Auth supported: `{"url": "...", "username": "user", "password": "pass"}`. |

### `sessions.list`

Returns the ids of all active sessions across both engines.

```json
{ "status": "ok", "sessions": ["session_id_1", "session_id_2"] }
```

### `sessions.destroy`

Shuts a session's browser down and frees its resources.

| Parameter | Notes                              |
| --------- | ---------------------------------- |
| session   | The session id to destroy.         |

### `request.get`

| Parameter           | Notes                                                                                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| url                 | Mandatory.                                                                                                                                                       |
| engine              | Optional. `chrome`, `stealth`, or `auto` (default). See [Engines & fallback](#engines--fallback).                                                                |
| session             | Optional. Reuse an existing browser instance. Without it, a temporary instance is created and destroyed after the request.                                      |
| session_ttl_minutes | Optional. Recreate the session if it is older than this many minutes.                                                                                            |
| maxTimeout          | Optional, default 60000. Max time to answer the request, in milliseconds. It covers the whole request, starting a browser included, so a fallback to the other engine shares it rather than starting a fresh one. Clamped to `MAX_TIMEOUT_MS` (default 180000), and so is the default when the field is omitted. |
| cookies             | Optional. Cookies to set before loading. Eg `"cookies": [{"name": "a", "value": "1"}]`.                                                                          |
| returnOnlyCookies   | Optional, default false. Return only cookies; drop response body and headers.                                                                                    |
| returnScreenshot    | Optional, default false. Return a Base64 PNG of the final page in the `screenshot` field.                                                                        |
| proxy               | Optional. Same shape as in `sessions.create`. Ignored when `session` is set (use a session proxy instead).                                                       |
| waitInSeconds       | Optional. Extra seconds to wait after solving, before returning (lets dynamic content load).                                                                     |
| disableMedia        | Optional, default false. Block images, CSS and fonts to speed up navigation. The same three on both engines, and only for the request that asks: a session is not left blocking media for the requests after it. |
| tabs_till_verify    | Optional (Chrome engine only). Number of `Tab` presses to reach a Turnstile checkbox; the resulting token is returned in `solution.turnstile_token`. Waits up to 5 seconds for a widget that renders after the page loads, so a page with no widget at all costs that long before the request continues without a token. Pressing stops in time to still return a page if the checkbox never yields one. The stealth engine detects Turnstile automatically and does not need this, and says so in the log when a request sends a count it will not use. |

> **Finding the right `tabs_till_verify`.** It is the number of `Tab` presses from the top of the document to the checkbox, so it depends on how many focusable elements the page puts before the widget. A widget with nothing focusable ahead of it is `1`. Find yours by sending the same request with `1`, `2`, `3` and so on: the value that comes back with a filled `solution.turnstile_token` is the one. Use a small `maxTimeout` while you search, because a wrong count spends the whole budget pressing before it gives up.

> **Reusing cookies?** Use the User-Agent Solverr returns (`solution.userAgent`) in your own requests. If the UA and `cf_clearance` don't match, Cloudflare re-challenges you.

> **PDFs.** When a URL serves a PDF, the stealth engine returns the file itself, Base64-encoded in `solution.response`, with `solution.contentType` set to `application/pdf`. The Chrome engine returns the viewer page instead, so pin `"engine": "stealth"` when you expect a PDF. Branch on `contentType` rather than assuming: it is absent for ordinary HTML, and also when Solverr could not get the raw bytes and fell back to the viewer page.

Example response (truncated):

```json
{
  "status": "ok",
  "message": "Challenge solved!",
  "solution": {
    "url": "https://www.google.com/",
    "status": 200,
    "headers": {},
    "response": "<!DOCTYPE html>...",
    "cookies": [ { "name": "cf_clearance", "value": "...", "domain": ".google.com", "path": "/" } ],
    "userAgent": "Mozilla/5.0 ...",
    "turnstile_token": null
  },
  "startTimestamp": 1594872947467,
  "endTimestamp": 1594872949617,
  "version": "1.7.0"
}
```

`solution.headers` is empty unless `RESPONSE_HEADERS=true`, which fills it on both engines or neither. FlareSolverr has never populated it, so the default keeps the payload identical to its.

`solution.turnstile_token` is whatever token the page's own Turnstile widget holds when the page is read. The Camoufox engine waits for a widget to fill one, because it can press the checkbox itself; the Chrome engine reports one only if it is already there, unless the request sends `tabs_till_verify` and asks it to press.

### `request.post`

Like `request.get`, plus `postData`.

| Parameter | Notes                                                                     |
| --------- | ------------------------------------------------------------------------ |
| postData  | A string in `application/x-www-form-urlencoded` form. Eg `a=b&c=d`.       |

## Passthrough proxy

Some clients don't consume the solved HTML that `/v1` returns. Instead they take the `cf_clearance` cookie and **re-fetch the URL themselves** with their own HTTP client. Cloudflare fingerprints that second request (different TLS/JA4, HTTP/2 settings, headers) than the browser that solved the challenge, decides it doesn't match, and re-challenges, so the client fails even though the solve worked. Indexer managers that drive Cloudflare-protected sites are the common case.

The passthrough removes the replay step. Point the client at Solverr's passthrough port instead of the site; Solverr solves in-process (reusing engine fallback, sessions, and per-host memory) and returns the solved body as a clean `200`. The client never sees a challenge, so it never re-fetches. Each allowed host gets its own warm session, so once a host has been cleared its next request reuses that browser and its cookies instead of starting one.

A passthrough request can't pin an engine, so it uses `DEFAULT_ENGINE` like any other request. That matters for PDFs: they come back as the real file only when the stealth engine solved them (see the PDF note under [`request.get`](#requestget)), so set `DEFAULT_ENGINE=stealth` if the site serves them.

**The target site is the first path segment**, and it must be listed in `PASSTHROUGH_ALLOWED_HOSTS`. Nothing outside that list is ever fetched, so it is never a blind open proxy. A request to:

```
http://<solverr-host>:8888/example-site.tld/some/path/1/
```

is solved as `https://example-site.tld/some/path/1/`. Replace `<solverr-host>` with wherever Solverr actually runs: its Docker **service/container name** (e.g. `solverr`) if the client is on the same Docker network, otherwise Solverr's **host IP or hostname**. `8888` is `PASSTHROUGH_PORT`.

A path whose first segment **isn't** an allow-listed host (the site's own root-relative links, like `/details/…` or `/download.php?id=1`, that a client follows for a details or next page) is routed to the **default mirror**, the first entry in `PASSTHROUGH_ALLOWED_HOSTS`. That's why downloads and pagination work; it also means the allow-list should be mirrors of one site, not unrelated sites. A mirror you forget to list is therefore served by the default one rather than refused: nothing distinguishes it from one of those root-relative links.

Searches can run against any mirror the client picks, but those root-relative follow-ups always land on the default one, so list the mirror you want them served by first. If that mirror goes down, move a healthy one to the front: a client pointed at a working mirror would otherwise still search fine and fail every download.

Enable it with:

```yaml
    environment:
      - PASSTHROUGH_ENABLED=true
      - PASSTHROUGH_ALLOWED_HOSTS=example-site.tld,mirror.example.tld
```

On the same Docker network the client reaches it by service name with no published port; from another host, publish `PASSTHROUGH_PORT` and use Solverr's IP. To make a client's Base URL offer several mirror domains (like a stock indexer definition does), give it one entry per mirror, each prefixed with the passthrough:

```
http://<solverr-host>:8888/example-site.tld/
http://<solverr-host>:8888/mirror.example.tld/
```

### Wiring up an indexer (Prowlarr/Jackett)

You don't need a bundled indexer file. Take the site's existing definition from the [Prowlarr Indexers repo](https://github.com/Prowlarr/Indexers) (or Jackett's), make three changes, and drop it in your manager's custom-definitions folder:

1. Change `id:` and `name:` to something unique (e.g. append `-passthrough`), so it sits alongside the stock one.
2. Replace the `links:` block with one entry per mirror, each prefixed with the passthrough: `- http://<solverr-host>:8888/<that-mirror-host>/`.
3. Add every one of those mirror hosts to `PASSTHROUGH_ALLOWED_HOSTS`.
4. Save the file into the manager's custom-definitions folder (create it if it isn't there) and restart the manager:
   - **Prowlarr**: `/config/Definitions/Custom/`. The `Custom` subfolder often doesn't exist yet, and Prowlarr **ignores** YAMLs placed directly in `Definitions/`, so create `Custom/` and put the file there.
   - **Jackett**: its custom-definitions folder, which Jackett prints in its startup log (commonly `/config/Jackett/Indexers/custom/` on the linuxserver image); create it if missing.

Then add the indexer in the manager, pick a mirror as the **Base URL**, and **do not attach a FlareSolverr/proxy tag**: the passthrough already does the solving, and a proxy tag would route around it. Everything else in the definition (search paths, selectors, categories) stays untouched.

> **Grab the definition as a file, not via copy-paste.** A few definitions contain non-printable characters in their filters (a rare title-cleanup step); pasting through a chat or some editors silently strips them and breaks parsing ("No title provided" on every result). Download the raw file so the bytes stay intact.

Notes and limits:

- **`GET`/`HEAD` only**; request bodies aren't forwarded. Most indexer definitions are `GET`.
- Encode the mirror as a **bare host** (`example-site.tld`), not `https://…`, because clients that normalise `//` in a path would otherwise corrupt an embedded scheme.
- Static assets are answered `404` rather than solved: a path ending in a script, stylesheet, image, font or video extension is never worth a browser. The check reads the path only, so a page whose query string happens to end that way is still fetched.
- Successful bodies are cached for `PASSTHROUGH_CACHE_TTL`; challenge pages and non-2xx responses are not, so a transient block retries rather than sticking. A site answers a bad moment with its own error page under HTTP 200, which looks like any other page from here, so `PASSTHROUGH_CACHE_REQUIRES` lets you name something every real page carries; a body without it is kept for a minute instead of the full window.
- The cache holds at most `PASSTHROUGH_CACHE_MAX_BYTES` in total. The TTL alone bounded how long a body was kept but not how much was kept, so a client walking many pages inside one TTL window could hold all of them at once.
- It's still bound by IP reputation like any solve (see [Proxy & reliability](#proxy--reliability)). If a site blocks your IP, a residential `PROXY_URL` applies to passthrough solves too.

## Configuration

All settings are environment variables and all are optional.

### Engines

| Variable               | Default     | Description                                                                    |
| ---------------------- | ----------- | ----------------------------------------------------------------------------- |
| `DEFAULT_ENGINE`       | `chrome`    | Which engine is tried **first** for requests that don't set `engine` (`chrome` \| `stealth` \| `auto`). Does not disable fallback (that's `ENGINE_FALLBACK`). |
| `STEALTH_ENGINE`       | `true`      | Load the Camoufox engine. Set `false` for a lighter, Chrome-only runtime.      |
| `ENGINE_FALLBACK`      | `true`      | Retry the other engine when the first fails or returns an unsolved challenge.   |
| `STEALTH_HEADLESS`     | `true`      | Run Camoufox headless.                                                          |
| `STEALTH_MAX_ATTEMPTS` | `1`         | Click attempts per solver nudge; the engine runs its own wait loop bounded by `maxTimeout`. |
| `STEALTH_START_TIMEOUT`| `120`       | Seconds allowed to launch a Camoufox browser.                                   |

### Sessions & cleanup

| Variable                  | Default | Description                                                              |
| ------------------------- | ------- | ----------------------------------------------------------------------- |
| `SESSION_TTL_MINUTES`     | `30`    | Idle minutes before the reaper closes a session's browser (`0` or less disables idle reaping). |
| `SESSION_MAX`             | `20`    | Max concurrent sessions per engine before oldest-idle eviction (`0` or less disables the cap). |
| `REAPER_INTERVAL_SECONDS` | `60`    | How often the reaper scans.                                              |
| `MAX_TIMEOUT_MS`          | `180000` | Ceiling on a request's `maxTimeout` (`0` lifts it). A larger request is clamped to this with a warning rather than refused, so existing callers keep working. Set below `60000` and it bounds a request that sends no `maxTimeout` at all too. |

### Proxy

| Variable         | Default | Description                                                                       |
| ---------------- | ------- | -------------------------------------------------------------------------------- |
| `PROXY_URL`      | none    | Upstream proxy for both engines. Eg `http://127.0.0.1:8080`. Overridden by a per-request/session `proxy`. |
| `PROXY_USERNAME` | none    | Proxy username.                                                                  |
| `PROXY_PASSWORD` | none    | Proxy password.                                                                  |

### Optional paid CAPTCHA fallback

Free click-solving clears the vast majority of challenges, including Turnstile/Managed. This is insurance for the rare site that escalates further: it sends that challenge to a paid solving service (2captcha / CapSolver, ~$3 per 1000 solves) **only after** free solving has failed, and does nothing until you configure it.

| Variable                  | Default          | Description                                                           |
| ------------------------- | ---------------- | -------------------------------------------------------------------- |
| `CAPTCHA_SOLVER`          | `none`           | Provider: `none`, `2captcha`, `capsolver`, or another 2captcha-compatible service. |
| `CAPTCHA_API_KEY`         | none             | API key. The solver stays dormant unless this **and** a provider are set. |
| `CAPTCHA_API_URL`         | provider default | Override the 2captcha-compatible host.                               |
| `CAPTCHA_API_MAX_ATTEMPTS`| `3`              | Polling attempts against the service.                               |

### Optional passthrough proxy

A second HTTP port that returns solved page bodies directly, for clients that would otherwise re-fetch the URL themselves (see [Passthrough proxy](#passthrough-proxy)). Off by default.

| Variable                   | Default   | Description                                                                    |
| -------------------------- | --------- | ----------------------------------------------------------------------------- |
| `PASSTHROUGH_ENABLED`      | `false`   | Turn the passthrough listener on.                                              |
| `PASSTHROUGH_ALLOWED_HOSTS`| none      | Comma-separated hosts it may fetch (the upstream is the first path segment). Empty = every request is answered `404`, so it's never a blind open proxy. |
| `PASSTHROUGH_PORT`         | `8888`    | Listening port.                                                                |
| `PASSTHROUGH_CACHE_TTL`    | `3600`    | Seconds to cache a solved 2xx body (`0` disables). Challenge pages are never cached. |
| `PASSTHROUGH_CACHE_REQUIRES` | (unset)   | A string an HTML body must contain to be cached for the full TTL, for example the link prefix your indexer's result rows use. A page without it is kept for 60 seconds, so one identical burst still costs one solve while a transient error page clears itself. Only HTML is judged this way: a PDF or an image keeps the full TTL. Unset caches every 2xx body for the full TTL. |
| `PASSTHROUGH_CACHE_MAX_BYTES` | `268435456` | Ceiling on the total bytes the cache holds (`0` lifts it). Past the ceiling the soonest-to-expire entries are evicted first. A single body over a quarter of the ceiling is served but not cached. |
| `PASSTHROUGH_TIMEOUT_MS`   | `90000`   | `maxTimeout` handed to the solver per request. Kept under the ~100s an indexer app waits before recording a failure and backing the indexer off. `0` or less falls back to 60000, the same budget the API gives a request that asks for none. |

### Browser, logging & server

| Variable             | Default   | Description                                                                    |
| -------------------- | --------- | ----------------------------------------------------------------------------- |
| `HEADLESS`           | `true`    | Run the Chrome engine headless (visible only for debugging).                   |
| `DISABLE_MEDIA`      | `false`   | Block images/CSS/fonts by default to save bandwidth (both engines).            |
| `RESPONSE_HEADERS`   | `false`   | Return the page's real response headers in `solution.headers` instead of an empty map. Both engines, or neither. Off by default: the Chrome engine gets them by having the browser log network events, which has not been measured against a fingerprinting check, and populating the field unasked would change every response. |
| `BROWSER_WAIT_TIMEOUT` | `1`     | Seconds the Chrome engine waits for an expected page state on each attempt. Raise it on a slow host or a slow site. Chrome only: the stealth engine polls until the request's own deadline instead. It never extends `maxTimeout`. |
| `BROWSER_GEO`        | none      | One tag setting the browser's language **and** timezone. Eg `de-DE`. See below. |
| `LANG`               | none      | Browser language for both engines. Accepts `en_US.UTF-8` or `en-US`. See below. |
| `BROWSER_TIMEZONE`   | `auto`    | Browser timezone for both engines: an IANA zone, or `auto` to follow the exit IP. See below. |
| `GEO_IP_LOOKUP_URLS` | none      | Comma-separated `https` services to look up the exit IP with, tried before the built-in ones. Eg `https://icanhazip.com`. See below. |
| `LOG_LEVEL`          | `info`    | `info` or `debug`.                                                             |
| `LOG_FILE`           | none      | Also write logs to this file. Eg `/config/solverr.log`.                        |
| `LOG_HTML`           | `false`   | Debug only: log all page HTML at `debug` level.                                |
| `HOST` / `PORT`      | `0.0.0.0` / `8191` | Listening interface and port. `HOST` applies to the passthrough port too. Rarely changed under Docker. |
| `TZ`                 | `UTC`     | Container timezone: log timestamps, and the browser's timezone when nothing resolves one. Eg `TZ=Europe/London`. |
| `PROMETHEUS_ENABLED` | `false`   | Enable the Prometheus exporter (see below).                                    |
| `PROMETHEUS_PORT`    | `8192`    | Exporter port (expose it if enabled).                                          |

### Browser language and timezone

A site can compare the language and clock a browser reports against the country its IP is in, so both engines are always given the same answer for both, from the same lookup. Out of the box you need to set nothing: the timezone and the language are worked out together from the exit IP once and reused, so they always name the same country and a request answered by either engine looks the same.

Set something only when you need a specific result. There are three knobs and they layer:

| Set this | Effect | Costs a lookup? |
| -------- | ------ | --------------- |
| nothing | Timezone and language both from the exit IP | once per proxy |
| `BROWSER_GEO=de-DE` | German, `Europe/Berlin` | no |
| `LANG=de-DE` | German, timezone still from the exit IP | once per proxy |
| `BROWSER_TIMEZONE=Europe/Berlin` | `Europe/Berlin`, language still from the exit IP | once per proxy, for the language |
| `BROWSER_TIMEZONE=Europe/Berlin` and `LANG=de-DE` | `Europe/Berlin`, German | no |
| `BROWSER_TIMEZONE=auto` | Exit IP, ignoring any `BROWSER_GEO` | once per proxy |

`LANG` and `BROWSER_TIMEZONE` each override `BROWSER_GEO` for their own half, so `BROWSER_GEO=en-US` with `BROWSER_TIMEZONE=America/Chicago` gives American English on Chicago time. Pin both halves and nothing is looked up; pin one and the lookup that remains is the other half's alone, so it no longer resolves a zone that was never going to be used.

"Per proxy" means per proxy account, not per proxy server: residential providers pick the exit country through the username, so two accounts on one endpoint each resolve their own country rather than sharing whichever was looked up first.

**`BROWSER_GEO`** is the short way to match a proxy that always leaves from the same country. It costs no lookup at all, which also makes it the right choice for a deployment with no outbound access beyond its proxy. The timezone it picks is written to the log at startup, because a country with several zones gets its most populous one rather than a fact: `BROWSER_GEO=en-US` gives `America/New_York`. Set `BROWSER_TIMEZONE` if that isn't the one you want. A tag with no country in it, such as `fr`, sets the language only.

**`LANG`** accepts both POSIX and tag forms, and the value is normalized before it reaches a browser:

| You set | Both engines use |
| ------- | ---------------- |
| `en_US.UTF-8` | `en-US` |
| `de_DE@euro`  | `de-DE` |
| `pt-BR`       | `pt-BR` |
| `zh_Hans_CN`  | `zh-Hans-CN` |
| `fr`          | `fr` |
| `C`, `POSIX`  | ignored, falls through to `BROWSER_GEO` then the exit IP |

Anything that isn't a language tag is ignored with a warning in the log rather than passed on. That is deliberate: a malformed value would reach `navigator.languages` and the `Accept-Language` header verbatim, which is a more distinctive fingerprint than setting nothing at all. `C.UTF-8` is ignored for the same reason, and because it is a container default nobody chose.

Whatever the language ends up being, both engines report it as the two-entry `navigator.languages` a desktop browser sends: `de-DE` becomes `["de-DE", "de"]`.

**`BROWSER_TIMEZONE`** takes any IANA zone the container's own timezone data lists. Pinning it costs no timezone lookup, but the language still comes from the exit IP, so an air-gapped deployment sets `LANG` as well, or uses `BROWSER_GEO`, to skip the check entirely. A zone that isn't in that data (`Europe/Stockholmm`) is ignored with a warning naming it, and the timezone falls back to `auto`, because passing a typo on would put the two engines in different timezones.

Two things worth knowing. Forcing a language a country doesn't speak, or a timezone it isn't in, is a mismatch a site can see, so change one only if you know why. And some countries share a timezone definition with a neighbour: Norway reports `Europe/Berlin` and the Netherlands `Europe/Brussels`, which is correct rather than a bug, since those are the same zone with the same offset and the same daylight-saving rules.

If the exit IP can't be reached, Solverr falls back to the container's `TZ` for the timezone and `en-US` for the language, logs a warning, and carries on; it does not fail the request. That fallback is kept for a minute and then looked up again, so a moment without network doesn't hold the wrong country in place for the rest of the cache window. A SOCKS proxy needs PySocks installed for that lookup to work, and without it you get the same fallback, so pin `BROWSER_TIMEZONE` or set `BROWSER_GEO` when using one.

The exit IP is looked up through `api.ipify.org`, `icanhazip.com` and `checkip.amazonaws.com`, in that order. `GEO_IP_LOOKUP_URLS` puts your own services in front of those, and the built-in ones are still tried after them. All of them share one 15-second budget per lookup, though, so a service of yours that hangs uses up time the built-in ones would have had: list only services that answer quickly. Each must be an `https` URL that answers with nothing but the IP as plain text (`https://ifconfig.co/ip` works, its JSON form does not). Behind a proxy the stealth engine also looks the exit IP up through the proxy on every launch, to give WebRTC the right address, even when `BROWSER_GEO` or both halves are pinned. That lookup uses the same list and never fails the launch. If the warning says every service failed in 0.0s with a name-resolution error, the container can't resolve hostnames at all, and changing services won't help: check its DNS.

## Proxy & reliability

No solver beats Cloudflare by fingerprint alone: **IP reputation dominates**. A datacenter/VPS IP fails far more challenges than a residential one. If a site keeps failing on **both** engines, the single most effective fix is a residential proxy: set `PROXY_URL` (and credentials), or pass `proxy` per request/session.

Rough guide to expected latency: Chrome solves take a few seconds; Camoufox solves take ~10–20 s (the price of clearing challenges Chromium can't). Session reuse brings follow-ups on the same host down to ~1–3 s.

## Prometheus exporter

Disabled by default. Enable with `PROMETHEUS_ENABLED=true` and expose `PROMETHEUS_PORT` (default 8192). Metrics include per-domain request counts, results, and duration histograms.

The domain label is capped at 100 distinct hosts; every host after that is reported as `other`. Prometheus keeps a time series per label value for the life of the process, so an uncapped label would grow the registry with the number of hosts requested. A deployer pointing Solverr at a handful of sites never reaches the cap.

## Troubleshooting

**A source shows no results but the log says `Challenge not detected!` with a 200.** An engine loaded the page but couldn't recognise a newer managed/Turnstile challenge and returned it as if solved. Solverr's auto-fallback is designed to catch this and retry on the other engine; make sure `ENGINE_FALLBACK` is on and the stealth engine is enabled. If it still fails, the site is likely gating on your IP, so add a residential proxy.

**Out-of-memory / browser launch errors (Proxmox LXC, low-RAM hosts).** Give the container more shared memory: `shm_size: 512mb` in `docker-compose.yml` (or `--shm-size=512m`). Reduce `SESSION_MAX` and keep `SESSION_TTL_MINUTES` modest so idle browsers are freed sooner.

**Camoufox / Firefox errors on ARM or NAS devices.** Stealth-engine support on ARM/NAS is best-effort. If it won't launch, set `STEALTH_ENGINE=false` to run Chrome-only.

**Cloudflare has blocked this request / IP banned.** Your IP is flagged for that site. Try a (residential) proxy, or open the site in a normal browser from the same network to confirm.

## Contributing

Bug reports and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request: it covers the setup, the tests, and the commit message standard that CI checks every commit against. Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Solverr is licensed under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)). It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) (MIT) and its stealth engine derives from [Byparr](https://github.com/ThePhaseless/Byparr) (GPL-3.0); because Byparr is copyleft, the combined work is GPL-3.0. Upstream copyright notices are preserved in [NOTICE](NOTICE).
