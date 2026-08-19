---
name: port-scan
description: The manager half of the upstream port loop. Compares Byparr and FlareSolverr against the sync ledger, decides which new upstream commits are portable, and files one labeled GitHub issue per portable change so the worker loop has a queue. Triage only, it has no file-writing tools and never creates a branch. Use on a schedule, or when an upstream has moved and you want the work itemised before deciding what to act on.
argument-hint: "[--dry-run] [byparr|flaresolverr] (omit for both, --dry-run to file nothing)"
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh issue *)
  - Bash(gh label *)
  - Read
  - Glob
  - Grep
---

Scan the upstreams for work that has not reached Solverr, and turn each portable change into one issue the worker loop can pick up.

This skill triages. **It has no Edit or Write tool and that is deliberate**: a manager that can also write code stops being a check on the worker. It never creates a branch, never touches `src/`, and never advances the ledger. The ledger moves when a port merges, not when it is spotted.

## Arguments

Parse `$ARGUMENTS` before anything else:

- `--dry-run` prints every issue it would file, in full, and creates nothing. **Always dry-run first on a ledger you have not scanned before.**
- `byparr` or `flaresolverr` narrows to one upstream. Omitted means both.

## Step 1: Refresh the reference clones

The siblings are read-only checkouts and they go stale. Fetch refs without touching either working tree, then read the remote branch rather than the local checkout:

```
git -C ../Byparr fetch --quiet origin
git -C ../FlareSolverr fetch --quiet origin
git -C ../Byparr log --oneline <ledger-sha>..origin/main
git -C ../FlareSolverr log --oneline <ledger-sha>..origin/master
```

Byparr's default branch is `main`, FlareSolverr's is `master`. A `fetch` is the only write either clone ever gets from this skill. Never check out, reset, pull, or edit anything under `../Byparr`, `../FlareSolverr`, or `../byparr-proxy`.

If a range comes back empty for an upstream, say so and skip it. An empty Byparr range is the expected result most days, and an empty FlareSolverr range is the expected result most months.

## Step 2: Read the state before judging anything

Three sources, all of them binding:

1. **`docs/dev/upstream-sync.md`**, the ledger. The "Audited through" table gives the range start. The "Deliberately different" list is the standing set of refusals: **an upstream commit that re-litigates one of those is not an issue**, it is already answered. The per-audit notes also record "not applicable" verdicts by SHA; do not re-file those either.
2. **Open issues already filed**, so a repeat scan is idempotent:
   ```
   gh issue list -R unseensnick/Solverr --state all --label loop:ready --label loop:needs-human --label loop:in-review --limit 100 --json number,title,state
   ```
   Every issue this skill files carries its upstream SHA in the title, which is what makes the dedupe reliable. A commit with an issue already open, or with a closed issue, is done.
3. **`Handoff.md`** if present, for recorded dead ends. Something listed under "What failed" is not eligible, whatever upstream did with it.

## Step 3: Classify each commit

Read the actual diff (`git -C ../Byparr show <sha>`), not the subject line. Subjects lie about scope, and Byparr's arc in the last audit was mostly commits that undid earlier commits.

Every commit lands in exactly one bucket:

- **Not applicable.** Upstream code with no counterpart here (the `/load` endpoint, their ruff config, their CI, test churn against files Solverr does not have). No issue. Record it in the scan summary so the next scan does not re-reason it.
- **Already covered.** Solverr does the same thing by another route. Cite both sides with `file:line`. No issue.
- **Deliberately refused.** It matches a "Deliberately different" ledger entry. No issue, cite the entry.
- **Portable.** Everything else. One issue.

Then decide the label for each portable commit, and **default to `loop:needs-human` whenever the answer is not obvious.** Broad autonomy is where this loop would hurt.

Eligibility is about **how well the change is understood, never about how few lines it touches.** A rule counting files would teach the worker to port the one call site that made the issue readable and leave its siblings alone, which is the failure `.claude/rules/code-quality.md` names directly: the reported case passing is not the bug being fixed. Solverr has two engines written against each other, so an upstream fix to one usually has a counterpart in the other.

`loop:ready` requires all of:

- **The full scope is enumerated in the issue.** Before labeling, grep for every place the change applies (both engines, the controller, the detection lists) and list each with `file:line`. If the search cannot be made exhaustive, the scope is not known, so the label is `loop:needs-human`.
- It is covered by the browser-free suite, or the change is provably inert to solving (a log line, a message string, a bounds check).
- No ledger divergence entry names any file in that scope.
- It changes neither the `/v1` request or response shape nor the `"FlareSolverr is ready!"` banner.

A change spanning four files with all four identified is a better `loop:ready` candidate than a one-file change whose blast radius nobody has checked.

`loop:needs-human` if any of these are true, and say which one:

- It touches the widget measuring or click path in `src/engines/stealth_engine.py`.
- It touches the shared `maxTimeout` budget split in `src/flaresolverr_service.py`.
- It touches the `quote()` calls in `src/postform.py`. That code has been "fixed" once already and the fix was wrong.
- It touches session lifecycle or the reaper (`src/sessions.py`, `src/session_reaper.py`).
- It touches `src/geo.py`, where the timezone and locale are paired on purpose.
- It moves a dependency pin, especially the stealth browser stack. Those get `/live-check` and a human before they merge, which is the whole reason the Renovate rule exists.
- It reopens a documented divergence, or the reasoning behind that divergence may no longer hold.
- You are not certain which bucket it belongs in.

## Step 4: File the issues

One issue per portable commit. Title carries the upstream and the short SHA so the dedupe in step 2 works:

```
port(byparr 2852dc2): report an unreachable target as a gateway failure
```

Body, in this order and nothing else:

1. **Upstream commit**, full SHA and a link, plus the files it touches upstream.
2. **What it does**, two or three sentences, from reading the diff.
3. **Scope here**, as a checklist of every site the change applies to, each with `file:line`, and the search that produced the list so the worker can re-run it. This is the section that stops a partial fix from looking finished.
4. **Why it is portable**, and for `loop:needs-human`, which trigger from step 3 fired.
5. **Ledger context**: any entry that bears on it, quoted.
6. **Suggested verification**, which checks from the live matrix would prove it.

Site names never appear in an issue title or body. `.claude/rules/workflow.md` covers commits, README, CHANGELOG and release notes; issues are a public surface too, so the same rule applies. Use "a Cloudflare-gated site" or "an indexer".

Ensure the labels exist before filing (`gh label list -R unseensnick/Solverr`), and create any that are missing:

Every issue this skill files also carries `source:upstream`, which is how `/loop-work` tells it apart from an audit finding.

| Label | Meaning |
|---|---|
| `loop:ready` | Eligible for the worker loop to take unattended. |
| `loop:needs-human` | Real work, but the worker must not start it alone. |
| `source:upstream` | Filed here, from an upstream commit. |
| `loop:in-review` | The worker opened a draft PR. Set by the worker, never here. |
| `needs-live-recheck` | The live tally was ambiguous. Set by the worker, never here. |

Always pass `-R unseensnick/Solverr` on `gh` calls. It is redundant now that the FlareSolverr remote is gone, and it costs nothing to keep the habit.

## Step 5: Report

A short summary to the chat, whatever the outcome:

- The commit range scanned per upstream, and the head SHA each was scanned to.
- Counts per bucket, then the issues filed with their numbers and labels.
- Anything skipped as already-open, so a repeat run is visibly a no-op.

**Do not update `docs/dev/upstream-sync.md`.** The ledger records what has landed. Moving it here would orphan every issue this scan just filed, and the next scan would see a clean range and file nothing.

## Rules

- No Edit, no Write, no branches, no PRs, no merges. Triage only.
- Never edit anything under `../FlareSolverr`, `../Byparr`, or `../byparr-proxy`. A `fetch` is the one exception.
- Read the diff before classifying. A subject line is not evidence.
- When the bucket or the label is genuinely unclear, `loop:needs-human` and say why. Guessing costs the owner a bad PR; escalating costs them one glance.
- A ledger divergence is cited, never re-argued.
- No em dashes. Commas, parentheses, periods, colons.
