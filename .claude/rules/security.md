---
paths:
  - "src/**"
---

# Security

- Solverr has no auth: exposing it publicly makes it an open proxy. That's the deployer's job (reverse proxy + auth); the README covers it. Don't add an auth layer unasked.
- Never log secrets: `PROXY_PASSWORD`, `CAPTCHA_API_KEY`, and returned cookies (`cf_clearance`, `__ddg2_`). `LOG_HTML=true` is debug-only and dumps page HTML; keep it off by default.
- User-controlled values that reach a browser (POST body, cookies, URL) must stay escaped. The POST form builder (`postform.py`) already `escape(quote(...))`s field names/values; keep that when editing.
- Never concatenate request input into a shell command. Browser navigation uses the driver/page API, not the shell.
- Treat the `/v1` request as an untrusted boundary: keep the existing validations in the controller before handing a URL to an engine.
