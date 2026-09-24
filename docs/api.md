# API

_Doc map: [README.md](README.md)._

Solverr answers the FlareSolverr `/v1` API. If your app already supports FlareSolverr, you only need to give it Solverr's address and can skip this page. It is for people writing their own client, or checking what a request can ask for.

Every command is a `POST` to `http://localhost:8191/v1` with a JSON body and the header `Content-Type: application/json`. The `cmd` field says which command it is.

## A first request

```bash
curl -sX POST 'http://localhost:8191/v1' \
  -H 'Content-Type: application/json' \
  --data '{ "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000 }'
```

The same request from Python:

```python
import requests
r = requests.post("http://localhost:8191/v1", json={
    "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000,
})
print(r.text)
```

And from PowerShell:

```powershell
$body = @{ cmd = "request.get"; url = "https://www.google.com/"; maxTimeout = 60000 } | ConvertTo-Json
irm -UseBasicParsing 'http://localhost:8191/v1' -Headers @{"Content-Type"="application/json"} -Method Post -Body $body
```

## `request.get`

Opens a URL, clears any challenge, and returns the page.

| Parameter | Notes |
| --------- | ----- |
| `url` | Required. Only `http://` and `https://` URLs are accepted. |
| `engine` | Optional. `chrome`, `stealth`, or `auto` (the default). See [Fallback](how-it-works.md#fallback-trying-the-other-engine). |
| `session` | Optional. Reuse a session's browser. Without it, Solverr starts a browser for this request and closes it afterwards. |
| `session_ttl_minutes` | Optional. Rebuild the session's browser if it is older than this many minutes. |
| `maxTimeout` | Optional, default 60000. The most time the whole request may take, in milliseconds, starting a browser included. A fallback to the other engine shares it instead of getting a fresh one. Capped at `MAX_TIMEOUT_MS` (180000 by default), and so is the default when this field is not sent. |
| `cookies` | Optional. Cookies to set before the page loads, for example `"cookies": [{"name": "a", "value": "1"}]`. |
| `returnOnlyCookies` | Optional, default false. Return only the cookies, without the page body or headers. |
| `returnScreenshot` | Optional, default false. Return a Base64 PNG of the final page in the `screenshot` field. |
| `proxy` | Optional. A proxy for this request, for example `"proxy": {"url": "http://127.0.0.1:8888"}`. The URL needs its scheme (`http://`, `socks4://` or `socks5://`). With a password: `{"url": "...", "username": "user", "password": "pass"}`. Ignored when `session` names a session that already exists, because a session keeps the proxy it was created with. A request that creates the session gives it this proxy. |
| `waitInSeconds` | Optional. Extra seconds to wait after the challenge clears, before returning, so content that loads late has time to appear. |
| `disableMedia` | Optional, defaults to the `DISABLE_MEDIA` setting (false unless you set it). Block images, CSS and fonts, which makes loading faster. Both engines block the same three, and only for this request: a session does not keep blocking them afterwards. |
| `tabs_till_verify` | Optional, Chrome engine only. How many `Tab` presses reach a Turnstile checkbox on the page. See [Turnstile checkboxes](#turnstile-checkboxes). |

### What comes back

An example response, shortened:

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
  "version": "1.8.0"
}
```

- **`solution.response`** is the page's HTML.
- **`solution.cookies`** are the browser's cookies for the page.
- **`solution.userAgent`** is the browser's user agent. If you reuse the cookies in your own requests, send this user agent with them. Cloudflare challenges again when the user agent and `cf_clearance` do not match.
- **`solution.headers`** is empty unless `RESPONSE_HEADERS=true`, which fills it on both engines or neither. Empty is what FlareSolverr returns, so it is the default.
- **`solution.turnstile_token`** is the token the page's own Turnstile widget holds when the page is read. The Camoufox engine waits for the widget to fill one, because it can press the checkbox itself. The Chrome engine reports one only if it is already there, unless the request sends `tabs_till_verify`.
- **`solution.contentType`** appears only when the stealth engine returns a PDF. See [PDFs](#pdfs).

### Turnstile checkboxes

`tabs_till_verify` is the number of `Tab` presses from the top of the page to the checkbox. It depends on how many things on the page can take keyboard focus before the widget: a widget with nothing focusable ahead of it is `1`. The token the press produces comes back in `solution.turnstile_token`.

To find the right number, send the same request with `1`, then `2`, `3` and so on. The value that comes back with a filled `solution.turnstile_token` is the one. Use a small `maxTimeout` while you search, because a wrong count spends the whole time limit pressing before it gives up.

Solverr waits up to 5 seconds for a widget that appears after the page loads, so a page with no widget at all costs that long before the request carries on without a token. It stops pressing in time to still return the page if the checkbox never gives a token.

The stealth engine finds Turnstile checkboxes by itself and does not need this. It says so in the log when a request sends a count it will not use.

### PDFs

When a URL serves a PDF, the stealth engine returns the file itself, Base64-encoded in `solution.response`, with `solution.contentType` set to `application/pdf`. The Chrome engine returns the browser's PDF viewer page instead, so send `"engine": "stealth"` when you expect a PDF.

Check `contentType` before treating the response as a file. It is missing for ordinary HTML, and also when Solverr could not get the file's bytes and returned the viewer page.

## `request.post`

Like `request.get`, and it also sends a form.

| Parameter | Notes |
| --------- | ----- |
| `postData` | Required. The form as an `application/x-www-form-urlencoded` string, for example `a=b&c=d`. |

## `sessions.create`

Starts a browser that keeps its cookies until you remove it with `sessions.destroy`, or until the [automatic cleanup](how-it-works.md#automatic-cleanup) closes it. Reusing it avoids solving the same challenge again and starting a new browser.

| Parameter | Notes |
| --------- | ----- |
| `session` | Optional. The id to give the session. A random UUID is used if you leave it out. |
| `engine` | Optional. `chrome` or `stealth` ties the session to that engine. Left out, or `auto`, it uses `DEFAULT_ENGINE`, which means Chrome unless you changed it. An id that is already open on either engine is reported back instead of being opened twice. |
| `proxy` | Optional. Same shape as in `request.get`. Every request on this session goes out through it. |

## `sessions.list`

Returns the ids of all open sessions on both engines.

```json
{ "status": "ok", "sessions": ["session_id_1", "session_id_2"] }
```

## `sessions.destroy`

Closes a session's browser and frees its memory.

| Parameter | Notes |
| --------- | ----- |
| `session` | The id of the session to close. |

## Fields FlareSolverr removed

`headers`, `userAgent`, `returnRawHtml` and `download` were removed from the API in FlareSolverr v2. Solverr accepts a request that still sends them, ignores them, and logs a warning.
