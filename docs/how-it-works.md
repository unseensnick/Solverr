# How it works

_Doc map: [README.md](README.md)._

This page explains what Solverr does with a request, so the settings in [Configuration](configuration.md) make sense. You do not need any of it to get started.

## The short version

Some websites sit behind Cloudflare or DDoS-GUARD, which shows a "Just a moment..." or "Verify you are human" page before letting a visitor in. An ordinary HTTP client, like the one inside an indexer manager or a reader app, cannot get past that page. A real browser can.

Solverr is a small server that owns real browsers. Your app sends it a URL. Solverr opens the URL in a browser, waits until the challenge is cleared (or the time limit runs out), and sends back the page's HTML and its cookies. The cookies, such as `cf_clearance` or `__ddg2_`, let any HTTP client reach the site directly for a while afterwards.

Solverr answers the same API as [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), on the same port, so an app built for FlareSolverr works with Solverr unchanged.

## Two engines

Solverr has two browsers inside it, called engines:

- **`chrome`** is the default. It is FlareSolverr's approach: [Selenium](https://www.selenium.dev) and [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) driving a real Chromium. It is fast, and it clears most sites.
- **`stealth`** is [Byparr](https://github.com/ThePhaseless/Byparr)'s approach: [Camoufox](https://github.com/daijro/camoufox), a Firefox build that disguises itself at the source-code level, which clicks the challenge for you. Byparr's stack also includes [playwright-captcha](https://github.com/techinz/playwright-captcha). It clears the newer Cloudflare Turnstile and managed challenges that the Chromium engine gives up on.

## Fallback: trying the other engine

Each request starts on one engine. If that engine fails (the site blocks it, or the time limit runs out), or it returns a page that still looks like an unsolved challenge, Solverr tries the other engine. It also remembers which engine cleared each website and starts there next time.

The order for a normal request is:

```
Chrome engine  ->  Camoufox click-solve  ->  paid CAPTCHA service (optional, off by default)
```

Both attempts share the request's one time limit (`maxTimeout`), so falling back never makes your app wait longer than it asked for. If too little time is left for a second browser to start, Solverr stops and reports why the first engine failed.

Which engine goes first, and whether there is a fallback at all:

| The request's `engine` field | What happens |
| ---------------------------- | ------------ |
| not sent, or `auto` | Start on the engine that last cleared this website (or `DEFAULT_ENGINE`), then try the other one on failure. |
| `chrome` | Chrome only, no fallback. |
| `stealth` | Camoufox only, no fallback. |

The `DEFAULT_ENGINE` setting only picks which engine goes **first**. It does not turn fallback off; `ENGINE_FALLBACK=false` does that. Most apps built for FlareSolverr never send an `engine` field, so they always get the fallback.

The paid CAPTCHA service is insurance for the rare site that escalates past what free clicking can clear. It runs inside the Camoufox engine, only after that engine's free clicking has failed on a challenge, and only if you configure it: see [Configuration](configuration.md#paid-captcha-fallback).

## Sessions

A session is a browser that stays open between requests. The cookie that cleared the challenge stays in that browser, so the next request to the same website skips the challenge and comes back in 1 to 3 seconds instead of solving again. Solve once, reuse the cookie many times: this is the biggest speed and reliability gain Solverr has.

An app uses a session like this:

1. It creates one with the `sessions.create` command, and gets back a session id.
2. It sends that id as `session` on its later requests.
3. It removes the session with `sessions.destroy` when it is done, or leaves that to Solverr's cleanup.

A request without a session still works. Solverr then starts a browser for that one request and closes it afterwards, which is slower.

What else to know about sessions:

- **A session belongs to one engine**, the one that created it: `DEFAULT_ENGINE`, unless `sessions.create` names another. Both engines share one set of session ids.
- **A session keeps the proxy it was created with.** Solverr rebuilds a session's browser when its lifetime runs out, when the cleanup closes it, when the cap evicts it, or when the other engine takes a request over. Every rebuild goes out through that same proxy, never through the server's own address. `sessions.destroy` forgets the session and its proxy.
- **One request at a time.** A session is one browser with one page, so two requests naming it take turns. The wait counts against the second request's own `maxTimeout`, and a request that waits too long is told the session was busy, never handed the other request's page.

### Automatic cleanup

Apps often create a session and never remove it (a phone app can be closed before it gets the chance). Open browsers use a lot of memory, so Solverr runs a background cleanup that:

- closes any session that has been idle longer than `SESSION_TTL_MINUTES` (30 minutes by default). Idle time counts from the end of the session's last request, and a session with a request running on it is never closed. `0` turns this off.
- closes the longest-idle session once an engine has more than `SESSION_MAX` sessions (20 by default). `0` turns this cap off.

The cleanup's startup line in the log says if either is off. With the cleanup running, `sessions.destroy` is good practice but optional.

## Memory

Browsers use a lot of memory. Each open session keeps one alive, and each request without a session starts one of its own. On a machine with little memory, avoid sending many requests at once, and lower `SESSION_MAX` and `SESSION_TTL_MINUTES` so idle browsers close sooner.

## Why your IP address matters most

No solver gets past Cloudflare on disguise alone: the reputation of the IP address the browser connects from matters more. An address from a data centre or a VPS fails far more challenges than one from a home connection.

If a website keeps failing on **both** engines, the most effective fix is a residential proxy: set `PROXY_URL` (and its username and password) in [Configuration](configuration.md#proxy), or send a `proxy` with the request or the session.

## How long a solve takes

- The Chrome engine solves in a few seconds.
- The Camoufox engine takes about 10 to 20 seconds; that is the price of clearing the challenges Chromium cannot.
- A follow-up request on a session that already cleared the website takes about 1 to 3 seconds.
