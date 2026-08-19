# The loops

Work on Solverr has two recurring shapes. An upstream moves and someone decides what applies here. Or something in this repo is wrong and someone finds it, proves it, and fixes it. Both end the same way: a branch, a verification pass, a PR a person reviews.

This file defines both as loops with an explicit contract, so they run on a schedule instead of only when someone remembers.

A loop is recurring work with five parts: a **job**, **permissions**, a **schedule**, **state that outlives the conversation**, and an **evaluation**. Three of those already existed here. `docs/dev/upstream-sync.md` is the state for the port loop, GitHub issues are the state for both. `/live-check` is the evaluation. The commit hooks, the pre-commit lint, and the Standards CI job are the permissions boundary. The loops add the job definitions and the schedule.

## Shape: two managers, one worker

```
/port-scan   (upstream moved)  ─┐
                                ├─> labeled issues ─> /loop-work ─> draft PR ─> a person merges
/audit-scan  (defect here)     ─┘
```

The managers decide what should be done and have no file-writing tools. The worker does one thing at a time and cannot merge it. They never talk to each other directly; they talk through issues and labels, which is also where a person can see and change the queue.

A single skill that both triaged and implemented would be simpler and would lose the only structural check in the design: bad triage would go straight to a branch. Splitting it means a wrong call costs a wrongly labeled issue, which is one glance to fix.

One worker rather than two, because the mechanics after triage are identical: worktree, branch, gates, draft PR. Only the provenance differs, and a `source:` label carries that.

Accountability sits with the person operating this. Every merge is a human decision.

## Manager: `/port-scan`

- **Job**: notice that an upstream moved, and itemise which of it applies here.
- **Inputs**: `../Byparr` and `../FlareSolverr` (fetched, never checked out), `docs/dev/upstream-sync.md`, existing issues, `Handoff.md`.
- **Allowed**: create and label issues, create labels.
- **Forbidden**: any file write, any branch, any PR, any change to the ledger.
- **Output**: one issue per portable upstream commit, labeled `source:upstream` plus `loop:ready` or `loop:needs-human`, with the upstream SHA in the title and every affected site enumerated.
- **Evaluation**: a repeat run with nothing new upstream files nothing. That is the loop's own regression test.
- **Escalation**: anything it cannot classify confidently becomes `loop:needs-human`.

## Manager: `/audit-scan`

- **Job**: audit one dimension of this repo per run and itemise the defects that survive an attempt to refute them.
- **Inputs**: one dimension of `src/`, `CLAUDE.md`'s recorded decisions, the ledger's divergences, prior `source:audit` issues including closed ones, `Handoff.md`, recent commits.
- **Allowed**: create and label issues, run the browser-free suite to prove a finding.
- **Forbidden**: any file write, any branch, any PR. No Docker and no browser, which is why anything needing a live solve to prove goes to a human.
- **Output**: one issue per defect (never one per site), labeled `source:audit` plus `loop:ready` or `loop:needs-human`, carrying a failure scenario, every affected site, and the refutation attempt that failed to kill it.
- **Evaluation**: a run that refutes everything it found is a successful run. A loop that manufactures findings to look productive is the failure mode.
- **Escalation**: unproven without a browser, or unsure, both become `loop:needs-human`.

**Why the refutation gate is the centre of this loop.** An earlier audit reported `postform.py`'s percent-encoding as a double-encoding bug. The reasoning was clean and it was wrong: the form travels as a `data:text/html,` URL, so the browser URL-decodes before the HTML parser runs, and removing `quote()` broke POST for values holding `%` or `#`. A live A/B caught it, after the finding was already written up. Code that looks wrong in this repo usually encodes something that was measured, so a finding must answer the recorded reason rather than not notice it.

## Worker: `/loop-work`

- **Job**: land exactly one `loop:ready` issue as a draft PR carrying its own evidence.
- **Inputs**: one issue, the repo at `origin/main`, and whatever the issue names.
- **Allowed**: a worktree under `.worktrees/`, a `loop/*` branch, edits under `src/`, tests, `CHANGELOG.md`, the ledger (for `source:upstream` only), throwaway containers suffixed `-loop<N>`, a push of its own branch, a draft PR.
- **Forbidden**: merging, marking a PR ready, pushing to `main`, force-pushing, tagging, releasing, taking a second issue, taking a `loop:needs-human` issue, bypassing a hook, touching the sibling clones.
- **Output**: a draft PR with three gates reported as numbers and the site checklist marked off, and the issue relabeled `loop:in-review`.
- **Evaluation**: the three gates below.
- **Escalation**: `/scout` finding the change bigger than the issue said, a live tally clearly worse than baseline, or a High review finding whose fix would reach a tripwire zone.

## Scope, and why eligibility does not count files

The first draft of these rules made `loop:ready` mean "at most one file under `src/`". That is exactly the rule that produces patchwork: it rewards fixing the one call site that made the bug visible and leaving its siblings alone, and it makes a well-understood four-file change ineligible while an unexamined one-file change sails through.

Eligibility is about **how well the scope is known**, never how small it is. Every issue carries a checklist of every affected site with `file:line`, plus the search that produced it, and the worker re-runs that search before calling the code done. Three outcomes are acceptable: fix every site, fix some and list the rest in the PR body with the reason, or escalate. Silence about a site is not one of them.

Solverr has two engines written against each other, so a defect in one usually has a twin in the other. That is the single most common way a fix here ends up half done.

Refactoring is in scope when the correct fix needs it, in the same change, with the reason in the commit body. What stays out is adjacent cleanup nothing in the issue motivates. See `.claude/rules/code-quality.md`.

## The three gates

Cheapest first, so a run fails fast.

**Gate A, the browser-free suite.** 138 tests, no browser, seconds. Pass or fail, no interpretation.

**Gate B, the live solve tally.** The one gate that cannot be boolean. Cloudflare's behavior varies with IP reputation, time of day, and how hard a host was hit five minutes ago, so a single result carries no information either way. The worker builds a baseline container off `origin/main` and runs it interleaved with the change, trial for trial, in the same window. Within noise is a pass, clearly worse is a fail, and an ambiguous window opens the PR labeled `needs-live-recheck` with the raw numbers. Asking a person is a valid outcome; a confident verdict off one sample is not.

**Gate C, the indexer chain.** A throwaway Prowlarr and `byparr-proxy` on a private network, on non-default ports, running one search. Prowlarr disables an indexer after about 100 seconds, so a slow solve reads as a broken indexer; on a throwaway instance that costs nothing.

## Labels

| Label | Set by | Meaning |
|---|---|---|
| `loop:ready` | either manager | The worker may take this unattended. |
| `loop:needs-human` | either manager, or the worker on escalation | Real work, but not alone. |
| `loop:in-review` | worker | A draft PR exists. |
| `source:upstream` | `/port-scan` | Came from an upstream commit. |
| `source:audit` | `/audit-scan` | Came from a verified defect here. |
| `needs-live-recheck` | worker | Gate B was ambiguous. Re-run the tally before merging. |

## Tripwire zones

`loop:needs-human`, always, whichever manager finds it: the widget measuring and click path in `stealth_engine.py`, the shared `maxTimeout` budget split, the `quote()` calls in `postform.py`, session and reaper lifecycle, `geo.py`, and any dependency pin for the browser stack. `Handoff.md`'s "What failed" section is the list of conclusions a confident agent reaches and gets wrong, so it is also the list of things the worker may not reason about alone.

## Guards

The worker can write code and push a branch, so the guards around it are worth stating.

`.claude/hooks/block-dangerous-commands.sh` blocks pushes to protected branches, force pushes, and destructive operations. It matches **both** the Bash and the PowerShell tool: matching only Bash left every guard bypassable by rewriting the same command in PowerShell, which is a different tool with a different name and its own spelling for every destructive operation. Fixtures under `.claude/hooks/tests/fixtures/` cover both syntaxes; run them with `bash .claude/hooks/tests/run-all.sh`.

The commit-msg and pre-commit hooks are never bypassed. `--no-verify` is not an option the worker has.

## Running them

Dry-run first, always, on any shape the loop has not seen:

```bash
/audit-scan --dry-run sessions
```

Then the real scan, and the worker on whatever it queued:

```bash
/loop-work --dry-run 12
```

On a schedule, one at a time, with `/loop`:

```bash
/loop 1d /port-scan
```

Cadence follows the input, not the calendar. Byparr moves in bursts (30 commits across two days in August 2026, then nothing) and FlareSolverr is close to dormant (one commit in July 2026, months of gaps before it), so daily is right for `/port-scan` and cheap when the range is empty. `/audit-scan` is bounded by its dimension list rather than by upstream activity, so weekly gets through the rotation without re-treading ground.

Both run locally and only while the machine is on. Gates B and C need Docker and a real network path to live challenges, so a cloud scheduled agent cannot serve as the worker.

## What these loops do not do

They do not merge, release, or decide that a divergence should end. They do not touch the engines without a person in the path. They do not run the paid CAPTCHA escalation, which needs an API key, and they do not cover a Cloudflare-gated PDF, because no such URL has been found. Those stay in the "not covered" section of every PR body the worker writes.
