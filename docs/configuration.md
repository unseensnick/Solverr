# Configuration

_Doc map: [README.md](README.md)._

Every setting is an environment variable, and every one is optional: Solverr runs with none of them set. Add them under `environment:` in your `docker-compose.yml`:

```yaml
services:
  solverr:
    image: ghcr.io/unseensnick/solverr:latest
    environment:
      - LOG_LEVEL=debug
      - SESSION_TTL_MINUTES=15
```

Then apply the change with `docker compose up -d`.

## Engines

What the engines are and how fallback works: [How it works](how-it-works.md#two-engines).

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `DEFAULT_ENGINE` | `chrome` | Which engine goes first for a request that does not choose one: `chrome` or `stealth` (`auto` is the same as `chrome`). It does not turn fallback off; `ENGINE_FALLBACK` does. |
| `STEALTH_ENGINE` | `true` | Load the Camoufox engine. `false` gives a lighter, Chrome-only Solverr. |
| `ENGINE_FALLBACK` | `true` | Try the other engine when the first one fails or returns an unsolved challenge. |
| `STEALTH_HEADLESS` | `true` | Run Camoufox without a visible window. |
| `STEALTH_MAX_ATTEMPTS` | `1` | Click attempts each time the Camoufox engine nudges the challenge. The engine keeps waiting in its own loop until `maxTimeout`. |
| `STEALTH_START_TIMEOUT` | `120` | Seconds allowed for a Camoufox browser to start. |

## Sessions and cleanup

What sessions are: [How it works](how-it-works.md#sessions).

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `SESSION_TTL_MINUTES` | `30` | Minutes a session may sit idle before the cleanup closes its browser. `0` or less turns idle cleanup off. |
| `SESSION_MAX` | `20` | Most sessions per engine. Past it, the longest-idle session is closed. `0` or less turns the cap off. |
| `REAPER_INTERVAL_SECONDS` | `60` | How often, in seconds, the cleanup checks. `0` or less turns the cleanup off entirely, so idle sessions are never closed. |
| `MAX_TIMEOUT_MS` | `180000` | The largest `maxTimeout` a request may ask for (`0` removes the limit). A larger request is cut down to this with a warning instead of being refused, so existing apps keep working. Set below `60000`, it also limits a request that sends no `maxTimeout` at all. |

## Proxy

Why a proxy helps: [Why your IP address matters most](how-it-works.md#why-your-ip-address-matters-most).

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `PROXY_URL` | none | A proxy both engines connect through, for example `http://127.0.0.1:8080`. A `proxy` sent with a request or a session takes its place. |
| `PROXY_USERNAME` | none | The proxy's username. |
| `PROXY_PASSWORD` | none | The proxy's password. |

## Paid CAPTCHA fallback

Free clicking clears almost every challenge, Turnstile and managed ones included. This is insurance for the rare site that escalates further: Solverr sends that challenge to a paid solving service (2captcha or CapSolver, about $3 per 1000 solves), but only after free solving has failed, and only when both a provider and a key are set.

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `CAPTCHA_SOLVER` | `none` | The provider: `none`, `2captcha`, `capsolver`, or another 2captcha-compatible service. |
| `CAPTCHA_API_KEY` | none | Your API key. Nothing is ever sent to a provider unless this and `CAPTCHA_SOLVER` are both set. |
| `CAPTCHA_API_URL` | provider default | The address of a 2captcha-compatible service, if not the provider's own. |
| `CAPTCHA_API_MAX_ATTEMPTS` | `3` | How many times to ask the service for the answer. |

## Passthrough proxy

A second port that hands solved pages straight to apps that would otherwise fetch the URL again themselves. Off by default. The full guide: [Passthrough proxy](passthrough.md).

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `PASSTHROUGH_ENABLED` | `false` | Turn the passthrough port on. |
| `PASSTHROUGH_ALLOWED_HOSTS` | none | Comma-separated sites the passthrough may fetch. The first one is the default mirror. Empty means every request gets a `404`, so it is never an open proxy. |
| `PASSTHROUGH_PORT` | `8888` | The port it listens on. |
| `PASSTHROUGH_CACHE_TTL` | `3600` | Seconds to keep a successful page (`0` turns the cache off). Challenge pages are never kept. |
| `PASSTHROUGH_CACHE_REQUIRES` | none | Text every real HTML page contains, for example the link prefix the site's result rows use. A page without it is kept for 60 seconds instead of the full time, so a burst of identical requests still costs one solve while a temporary error page clears itself. PDFs and images always keep the full time. Unset, every successful page keeps the full time. |
| `PASSTHROUGH_CACHE_MAX_BYTES` | `268435456` | The most the cache holds in total, in bytes (256 MiB; `0` removes the limit). When full, the pages closest to expiring go first. A single page bigger than a quarter of the limit is served but not kept. |
| `PASSTHROUGH_TIMEOUT_MS` | `90000` | The `maxTimeout` each passthrough request gets. Kept under the roughly 100 seconds an indexer app waits before it records a failure and backs off. `0` or less means 60000, the same as an API request that asks for nothing. |

## Browser language and timezone

Out of the box, Solverr works out the browser's language and timezone from the IP address it connects from, so you usually set nothing here. The full explanation: [Language and timezone](language-and-timezone.md).

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `BROWSER_GEO` | none | One language tag that sets both the language and the timezone, for example `de-DE`. |
| `LANG` | none | The browser language for both engines. Accepts `en_US.UTF-8` or `en-US`. |
| `BROWSER_TIMEZONE` | `auto` | The browser timezone for both engines: a zone name such as `Europe/Berlin`, or `auto` to follow the IP address. |
| `GEO_IP_LOOKUP_URLS` | none | Comma-separated `https` services to look up the IP address with, tried before the built-in ones, for example `https://icanhazip.com`. |
| `TZ` | `UTC` | The container's own timezone: used for log timestamps, and for the browser when no timezone can be worked out. For example `TZ=Europe/London`. |

## Browser behaviour

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `HEADLESS` | `true` | Run the Chrome engine without a visible window. Turn it off only for debugging. |
| `DISABLE_MEDIA` | `false` | Block images, CSS and fonts by default, on both engines, to save bandwidth. A request's own `disableMedia` field overrides it. |
| `RESPONSE_HEADERS` | `false` | Return the page's real response headers in `solution.headers` instead of an empty map, on both engines. Off by default: the Chrome engine can only get them by having the browser log its network traffic, which has not been tested against fingerprinting checks, and turning it on changes every response. |
| `BROWSER_WAIT_TIMEOUT` | `1` | Seconds the Chrome engine waits for the page to reach an expected state on each attempt. Raise it for a slow machine or a slow site. Chrome only: the stealth engine keeps checking until the request's own time limit. It never makes a request take longer than its `maxTimeout`. |

## Logging and server

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `LOG_LEVEL` | `info` | `info`, or `debug` for detail. Attach a `debug` log to a bug report. |
| `LOG_FILE` | none | Also write the log to this file, for example `/config/solverr.log`. |
| `LOG_HTML` | `false` | Debugging only: write the HTML of every page to the log at `debug` level. |
| `HOST` / `PORT` | `0.0.0.0` / `8191` | The network interface and port the API listens on. `HOST` applies to the passthrough port too. Rarely changed under Docker. |
| `PROMETHEUS_ENABLED` | `false` | Turn on the Prometheus metrics exporter. See [Prometheus metrics](#prometheus-metrics). |
| `PROMETHEUS_PORT` | `8192` | The exporter's port. Publish it if you turn the exporter on. |

## Prometheus metrics

With `PROMETHEUS_ENABLED=true` and `PROMETHEUS_PORT` published, Solverr exports request counts, results, and duration histograms per website.

The website label counts at most 100 different hosts; every host after that is reported as `other`. Prometheus keeps every label value for the life of the process, so without the limit its memory would grow with every new host. A setup that points Solverr at a handful of sites never reaches it.
