---
name: deep-audit
description: Read-only, many-agent audit of a whole range of work (default the current branch against main) for bugs, misconfiguration, missing wiring, dead code, rule violations, and drift between the Chrome and stealth engines. Runs a Workflow that slices the range, points fourteen lenses at it (including two-ends tracing, where independent agents start at the write side and the read side of one value and a third compares them), refutes every finding before it counts, mutation-checks the range's new tests, and reports. Never edits code; the owner decides what gets fixed. Use when the user asks for a broad audit, a pre-release or pre-PR audit, or "check that all the changes work together". NOT for one diff (/pr-review), drift against FlareSolverr or Byparr (/upstream-audit), or one concrete task (/scout).
argument-hint: "[<rev>..<rev> | <path> | <surface>] [--lenses a,b] [--no-mutate] [--yes]"
disable-model-invocation: false
allowed-tools:
  - Bash(git status*)
  - Bash(git log *)
  - Bash(git diff *)
  - Bash(git merge-base *)
  - Bash(git rev-parse *)
---

Audit a whole range of Solverr work with many agents, verify every finding adversarially, and report.
**This skill writes one file, the ledger, and nothing else.** It never edits code, never fixes, never
commits. What gets fixed, and how, is the owner's call once the report is in.

How it differs from its siblings: `/pr-review` is a fast diff check with no verification,
`/upstream-audit` compares Solverr against its two upstreams, `/scout` plans one task. This one is
broad and slow on purpose, and the map step shows what it will cost before anything expensive runs.

The engine is [deep-audit.workflow.js](deep-audit.workflow.js), run through the `Workflow` tool with
`scriptPath`. Invoking this skill is the opt-in to multi-agent orchestration. A default run spawns far
more agents than the session's workflow-size guideline (about 15); if the owner wants that guideline
raised to match, it is "Dynamic workflow size" in the app's config.

## Arguments

- **No scope**: the current branch against `main` (`git merge-base main HEAD` to `HEAD`).
- **`<rev>..<rev>`**: that commit range.
- **A path**: the default range, limited to files under that path.
- **A surface name** (`sessions`, `result assembly`, `passthrough`): resolve it to paths through the
  seam-depth table in `.claude/rules/engine-layer.md` and "Where things live" in `CLAUDE.md`, then
  treat it as a path scope. If it resolves to nothing, ask.
- **`--lenses a,b`**: run only these lenses (names below). The critic stays inside the same set.
- **`--no-mutate`**: skip the mutation step. It is on by default because the browser-free suite runs in
  seconds here, and `engine-layer.md` requires every new test to have been seen failing.
- **`--yes`**: skip the approval stop after the map.

## The lenses

| Lens | Target | What it hunts |
|---|---|---|
| `correctness` | each code slice (`code-reviewer`) | Logic, `None` and empty handling, state, error handling, and whether changed functions still fit their callers |
| `security` | each code slice (`security-reviewer`) | Untrusted `/v1` and passthrough input reaching a browser, secrets and solved cookies in logs, credentials left on disk |
| `performance` | each code slice (`performance-reviewer`) | Browser launches, the `maxTimeout` budget, blocking the stealth loop, lock scope, unbounded growth |
| `concurrency` | each code slice | Waitress threads racing on shared state, the reaper against a session in use, locks held across I/O, floating tasks on the stealth loop, cleanup outside `finally` |
| `dead` | each code slice | Unused code, settings, env vars and branches, with a mandatory whole-tree re-search behind every "unused" |
| `rules` | each rule file over groups of slices | `engine-layer`, `code-quality`, `architecture`, `error-handling`, `security`, `testing`, checked line by line |
| `docs` | each docs slice (`doc-reviewer`) | Claims in the README, CHANGELOG, `CLAUDE.md`, rules, skills and `docs/dev` that current code contradicts, with counts re-derived |
| `tests` | each tests slice | Tests that cannot fail, over-claiming names, per-engine pairs that should be one conformance case, and a reasoned mutation of up to five |
| `tooling` | each tooling slice | Git and Claude Code hooks, CI workflows, settings and permissions: regexes that cannot match or match too much, Windows paths, rule precedence, CI steps that cannot fail |
| `wiring` | the range, twice | Packaging (Dockerfile, compose, `requirements.txt` against imports, the image's env); config and contract (every env var read against the README table and its default, `/v1` fields declared against fields read) |
| `parity` | user-visible changes, batched | Write-once gaps between the Chrome and stealth engines, and capabilities that silently do nothing on one engine |
| `sibling` | fix commits, batched | The same defect still sitting at another site: the other engine, the other command, the same call shape elsewhere |
| `upstream` | each taken-over surface | Behaviour the replaced FlareSolverr or Byparr code had that the spine lost, walked from theirs |
| `twoends` | each write/read pair | Two blind tracers (write side, read side) and a reconciler that reports every mismatch |

Every finding carries an evidence label: **executed** when a command's output decided it, **traced**
otherwise. The report keeps the two apart.

## Step 0: A clean tree and a real range

Agents read the working tree, while the range is committed history, so uncommitted edits make
them report against code that is not in the range. **If `git status` is dirty, stop and ask** the owner
to commit or stash first.

Resolve `base` and `head` to short SHAs with `git rev-parse`, and state the range and its size
(`git log --oneline <base>..<head> | wc -l`, `git diff --shortstat <base>...<head>`) in one line.

## Step 1: Ground

Read these, and pass them to the workflow verbatim as `ground`:

- **`parked`**: the `## Parked` section of `Handoff.md` (gitignored, on disk), if it has one. Parked
  items are not findings.
- **`ledger`**: the table rows in [ledger.md](ledger.md). A finding refuted before is not raised again
  while its reason still holds.

Deliberate divergences live in `docs/dev/upstream-sync.md`, and the engine-layer rulings in
`docs/dev/engine-layer-architecture.md`. The agents are told to read those themselves; don't inline them.

## Step 2: Map, then stop for approval

```
Workflow({ scriptPath: "<repo root>/.claude/skills/deep-audit/deep-audit.workflow.js",
           args: { mode: "map", base, head, pathFilter, lenses, ground } })
```

Four agents build the map: slices (code, docs, tests, tooling), up to 15 write/read pairs ranked by
risk, the fix commits plus the user-visible changes (the `[Unreleased]` entries the range added to
`CHANGELOG.md`, plus `feat` and `fix` commits), and the taken-over surfaces the range touches. The
result includes an estimate of finder agents per lens.

Show the owner: the slice list with sizes, the pairs, the surfaces, what was excluded and why, and the
estimate (finders, plus roughly one verifier per medium or low finding and three per high one). Then
**stop and wait**, unless `--yes` was passed. The owner may drop slices or pairs, add a pair, or narrow
the range; edit the map object to match. The map decides everything downstream, and this is the last
cheap point to fix it.

## Step 3: Audit

```
Workflow({ scriptPath: "<same>", args: { mode: "audit", base, head, pathFilter, lenses, ground, mutate, map } })
```

`mutate` is `true` unless `--no-mutate` was passed. It runs in the background; wait for the completion
notification. What the script does, so the report can explain it:

1. **Find.** Every lens over its targets, in parallel.
2. **Verify.** Findings on the same file within three lines merge (lenses overlap), then each is attacked
   by skeptics told to refute it, defaulting to refuted. A high finding gets three, each from a
   different angle: re-read the code, settle it by running something that changes no tracked file, and
   check whether it is ruled, recorded as deliberate, parked, in the ledger or contradicted by a
   passing gate. It survives on two of three. A medium or low finding gets one skeptic doing all three.
3. **Critic.** One agent reads the coverage table and names up to ten gaps. Those gaps get one round of
   finders and verification; there is no second round, and the report says so.
4. **Mutate.** One agent in a throwaway worktree deletes the production clause each of up to five new
   tests names, runs that test through the browser-free suite, and reports any that stay green.

If the run dies part-way, resume it with `resumeFromRunId` and the same args. Finished agents replay
from cache.

## Step 4: Report

In the conversation, in the structure of [plan-output.md](../../rules/plan-output.md): headline,
findings graded High / Medium / Low, stale docs, open questions. There is no plan section, since this is
an audit. The 700-word cap is waived for the findings list only, because a range audit's length scales
with its range. Everything around the list stays dense.

- **Each finding**: bolded one-line claim, `file:line`, the lenses that raised it, the evidence label.
  High findings get the evidence prose; medium and low findings are one line.
- **High findings that are still traced** go in their own short list, each with the probe that would
  settle it (a browser-free test, a log line, a `/live-check` run). They are not confirmed until
  someone runs that.
- **Stale docs** come from the `docs` lens and from any finding whose "defect" is really a doc that
  never caught up.
- **Coverage**: a compact table of lens by target with its status. Name every failed task and every
  gap the critic raised that was not re-run. Without this, "no findings" can't be told apart from
  "nobody looked".
- **By surface**: group confirmed findings by surface. A surface with more than about five confirmed
  findings is flagged as a design problem for the owner rather than a list of fixes.
- **Refuted**: a count, plus any refutation the owner might want to overturn.

Say plainly that the run was read-only, and which findings still need a live check.

## Step 5: The ledger

Append each refuted finding to [ledger.md](ledger.md) as one row: the range, `file:line`, the claim in
a short phrase, why it was refuted. **Leave the file uncommitted**; committing it is the owner's call.
A row stops holding once its reason stops being true, so a later run may re-raise it with the new
evidence.

## Rules

- Read-only. No code edits, no fixes, no commits, no pushes. The ledger is the only write.
- Never skip the approval stop after the map unless `--yes` was passed.
- Never report a finding the verifiers refuted, and never promote a traced high finding to confirmed.
- Never let a zero stand for coverage: a lens that returned nothing is reported with what it searched.
- No em dashes, no AI watermarks, in the report or the ledger.
