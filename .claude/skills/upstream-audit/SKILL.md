---
name: upstream-audit
description: Audit Solverr against its two upstreams for drift. Compares each clearing core against its upstream file, walks the taken-over surfaces (assembly, pipeline, budget, sessions, config, dtos) as behaviour from upstream's side, checks dependency pins including transitive ones, and classifies every difference as already covered, genuinely missing, or deliberately divergent, then checks that the /v1 request and response shape is still byte-compatible. Use when either upstream has moved, before a release, when a fix lands upstream that might apply here, or when the owner asks whether Solverr has fallen behind. Reads only, and asks before its one write, the sync ledger.
argument-hint: "[scope] (e.g. 'stealth engine', 'contract only', 'pins', omit for the full sweep)"
disable-model-invocation: false
allowed-tools:
  - Bash(git *)
  - Bash(uv run *)
  - Glob
  - Grep
---

Audit Solverr against `../FlareSolverr` and `../Byparr` for the scope in `$ARGUMENTS` (omitted means the full sweep). This skill reads and reports; it fixes nothing without approval.

The output is not "here is a diff". It is a classification: for each difference, **already covered**, **genuinely missing**, or **deliberately different**. A difference that turns out to be deliberate is a finding too, because the record of why is what stops the next audit from re-flagging it.

## Step 1: Ground the context

Cheap reads on the main thread, before spawning anything:

1. `docs/dev/upstream-sync.md`, the ledger: what was last taken from each upstream, and the standing list of deliberate divergences. **Anything on that list is not a finding unless the reasoning no longer holds.**
2. `git -C ../FlareSolverr log --oneline -20` and `git -C ../Byparr log --oneline -20`. Compare against the ledger's "synced through" markers to get the real delta. If the clones look stale, say so and offer to fetch; do not audit against a month-old clone silently.
3. `Handoff.md` if present, for deferred work and recorded dead ends.
4. `.claude/rules/workflow.md` "Fork compatibility", the two hard constraints: the `"FlareSolverr is ready!"` banner, and `/v1` byte compatibility.

## Step 2: Fan out

One `Agent` per area, `subagent_type: Explore`, in a single message. Skip an area outside the requested scope.

The two kinds of surface are compared differently, per `.claude/rules/engine-layer.md`. A **clearing core** is still upstream-derived, so it is compared file against file. A **taken-over surface** (marked Done in that file's seam-depth table) no longer has upstream's shape, so a file diff says nothing useful about it: walk upstream's behaviour instead, and mark each item **present** (cite the spine site), **deliberately dropped** (cite the ledger), or **missing**. That rule's "A takeover is not complete until its behaviour is inventoried" is why this is a separate agent.

| Agent | Compares | Brief it to check |
|---|---|---|
| Stealth clearing core vs Byparr | `_wait_until_cleared`, `_challenge_stays_gone`, `_click_turnstile`, `_widget_box`, `_frame_box`, `_container_box`, `_turnstile_token` in `src/engines/stealth_engine.py` vs `../Byparr/src/challenge.py` and `_navigate_and_solve` in `../Byparr/src/endpoints.py` | The algorithm and the widget constants by name and role, the confirm-after-clear re-check, the click cooldown, load-state waiting order. Also the adapter's browser launch options (`StealthContext.start`) vs `get_browser` in `../Byparr/src/utils.py`. |
| Chrome clearing core and inherited files vs FlareSolverr | The challenge wait in `ChromeEngine._evil_logic`, `click_verify`, `_get_turnstile_token`, `_resolve_turnstile_captcha` in `src/engines/chrome_engine.py` vs the same functions in `../FlareSolverr/src/flaresolverr_service.py`; `src/utils.py` and the ledger's byte-identical list vs the same files upstream | Fixes upstream has that Solverr lacks, and files that are byte-identical (those need no review, say so). Diff with `--strip-trailing-cr`: Solverr's copies are CRLF. |
| Taken-over surfaces, as behaviour | `src/assembly.py`, `src/pipeline.py`, `src/budget.py`, `src/sessions.py`, `src/config.py`, `src/dtos.py` vs what they replaced: the rest of FlareSolverr's `_evil_logic` (media blocking, the navigate-cookies-reload order, access-denied, `waitInSeconds`, `returnOnlyCookies`, the screenshot, result assembly), `SessionsStorage` in `../FlareSolverr/src/sessions.py`, upstream's `dtos.py` and `utils.get_config_*`, and Byparr's `read_item` in `../Byparr/src/endpoints.py` with `../Byparr/src/models.py` | Walk each upstream behaviour end to end, starting from upstream's code, never from ours, and mark it present, deliberately dropped, or missing. Include what upstream changed there since the ledger's audited-through commit. |
| Contract vs FlareSolverr | `src/dtos.py`, `src/flaresolverr_service.py`, `src/flaresolverr.py` | Field names, types, which fields are emitted unset, error shape, status codes, command handling, the banner. |
| Dependency pins | `requirements.txt` vs `../Byparr/pyproject.toml` and `../Byparr/uv.lock`, and vs `../FlareSolverr/requirements.txt` | Direct pins, and the transitive versions Byparr's lock resolves under an unchanged floor: `invisible-playwright` carries `invisible-core`, which carries the patched Firefox, so a lock that moves either is drift even when `pyproject.toml` did not change. Compare against what Solverr's pin resolves to, recorded in the ledger's Taken section, not against the local `.venv`, which can lag the pin. |

Demand `file:line` on both sides of every claimed difference. Cap each at ~500 words. Ask each for an explicit "identical, nothing to report" list, which is as useful as the differences.

## Step 3: Classify, then verify

Every reported difference gets one label, and the label is the work:

- **Already covered.** Solverr does the same thing by another route. Cite both sides and drop it.
- **Genuinely missing.** Upstream has something Solverr does not, and it applies here. Cite it, say what it would take, and rate the impact.
- **Deliberately different.** Solverr diverges on purpose. Cite the ledger entry or the code comment that records why. If nothing records it, that is itself a finding: the reason exists only in someone's head.

The behaviour inventory maps onto the same three labels: present is already covered, deliberately dropped is deliberately different, and missing is genuinely missing.

Then re-read, yourself, every difference you are about to call missing. The recurring false positive is an upstream fix Solverr already has under a different name, in a different layer, or with a different spelling.

Solverr is ahead of both upstreams in places. Say so explicitly when you find it, because "do not port backwards" is a real failure mode.

## Step 4: Report

Follow [.claude/rules/plan-output.md](../../rules/plan-output.md). Three sections in this order, matching how the audit gets consumed:

1. **Drift against Byparr**, each item labelled with its classification.
2. **Drift against FlareSolverr**, same.
3. **`/v1` contract compatibility**, which is pass or fail rather than graded: name any field renamed, dropped, retyped, or newly always-emitted, and confirm the banner is intact.

Takeover inventory items and pin drift go under the upstream they came from.

An audit with no implementation to propose omits the plan section.

## Step 5: Update the ledger

Whatever the outcome, propose the update to `docs/dev/upstream-sync.md`: the commit each upstream was audited through, today's date, and any new deliberate divergence with its reasoning. Write it once the owner confirms; it is the only file this skill writes. An audit that does not move the ledger forward will be run from scratch next time.

If the audit found work, ask which items to act on. Do not start fixing during the audit: a fix mid-audit contaminates the rest of the comparison.

## Rules

- Never edit anything under `../FlareSolverr`, `../Byparr`, or `../byparr-proxy`. They are read-only clones.
- A difference without `file:line` on both sides is not a finding.
- Inherited-but-unchanged files are worth naming. "Byte-identical to upstream" answers the question for that file permanently.
- Deliberate divergences are cited, never re-argued. Change one only when the owner asks.
- No em dashes. Commas, parentheses, periods, colons.
