---
name: live-check
description: Verify a change against live Cloudflare challenges through a running Solverr, because the unit tests cannot reach the thing that actually breaks. Builds an isolated container, runs the matrix (interstitial on both engines, Turnstile, PDF, passthrough, cookie shape), and proves each mechanism with a controlled A/B rather than a single pass. Use after any change to the engines, detection, sessions, or the passthrough, and before cutting a release. Never touches the owner's own solverr container.
argument-hint: "<what changed> (e.g. 'the detection retry', 'PDF handling', omit for the full matrix)"
disable-model-invocation: true
allowed-tools:
  - Bash(docker *)
  - Bash(curl *)
  - Bash(uv run *)
---

Verify the change described by `$ARGUMENTS` against live challenges. The browser-free tests cover response shape and detection strings; they cannot tell you whether a page still clears. That gap is where the real regressions live, so this skill exists to close it before a release rather than after.

## The rule this skill enforces

**A single passing request proves nothing.** Cloudflare's behavior varies by IP reputation, time, and how hard the host was hit five minutes ago. A change that "works" on one request and a change that is genuinely fine look identical from one sample. So every claim here comes from either a repeat count or a controlled comparison:

- **Repeat count** for reliability claims: N trials, report the tally and the spread, not the best run.
- **Controlled A/B** for mechanism claims: run the old and new configuration back to back, in the same window, against the same host. Sequential runs minutes apart are not a comparison when the environment drifts.

When a run fails, the first question is always "does the bare browser do this too?" A probe that drives the browser directly, with none of Solverr's logic, separates "our code broke it" from "the site is refusing us today".

## Step 1: Build and start an isolated container

Never reuse or restart the owner's `solverr` container, and never bind its ports blindly. Build a distinct tag and run a distinct name:

```
docker build -t solverr:livetest .
docker run -d --name solverr-livetest --shm-size=512m -p 8191:8191 \
  -e LOG_LEVEL=debug -e STEALTH_ENGINE=true -e DEFAULT_ENGINE=chrome solverr:livetest
```

Check for a name or port clash first (`docker ps -a`). If the owner's container exists and is stopped, its name is taken: use the `-livetest` suffix and a network alias if something needs to resolve it by name. `LOG_LEVEL=debug` matters here, since the engine's own view of the page is the evidence when a solve fails.

## Step 2: Run the matrix

Scope it to what changed; run the whole thing before a release. Live testers that actually gate are recorded in the `cf-challenge-test-urls` memory. Sites that do not gate prove nothing, so do not substitute a convenient URL.

| Check | What it proves |
|---|---|
| Interstitial, `engine: chrome` | The default path still clears, with a timing baseline. |
| Interstitial, `engine: stealth` | The Camoufox path clears, and how fast relative to Chrome. |
| Interstitial, repeated N times | Reliability, not luck. Report the tally. |
| A Turnstile widget, `engine: stealth` | The click path fills `cf-turnstile-response`. Solving is proven by the token, never by the solver returning quietly. |
| A PDF URL, `engine: stealth` | `solution.contentType` is `application/pdf` and the base64 body starts `%PDF`. |
| Any solve, both engines | `solution.cookies` key sets match: `expiry`, absent for session cookies. |
| Passthrough, if touched | The body arrives with the right `Content-Type`, and a root-relative path routes to the default mirror. |

Post to `/v1` with `cmd: request.get` and a generous `maxTimeout`. Record elapsed time per request; a solve that suddenly takes 5x longer is a finding even when it succeeds.

## Step 3: When something fails, isolate before concluding

Do not report "the change broke X" from a failed request. Work down this list:

1. **Read the engine's own view.** With `LOG_LEVEL=debug` the wait loop logs the page title and URL each pass. A title that stays on the challenge means it never cleared; a title that reached the real page means detection is lying.
2. **Watch for a re-challenge loop.** Several distinct `__cf_chl_tk` values inside one request means Cloudflare kept re-issuing rather than accepting. That is the environment, not the code, and the fallback engine covers it.
3. **Run the bare-browser probe.** A short script inside the container that drives the browser directly, with no solver and none of Solverr's logic, and polls the same detection predicates. If the bare browser also fails, the site is refusing this IP; stop blaming the diff.
4. **A/B the suspect.** Add back exactly one piece of Solverr's behavior at a time (the response listener, the click solver, media blocking) until the failure reappears. The piece that reintroduces it is the cause.
5. **Only then** conclude, and say which of the four steps the conclusion rests on.

## Step 4: Report and clean up

Report a table of check, engine, outcome, and timing, with tallies for anything repeated. State plainly what was **not** covered: the paid CAPTCHA escalation needs an API key, a Cloudflare-gated PDF needs a gated PDF, and the POST path needs a form target. An unverified path claimed as verified is worse than an admitted gap.

Then clean up:

```
docker rm -f solverr-livetest
docker rmi solverr:livetest
```

Leave the owner's container and images untouched, and say explicitly that you did.

## Rules

- Never start, stop, restart, or remove a container the owner owns. Only `-livetest` names.
- Never claim a fix works from one passing request. Tally or A/B.
- The Turnstile token is the only proof a widget cleared. The click solver returns without raising when it found nothing to click.
- Report failures with the engine's logged view of the page, not just the HTTP result.
- Hammering one host escalates it for everyone on this IP, including the owner's own stack. Spread trials across the testers and stop when you have the tally you need.
- No em dashes. Commas, parentheses, periods, colons.
