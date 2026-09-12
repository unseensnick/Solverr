---
paths:
  - "src/**"
---

# Security

- Solverr has no auth: exposing it publicly makes it an open proxy. That's the deployer's job (reverse proxy + auth); the README covers it. Don't add an auth layer unasked.
- Never log secrets: `PROXY_PASSWORD`, `CAPTCHA_API_KEY`, and returned cookies (`cf_clearance`, `__ddg2_`). `LOG_HTML=true` is debug-only and dumps page HTML; keep it off by default.
- User-controlled values that reach a browser (POST body, cookies, URL) must stay escaped. The POST form builder (`postform.py`) runs every attribute through `_attr`, which is `quote(escape(...))`: HTML-escaped first and percent-encoded second, because the browser URL-decodes the whole `data:` document before parsing it. Keep both, and keep that order, for the action as well as the field names and values.
- Never concatenate request input into a shell command. Browser navigation uses the driver/page API, not the shell.
- Treat the `/v1` request as an untrusted boundary: keep the existing validations in the controller before handing a URL to an engine.
- **Only `http(s)` URLs may reach a browser** (`_validate_url` in `flaresolverr_service.py`, called by both request commands). Do not loosen it: before v1.2.1 a `file://` URL was fetched and its contents returned in `solution.response`, which made any reachable port a local-file reader.
