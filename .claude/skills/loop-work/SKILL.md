---
name: loop-work
description: The worker shared by both loops. Takes exactly one loop:ready issue, whether it came from /port-scan or /audit-scan, does the work in its own worktree and branch, fixes every site the issue lists rather than the one that reproduced, proves it with the browser-free suite, a live solve tally against a same-window baseline, and the indexer chain, then opens a draft PR carrying that evidence. It never merges, never pushes to main, and stops rather than guess. Use to land a queued item, or with --dry-run to see what it would do first.
argument-hint: "[--dry-run] [issue number] (omit to take the oldest loop:ready)"
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
  - Bash(uv run *)
  - Bash(docker *)
  - Bash(curl *)
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Skill
---

Land one queued issue end to end, and stop at a draft PR a person reviews.

Both managers feed this one worker. `/port-scan` files `source:upstream` issues, `/audit-scan` files `source:audit` issues, and everything below is the same for both except where marked. The containment is the point: this skill can write code, so everything else about it is narrowed to **one issue per run**, one branch, one draft PR, and a hard stop the moment the work turns out to be bigger than the issue said. A person approves every merge. Nothing here merges, marks a PR ready, pushes to `main`, tags, or releases.

## Arguments

- `--dry-run` reads everything, mutates nothing, and prints the plan, the eligibility verdict, the exact commands, and the PR body it would write. It claims no issue, creates no branch, starts no container, and pushes nothing. Use it on the first run against any new issue shape.
- An issue number takes that issue. Omitted takes the oldest open `loop:ready`.

## Step 1: Claim exactly one issue

```
gh issue list -R unseensnick/Solverr --label loop:ready --state open \
  --json number,title,createdAt,labels --limit 20
```

Take the oldest, or the one named in `$ARGUMENTS`, and read its `source:` label to know which manager filed it. Then stop if any of these hold, and say which:

- The queue is empty. That is a normal, successful, no-op run.
- The issue carries `loop:needs-human`. That label exists to stop this skill. Never take one, and never relabel one to `loop:ready` to get around it.
- The issue carries `loop:in-review`. It already has a PR.

Claim it by removing `loop:ready` (`gh issue edit <N> -R unseensnick/Solverr --remove-label loop:ready`). If the run bails later for any reason, put the label back unless the bail reason was an escalation to `loop:needs-human`.

## Step 2: Preflight

Refuse to start on a dirty tree. All of these must hold, and a failure is a stop, not something to work around:

- `git status --porcelain` is empty.
- The current branch is `main` and it matches `origin/main` (`git fetch origin && git rev-parse main origin/main`).
- No worktree or branch already exists for this issue (`git worktree list`, `git branch --list 'loop/*'`).
- Docker is up, and the ports this run needs are free (`docker ps -a`).

## Step 3: Worktree and branch

```
git worktree add .worktrees/loop-<N> -b loop/<N>-<slug> origin/main
```

`.worktrees/` is gitignored. The slug is a short kebab summary and, like every branch name here, carries no site names. Confirm the hooks reach the worktree (`git -C .worktrees/loop-<N> config --get core.hooksPath` should read `.githooks`); local config is shared across worktrees, so this is a check rather than a step. Never bypass a hook with `--no-verify`.

Every command from here runs with `-C .worktrees/loop-<N>` or after a `cd` into it. The shell resets between calls, so assume neither.

## Step 4: Scout first, and treat its verdict as a gate

Run `/scout` against the issue before editing anything. This is the escalation gate, and it is the single most important step in the skill.

**Stop and escalate** if scout reports any of the following: the change reaches the engines, sessions, or the controller in a way the issue did not anticipate; it collides with a "Deliberately different" ledger entry; the real scope is wider than the issue's checklist and the extra sites land in a tripwire zone; or it ends with a blocking open question. Escalating means: post the scout report as an issue comment, relabel `loop:needs-human`, remove the worktree and delete the branch, and report what stopped it.

**Do not implement a smaller version of the task instead.** A narrowed fix that passes the gates is the worst outcome this loop can produce, because it looks like success.

## Step 5: Implement, at every site

Follow the scout plan. Then, before calling the code done, re-run the issue's own scope search and confirm every listed site is addressed. `.claude/rules/code-quality.md` is binding here: a bug present in five places is one bug with five sites, and the reported case passing is not the bug being fixed. Solverr's two engines were written against each other, so a defect in one usually has a twin in the other.

Three outcomes are acceptable and no others. Fix every site. Or fix some and **list the rest in the PR body with the reason**, which is a decision the reviewer can see and overrule. Or escalate, if the full fix would reach a tripwire zone.

Refactor when the correct fix needs it, in the same change, with the reason in the commit body. Extracting a helper, changing a signature, or moving a call site is the proper fix when the alternative is threading a workaround through the shape that is already there. What stays out of scope is adjacent cleanup that no part of this issue motivates.

Alongside the code:

1. **Tests**, if the change is reachable from the browser-free suite. It usually is. A fix at five sites gets coverage that would fail if any one of them regressed.
2. **`CHANGELOG.md`**, only when a deployer or an API client could notice. Behavior, config, response shape, image. A change with no observable effect gets a plain line under `Other`, and contributor tooling gets no entry at all.
3. **`docs/dev/upstream-sync.md`**, for `source:upstream` issues only: move the "Audited through" SHA for that upstream and add any new divergence with its reasoning. **This is the loop's state advance and it belongs in the branch**, so the ledger moves when the PR merges and not before. A port that leaves the ledger behind gets re-filed by the next scan. `source:audit` issues never touch the ledger.

Commit with the message standard from `.claude/rules/workflow.md`. Check the message against the pre-commit checklist before running the command: the hook aborts a chained `git add && git commit`, so a bad message costs the whole chain.

## Step 6: Verify, in three gates, cheapest first

### Gate A, the browser-free suite

```
PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src
```

138 tests, no browser, seconds. A hard gate: any failure stops the run. Fix it or escalate, never delete or skip the test.

### Gate B, the live solve tally

Run `/live-check` scoped to what changed, and read its rules before starting: a single passing request proves nothing.

Build and run on ports that leave the defaults alone:

```
docker build -t solverr:loop<N> .
docker run -d --name solverr-loop<N> --shm-size=512m -p 8291:8191 \
  -e LOG_LEVEL=debug -e STEALTH_ENGINE=true -e DEFAULT_ENGINE=chrome solverr:loop<N>
```

**Build the baseline in the same window.** A second container off `origin/main` (`solverr:loop<N>-base` on 8391), run trial for trial against the same testers, interleaved rather than one batch after the other. Cloudflare's behavior drifts by the hour, so a baseline measured yesterday is not a baseline.

The verdict is a tally, never a single result:

- **Within noise of the baseline**: pass. Record the numbers anyway.
- **Clearly worse than the baseline**, same testers, same window: fail. Comment the numbers on the issue, relabel `loop:needs-human`, keep the branch, and open no PR.
- **Ambiguous**: both arms low, or the failures look like refusals rather than breakage (several distinct `__cf_chl_tk` values inside one request, or the bare-browser probe failing too). Open the draft PR anyway, label it `needs-live-recheck`, and put the raw numbers and the reason in the body. An ambiguous window is a reason to ask a person, not to block or to bluff.

Never report a live result as pass or fail without the trial count behind it.

### Gate C, the indexer chain

The chain is the thing that actually consumes Solverr, so it gets exercised. Everything here is throwaway and isolated:

- A private network, plus a fresh Prowlarr and a fresh `byparr-proxy` built from `../byparr-proxy`, both named with the `-loop<N>` suffix. Prowlarr on 9796 and the proxy on 8988, never 9696 or 8888.
- Point the proxy's `BYPARR` at the run's own container (`http://solverr-loop<N>:8191/v1`) on that private network.
- Import the definition from `../byparr-proxy/definitions/`, run **one** search, and assert results came back.

One search, not a sweep. Prowlarr gives a request about 100 seconds and then disables the indexer, so a slow solve reads as a broken indexer. On a throwaway instance that costs nothing, which is why the owner's own Prowlarr is never used here. Report the elapsed time next to the result count; a search that succeeds at 95 seconds is a finding.

## Step 7: Review

Run `/pr-review` against the branch diff. Fix every High finding before opening the PR, or escalate if a fix would widen the change into a tripwire zone. Medium and Low findings go in the PR body for the human to weigh.

## Step 8: Open the draft PR

```
git -C .worktrees/loop-<N> push -u origin loop/<N>-<slug>
gh pr create --draft -R unseensnick/Solverr --base main --head loop/<N>-<slug> \
  --title "<the commit subject>" --body-file <file>
```

Draft, always. The body carries the evidence, in this order:

1. **What changed and why**, plain language first.
2. **Provenance**: the upstream commit with its full SHA and link for `source:upstream`, or the audit finding and how it was verified for `source:audit`.
3. **Scope covered**: the issue's site checklist, each line marked fixed or deliberately left, with the reason for anything left. A reviewer must be able to see partial coverage without reading the diff.
4. **Gate A**: the test count and result.
5. **Gate B**: the tally table, new against baseline, trials per engine per tester, elapsed times, and the verdict with its reasoning.
6. **Gate C**: result count and elapsed time for the search.
7. **What was not covered.** An unverified path claimed as verified is worse than an admitted gap.
8. **Review findings** that were not fixed.
9. `Closes unseensnick/Solverr#<N>`, in the explicit `owner/repo#N` form. A bare `#N` violates the message standard.

Then relabel the issue `loop:in-review`, and add `needs-live-recheck` to the PR if gate B was ambiguous.

## Step 9: Clean up, then report

Remove everything this run created except the branch and the PR: both `-loop<N>` containers and images, the throwaway Prowlarr and proxy, their network and volumes, and the worktree (`git worktree remove .worktrees/loop-<N>`). The branch stays, because the PR points at it.

Then report: the issue taken, the branch, the PR URL, the site checklist with its coverage, the three gates with their numbers, what was escalated, and confirmation that the cleanup left nothing behind (`docker ps -a`, `git worktree list`).

## Never

- Merge, mark a PR ready for review, approve, or close a PR.
- Push to `main`, force-push anything, tag, or cut a release.
- Take more than one issue in a run, or take a `loop:needs-human` issue.
- Relabel a `loop:needs-human` issue to `loop:ready`.
- Bypass a git hook, delete or skip a failing test, or silence a linter.
- Rewrite a command in PowerShell to get past a guard that stopped it in Bash. The hook matches both, and trying is itself a reason to stop and report.
- Edit anything under `../FlareSolverr`, `../Byparr`, or `../byparr-proxy`.
- Report a live gate as passing from one request.
- Put a site name in a branch name, commit, PR title, PR body, or CHANGELOG entry.

## Rules

- One run, one issue, one branch, one draft PR.
- Every site the issue lists is fixed, or listed as left with a reason. Silence is not an answer.
- Stop and escalate rather than shrink the task to fit what the loop can do alone.
- Every claim in the PR body is a number or a `file:line`, never an assurance.
- No em dashes. Commas, parentheses, periods, colons.
