---
name: audit-scan
description: The manager half of the audit and bug-fix loop. Audits one dimension of Solverr's own code per run, tries hard to refute every candidate defect before believing it, and files the survivors as labeled GitHub issues with every affected site enumerated, so the worker loop has a queue. Triage only, it has no file-writing tools and never creates a branch. Use on a schedule, after a release, or when you want the known problems itemised rather than argued about.
argument-hint: "[--dry-run] [dimension] (omit to pick the least recently audited dimension)"
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh issue *)
  - Bash(gh label *)
  - Bash(uv run *)
  - Read
  - Glob
  - Grep
---

Audit one dimension of Solverr per run and turn each surviving defect into one issue the worker loop can pick up.

This is the sibling of `/port-scan`. Same contract, different input: `/port-scan` reads what upstream did, this reads what Solverr does. Like its sibling it triages only, **has no Edit or Write tool by design**, and never creates a branch.

## The rule this skill exists to enforce

**A finding that has not survived an attempt to refute it is a guess.** This project has already paid for the alternative. An audit reported `postform.py`'s percent-encoding as double-encoding, the reasoning was clean, and it was wrong: the form travels as a `data:text/html,` URL, so the browser URL-decodes before the HTML parser runs, and removing `quote()` broke POST for any value holding `%` or `#`. It took a live A/B against an echo service to find that out, after the "bug" had already been written up.

So the standing rule, also recorded in the `measure-before-fixing-odd-code` memory: **code that looks wrong here usually encodes a measured constraint.** Two consequences, both binding:

- A finding about code carrying a WHY comment, a `CLAUDE.md` note, or a ledger entry must say **why that recorded reason no longer holds**. If it cannot, it is not a finding.
- A finding whose proof needs a live browser is not confirmable here. File it as `loop:needs-human` with the A/B that would settle it, and let a person run `/live-check`.

## Arguments

- `--dry-run` prints every issue it would file, in full, and creates nothing. Always dry-run a dimension the loop has not audited before.
- A dimension name narrows the run. Omitted picks the least recently audited one, judged from the `source:audit` issue history.

## Step 1: Pick one dimension

One per run. A sweep over everything produces shallow findings in all of it, and the loop runs often enough that rotation covers the ground.

| Dimension | Scope |
|---|---|
| `engines` | `src/engines/`, and specifically whether the two engines still agree. A defect in one usually has a twin in the other. |
| `sessions` | `src/sessions.py`, `src/session_reaper.py`, `src/async_runtime.py`: lifecycle, the reaper, `in_use` counting, event-loop boundaries. |
| `contract` | `src/dtos.py`, `src/flaresolverr_service.py`: request validation, error shape, status codes, `/v1` byte compatibility, the ready banner. |
| `passthrough` | `src/passthrough.py`: routing, host allowlisting, in-flight slots, content types. |
| `config` | `src/config.py`, `src/geo.py`: env parsing, defaults, what happens when a value is absent or malformed. |
| `packaging` | `Dockerfile`, `.dockerignore`, `requirements.txt`, the workflows: build context, pins, what reaches the image. |
| `resources` | Anything that can leak: browsers, contexts, temp files, in-flight slots, unbounded growth (`maxTimeout` has no upper bound, Prometheus labels by domain are unbounded cardinality). |

## Step 2: Read the state before judging anything

1. **Existing issues, open and closed**, which is where prior verdicts live:
   ```
   gh issue list -R unseensnick/Solverr --state all --label source:audit --limit 100 \
     --json number,title,state,labels
   ```
   A closed issue is a decided question. **Read the closing comment before re-filing anything that resembles it**, because a rejected finding re-filed every run is how this loop turns into noise.
2. **`CLAUDE.md`** "Architecture (non-obvious)" and "Key decisions (WHY)". Nearly every entry there is a measured constraint that looks like a bug from the outside.
3. **`docs/dev/upstream-sync.md`** "Deliberately different". Same status: cited, never re-argued.
4. **`Handoff.md`** if present. "What failed" is the list of conclusions that were reached confidently and turned out wrong, and "Next steps" often names inherited issues already known and not yet filed. Filing those is good work; re-deriving them as new discoveries is not.
5. `git log --oneline -20` for what changed recently, since new code is where new defects are.

## Step 3: Find candidates

Read the dimension's files directly. Look for the things that are actually true here rather than a generic checklist: disagreement between the two engines, a resource acquired on one path and released on another, an `except` that swallows the case it was written for, a value from the request reaching a browser or a shell without validation, a default that is safe on one engine and not the other, an unbounded accumulation.

For each candidate, before it is allowed to become a finding, **enumerate every site**. List each hit with `file:line`, and record the search itself so the worker can re-run it. A candidate with one known site and no search behind it is not ready to file.

**Search recursively from `src/`, never `src/*.py`.** A glob stops at the top level and silently skips `src/engines/` and `src/bottle_plugins/`, which is where half the interesting code lives. That mistake produced a false finding on the first run of this skill: `end_use` looked like it was never called anywhere, because the only caller is `src/engines/chrome_engine.py:73`. Use `grep -rn '<pattern>' src/ --include=*.py`, or the `Grep` tool with a directory path, and confirm the hit count is plausible before trusting a zero.

## Step 4: Refute, then file what survives

This is the gate, and it is the reason this skill is worth running.

For every candidate, argue the other side as hard as you argued the first: find the comment, commit message, ledger entry, or test that would make it correct as written. `git log -S` and `git blame` on the line are the fastest route to the reason. Then:

- **Refuted**: drop it. If it is a candidate someone would plausibly re-raise, file it as a closed issue recording the reason, so the next run does not spend the same tokens.
- **Confirmed, provable without a browser**: prove it. A failing case in the browser-free suite, or a `uv run` snippet that demonstrates it. Attach the proof to the issue.
- **Confirmed, needs a live browser**: file it, label `loop:needs-human`, and write the exact A/B that would settle it.
- **Still unsure**: `loop:needs-human`. Uncertainty is a fine thing to report and a bad thing to hide.

## Step 5: File the issues

One issue per defect, never one per site. Title names the defect and the dimension:

```
audit(sessions): the reaper can close a driver a request is still holding
```

Body, in this order:

1. **The defect**, one sentence, stated as what goes wrong rather than what looks odd.
2. **Failure scenario**: concrete inputs or state, then the wrong output. If you cannot write one, you have a smell and not a defect.
3. **Every affected site**, as a checklist with `file:line`, plus the search that produced it. This is the section that stops the worker at four of five sites.
4. **The refutation attempt**: what you looked for that would have made it correct, and why that did not hold. A finding without this section has not been through step 4.
5. **Proof**, or the A/B that would produce it.
6. **Suggested fix**, including whether the proper fix needs a refactor. Say so plainly if it does; `.claude/rules/code-quality.md` prefers the proper fix over threading a workaround through the existing shape.
7. **Risk**: which tripwire zones it touches, if any.

Label every issue `source:audit`, plus `loop:ready` or `loop:needs-human`. The eligibility rules are the same as `/port-scan`'s and are not restated here: full scope enumerated, covered by the browser-free suite or provably inert to solving, no ledger divergence in scope, no change to the `/v1` shape or the ready banner, and `loop:needs-human` for the widget path, the budget split, `postform.py`'s `quote()` calls, session lifecycle, `geo.py`, dependency pins, or anything you are not certain about.

No site names in an issue title or body. Issues are a public surface.

## Step 6: Report

The dimension audited, candidates found, how many were refuted and by what, issues filed with numbers and labels, and anything skipped as already decided. **A run that refutes everything it found is a successful run**, and worth saying plainly, because the alternative is a loop that manufactures findings to look productive.

Do not update any doc. This skill files issues and nothing else.

## Rules

- One dimension per run. One issue per defect, with all its sites.
- Refute before believing. A finding that skipped step 4 does not get filed.
- Recorded reasons (WHY comments, `CLAUDE.md`, the ledger) are cited and answered, never ignored.
- Anything needing a live browser to prove goes to a human. This skill has no Docker and no browser.
- No Edit, no Write, no branches, no PRs. Triage only.
- No em dashes. Commas, parentheses, periods, colons.
