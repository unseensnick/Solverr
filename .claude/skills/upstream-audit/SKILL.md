---
name: upstream-audit
description: Audit Solverr against its two upstreams for drift. Compares the stealth engine against Byparr and the Chrome engine, sessions and the /v1 surface against FlareSolverr, classifying every difference as already covered, genuinely missing, or deliberately divergent, and checking that the /v1 request and response shape is still byte-compatible. Use when either upstream has moved, before a release, when a fix lands upstream that might apply here, or when the owner asks whether Solverr has fallen behind. Ends by updating the sync ledger.
argument-hint: "[scope] (e.g. 'stealth engine', 'contract only', omit for the full sweep)"
disable-model-invocation: true
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

| Agent | Compares | Brief it to check |
|---|---|---|
| Stealth vs Byparr | `src/engines/stealth_engine.py` vs `../Byparr/src/` | Browser launch options, the playwright-captcha framework and solver lifecycle, challenge detection lists, load-state waiting order, timeout handling, request options, dependency pins in `requirements.txt` vs `pyproject.toml`. |
| Chrome and sessions vs FlareSolverr | `src/engines/chrome_engine.py`, `src/sessions.py`, `src/utils.py` vs the same files upstream | Fixes upstream has that Solverr lacks, and files that are byte-identical (those need no review, say so). |
| Contract vs FlareSolverr | `src/dtos.py`, `src/flaresolverr_service.py`, `src/flaresolverr.py` | Field names, types, which fields are emitted unset, error shape, status codes, command handling, the banner. |

Demand `file:line` on both sides of every claimed difference. Cap each at ~500 words. Ask each for an explicit "identical, nothing to report" list, which is as useful as the differences.

## Step 3: Classify, then verify

Every reported difference gets one label, and the label is the work:

- **Already covered.** Solverr does the same thing by another route. Cite both sides and drop it.
- **Genuinely missing.** Upstream has something Solverr does not, and it applies here. Cite it, say what it would take, and rate the impact.
- **Deliberately different.** Solverr diverges on purpose. Cite the ledger entry or the code comment that records why. If nothing records it, that is itself a finding: the reason exists only in someone's head.

Then re-read, yourself, every difference you are about to call missing. The recurring false positive is an upstream fix Solverr already has under a different name, in a different layer, or with a different spelling.

Solverr is ahead of both upstreams in places. Say so explicitly when you find it, because "do not port backwards" is a real failure mode.

## Step 4: Report

Follow [.claude/rules/plan-output.md](../../rules/plan-output.md). Three sections in this order, matching how the audit gets consumed:

1. **Drift against Byparr**, each item labelled with its classification.
2. **Drift against FlareSolverr**, same.
3. **`/v1` contract compatibility**, which is pass or fail rather than graded: name any field renamed, dropped, retyped, or newly always-emitted, and confirm the banner is intact.

An audit with no implementation to propose omits the plan section.

## Step 5: Update the ledger

Whatever the outcome, update `docs/dev/upstream-sync.md`: the commit each upstream was audited through, today's date, and any new deliberate divergence with its reasoning. An audit that does not move the ledger forward will be run from scratch next time.

If the audit found work, ask which items to act on. Do not start fixing during the audit: a fix mid-audit contaminates the rest of the comparison.

## Rules

- Never edit anything under `../FlareSolverr`, `../Byparr`, or `../byparr-proxy`. They are read-only clones.
- A difference without `file:line` on both sides is not a finding.
- Inherited-but-unchanged files are worth naming. "Byte-identical to upstream" answers the question for that file permanently.
- Deliberate divergences are cited, never re-argued. Change one only when the owner asks.
- No em dashes. Commas, parentheses, periods, colons.
