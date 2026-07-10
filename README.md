# Solverr

Solverr is a proxy server to bypass Cloudflare and DDoS-GUARD protection. It fuses the two best open-source solvers into one service and switches between them automatically, so you get reliable solving **and** coverage of the newer challenge tiers.

- **Chrome engine** (default) — the original [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) approach: [Selenium](https://www.selenium.dev) + [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) driving a real Chromium. Fast, session-capable, and clears most sites.
- **Stealth engine** — [Byparr](https://github.com/ThePhaseless/Byparr)'s stack: [Camoufox](https://github.com/daijro/camoufox) (an anti-detect Firefox that patches its fingerprint in compiled code) + [playwright-captcha](https://github.com/techinz/playwright-captcha). Clears the newer Cloudflare **Turnstile / Managed Challenges** that headless Chromium gives up on.

It speaks the exact FlareSolverr `/v1` API on port `8191`, so it is a drop-in replacement: existing clients (the *arr stack, manga/novel readers, etc.) work unchanged.

## How it works

Solverr waits for requests in an idle state. When one arrives it opens the URL in a real browser, waits until the challenge is solved (or the timeout is hit), and returns the page HTML plus the cookies. Those cookies (e.g. `cf_clearance`, `__ddg2_`) can then be reused by any HTTP client to reach the site directly.

Each request picks an engine and **automatically falls back to the other** when the first is blocked, times out, or hands back an unsolved "Just a moment..." page. Solverr remembers which engine cleared each host and routes there first next time.

The escalation ladder for a normal request is:

```
Chrome engine  →  Camoufox click-solve  →  (optional) paid CAPTCHA API
```

> **Web browsers use a lot of memory.** Each session keeps a browser alive; sessionless requests launch one per request. On a low-RAM machine, avoid many concurrent requests. Solverr closes idle sessions automatically (see [Sessions & cleanup](#sessions--automatic-cleanup)).

## Quick start

```bash
docker compose up -d --build      # builds the dual-browser image (~2.3 GB)
```

Then point your client at `http://<host>:8191` and send a request:

```bash
curl -sX POST 'http://localhost:8191/v1' \
  -H 'Content-Type: application/json' \
  --data '{ "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000 }'
```

## Installation

### Docker (recommended)

The browsers are bundled in the image, so Docker is the easiest path. A `docker-compose.yml` is provided; edit the environment block to taste and run `docker compose up -d --build`.

Or with the Docker CLI (build the image once, then run it):

```bash
docker build -t solverr .
docker run -d \
  --name=solverr \
  -p 8191:8191 \
  --shm-size=512m \
  --restart unless-stopped \
  solverr
```

**Run the published image instead** (no local build) — save as `docker-compose.yml` and `docker compose up -d`:

```yaml
services:
  solverr:
    image: ghcr.io/unseensnick/solverr:latest
    container_name: solverr
    ports:
      - "8191:8191"
    environment:
      - DEFAULT_ENGINE=chrome     # chrome | stealth | auto
      - ENGINE_FALLBACK=true
      - TZ=UTC
      # See Configuration below for the full env list.
    shm_size: 512mb
    restart: unless-stopped
```

Or with the CLI: `docker run -d --name solverr -p 8191:8191 --shm-size=512m --restart unless-stopped ghcr.io/unseensnick/solverr:latest`.

On a Debian **host**, make sure `libseccomp2` is 2.5.x (`sudo apt-cache policy libseccomp2`) or the browser may fail to start; update it and restart the Docker daemon.

### From source

For development or unsupported architectures. Requires Python 3.11+ (the Docker image uses 3.14), and both browsers if you want both engines:

```bash
# install Python deps (pip, or `uv pip`)
pip install -r requirements.txt

# Chrome engine: install Chrome or Chromium (+ Xvfb on Linux)
# Stealth engine: install Firefox libraries and fetch Camoufox
playwright install-deps firefox
python -m invisible_playwright fetch

python src/flaresolverr.py
```

Set `STEALTH_ENGINE=false` to run Chrome-only and skip the Camoufox/Firefox setup entirely.

## Engines & fallback

Choose the engine per request with the optional `engine` field, or set the default with `DEFAULT_ENGINE`.

| Request `engine` | Behaviour                                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| omitted / `auto` | Start on the engine that last cleared this host (or `DEFAULT_ENGINE`), then fall back to the other on failure. |
| `chrome`         | Chrome only, no fallback.                                                                                      |
| `stealth`        | Camoufox only, no fallback.                                                                                    |

Fallback triggers when an engine throws (blocked / timeout), or returns a page that still looks like an unsolved challenge. Set `ENGINE_FALLBACK=false` to disable it.

## Sessions & automatic cleanup

A **session** keeps a browser alive between requests. The cleared `cf_clearance` cookie stays in that browser's memory, so follow-up requests to the same host skip the challenge and return in 1–3 s instead of re-solving. This is the main reliability and speed lever: solve once, reuse the cookie many times.

Each engine keeps its own session pool under one shared session-id namespace; a session is bound to whichever engine created it (default Chrome). Create one with `sessions.create` and pass its `session` id on later requests.

Clients often create a session and never destroy it (a mobile app can be killed before it could). To stop abandoned browsers leaking memory, Solverr runs a **background reaper** that:

- closes any session idle longer than `SESSION_TTL_MINUTES` (default 30). Every request bumps the session's last-used time, so an in-use session is never reaped.
- evicts the oldest-idle session once an engine exceeds `SESSION_MAX` (default 20).

So `sessions.destroy` is good practice but optional — cleanup happens automatically.

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
| engine    | Optional. `chrome` (default) or `stealth`. Binds the session to that engine.                                                                                                                     |
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
| maxTimeout          | Optional, default 60000. Max time to solve the challenge, in milliseconds.                                                                                       |
| cookies             | Optional. Cookies to set before loading. Eg `"cookies": [{"name": "a", "value": "1"}]`.                                                                          |
| returnOnlyCookies   | Optional, default false. Return only cookies; drop response body and headers.                                                                                    |
| returnScreenshot    | Optional, default false. Return a Base64 PNG of the final page in the `screenshot` field.                                                                        |
| proxy               | Optional. Same shape as in `sessions.create`. Ignored when `session` is set (use a session proxy instead).                                                       |
| waitInSeconds       | Optional. Extra seconds to wait after solving, before returning (lets dynamic content load).                                                                     |
| disableMedia        | Optional, default false. Block images, CSS and fonts to speed up navigation.                                                                                     |
| tabs_till_verify    | Optional (Chrome engine only). Number of `Tab` presses to reach a Turnstile checkbox; the resulting token is returned in `solution.turnstile_token`. The stealth engine detects Turnstile automatically and does not need this. |

> **Reusing cookies?** Use the User-Agent Solverr returns (`solution.userAgent`) in your own requests. If the UA and `cf_clearance` don't match, Cloudflare re-challenges you.

Example response (truncated):

```json
{
  "status": "ok",
  "message": "Challenge solved!",
  "solution": {
    "url": "https://www.google.com/",
    "status": 200,
    "headers": { "content-type": "text/html; charset=UTF-8" },
    "response": "<!DOCTYPE html>...",
    "cookies": [ { "name": "cf_clearance", "value": "...", "domain": ".google.com", "path": "/" } ],
    "userAgent": "Mozilla/5.0 ...",
    "turnstile_token": null
  },
  "startTimestamp": 1594872947467,
  "endTimestamp": 1594872949617,
  "version": "3.5.0"
}
```

### `request.post`

Like `request.get`, plus `postData`.

| Parameter | Notes                                                                     |
| --------- | ------------------------------------------------------------------------ |
| postData  | A string in `application/x-www-form-urlencoded` form. Eg `a=b&c=d`.       |

## Configuration

All settings are environment variables and all are optional.

### Engines

| Variable               | Default     | Description                                                                    |
| ---------------------- | ----------- | ----------------------------------------------------------------------------- |
| `DEFAULT_ENGINE`       | `chrome`    | Engine for requests that don't set `engine` (`chrome` \| `stealth` \| `auto`). |
| `STEALTH_ENGINE`       | `true`      | Load the Camoufox engine. Set `false` for a lighter, Chrome-only runtime.      |
| `ENGINE_FALLBACK`      | `true`      | Retry the other engine when the first fails or returns an unsolved challenge.   |
| `STEALTH_HEADLESS`     | `true`      | Run Camoufox headless.                                                          |
| `STEALTH_MAX_ATTEMPTS` | `1`         | Click attempts per solver nudge; the engine runs its own wait loop bounded by `maxTimeout`. |
| `STEALTH_START_TIMEOUT`| `120`       | Seconds allowed to launch a Camoufox browser.                                   |

### Sessions & cleanup

| Variable                  | Default | Description                                                              |
| ------------------------- | ------- | ----------------------------------------------------------------------- |
| `SESSION_TTL_MINUTES`     | `30`    | Idle minutes before the reaper closes a session's browser (`0` disables). |
| `SESSION_MAX`             | `20`    | Max concurrent sessions per engine before oldest-idle eviction.          |
| `REAPER_INTERVAL_SECONDS` | `60`    | How often the reaper scans.                                              |

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

### Browser, logging & server

| Variable             | Default   | Description                                                                    |
| -------------------- | --------- | ----------------------------------------------------------------------------- |
| `HEADLESS`           | `true`    | Run the Chrome engine headless (visible only for debugging).                   |
| `DISABLE_MEDIA`      | `false`   | Block images/CSS/fonts by default to save bandwidth (both engines).            |
| `LANG`               | none      | Chrome browser language. Eg `LANG=en_GB`.                                       |
| `LOG_LEVEL`          | `info`    | `info` or `debug`.                                                             |
| `LOG_FILE`           | none      | Also write logs to this file. Eg `/config/solverr.log`.                        |
| `LOG_HTML`           | `false`   | Debug only: log all page HTML at `debug` level.                                |
| `HOST` / `PORT`      | `0.0.0.0` / `8191` | Listening interface and port. Rarely changed under Docker.           |
| `TZ`                 | `UTC`     | Container timezone (affects log timestamps). Eg `TZ=Europe/London`.            |
| `PROMETHEUS_ENABLED` | `false`   | Enable the Prometheus exporter (see below).                                    |
| `PROMETHEUS_PORT`    | `8192`    | Exporter port (expose it if enabled).                                          |

## Proxy & reliability

No solver beats Cloudflare by fingerprint alone — **IP reputation dominates**. A datacenter/VPS IP fails far more challenges than a residential one. If a site keeps failing on **both** engines, the single most effective fix is a residential proxy: set `PROXY_URL` (and credentials), or pass `proxy` per request/session.

Rough guide to expected latency: Chrome solves take a few seconds; Camoufox solves take ~10–20 s (the price of clearing challenges Chromium can't). Session reuse brings follow-ups on the same host down to ~1–3 s.

## Prometheus exporter

Disabled by default. Enable with `PROMETHEUS_ENABLED=true` and expose `PROMETHEUS_PORT` (default 8192). Metrics include per-domain request counts, results, and duration histograms.

## Troubleshooting

**A source shows no results but the log says `Challenge not detected!` with a 200.** An engine loaded the page but couldn't recognise a newer managed/Turnstile challenge and returned it as if solved. Solverr's auto-fallback is designed to catch this and retry on the other engine; make sure `ENGINE_FALLBACK` is on and the stealth engine is enabled. If it still fails, the site is likely gating on your IP — add a residential proxy.

**Out-of-memory / browser launch errors (Proxmox LXC, low-RAM hosts).** Give the container more shared memory: `shm_size: 512mb` in `docker-compose.yml` (or `--shm-size=512m`). Reduce `SESSION_MAX` and keep `SESSION_TTL_MINUTES` modest so idle browsers are freed sooner.

**Camoufox / Firefox errors on ARM or NAS devices.** Stealth-engine support on ARM/NAS is best-effort. If it won't launch, set `STEALTH_ENGINE=false` to run Chrome-only.

**Cloudflare has blocked this request / IP banned.** Your IP is flagged for that site. Try a (residential) proxy, or open the site in a normal browser from the same network to confirm.

## License

Solverr is licensed under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)). It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) (MIT) and its stealth engine derives from [Byparr](https://github.com/ThePhaseless/Byparr) (GPL-3.0); because Byparr is copyleft, the combined work is GPL-3.0. Upstream copyright notices are preserved in [NOTICE](NOTICE).
