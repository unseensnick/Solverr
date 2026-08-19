---
name: session-handoff
description: Write Solverr's Handoff.md so a fresh session resumes cleanly, and bring the CHANGELOG, dependent docs, and memory store in line with it. Use when the session is wrapping up ("I'm stepping away", "continue tomorrow", "wrap up"), before a `/clear`, when a long session has accumulated state that would be lost, or when the session is looping on a broken approach and a fresh agent would do better. Offer it proactively on those signals; do not wait to be asked.
argument-hint: "[optional: a narrower scope, e.g. 'handoff only' or 'skip the memory pass']"
disable-model-invocation: false
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
  - Bash(docker ps *)
  - Read
  - Glob
  - Grep
---

Write the handoff and bring the durable records in line with it, so the next session starts from an accurate picture instead of re-deriving one. The repo-specific twin of the global `session-handoff` skill: same spine, Solverr's file set and conventions.

## Default behavior (no arguments)

**Invoked with no arguments, do the full sweep.** No further prompting, no asking which parts to do:

1. **Rewrite `Handoff.md`** to the structure below.
2. **Check `CHANGELOG.md` `[Unreleased]`** actually covers what shipped this session, and that each entry leads with the user-facing effect.
3. **Update dependent docs** this session's work contradicts or completes (the set is in "The doc set").
4. **Update the memory store** with anything durable this session learned, and its index.

`$ARGUMENTS`, when present, narrows that scope. It never widens it. An unrecognized argument is a scope hint, not a topic: still do the sweep, biased toward what it names.

## Step 1: Establish the real state before writing

Never write the handoff from conversation memory alone. These are the facts the next session acts on, so confirm each:

- `git status --short` and `git log origin/main..HEAD --oneline`. State both in the header, including "pushed and clean" when that is the case.
- `git log --oneline -15` for the session's real commit range and subjects.
- **Release state**, since sessions here often end near one: `git tag --sort=-v:refname | head -3`, the `version` in `package.json`, and whether the tag was pushed. If a release shipped, confirm both workflows went green (`gh run list -R unseensnick/Solverr --limit 4`; pass `-R` even though `origin` is now the only remote) and that `ghcr.io/unseensnick/solverr:latest` and the version tag report the same digests.
- **Container hygiene**: `docker ps -a` for anything this session created. Every `-livetest` container, image, and network must be gone, and the owner's own `solverr` container must be exactly as it was found. A stray container holding port 8191 breaks their stack, so say explicitly in the handoff that cleanup happened.
- **Whether solving was verified live.** If the session changed the engines, detection, sessions, or the passthrough and `/live-check` was never run, that is a Current-state fact and a Next-step, not something to leave implied.
- Read the existing `Handoff.md`. If it describes a previous session as "this session", **rewrite it rather than appending**. Layered patches are how a handoff goes stale.

## Step 2: The handoff structure

Keep these sections. Omit one only when it genuinely has nothing, rather than writing a placeholder.

- **Goal**: the program-level outcome, not the next tactical step. What the fork is for, plus what this session was actually about.
- **Current state**: what works, what is half-done, what is deliberately not done. Lead with the branch, HEAD SHA, pushed/unpushed count, and tree state. Include the release and container facts from Step 1. Name measured effects where there are any (timings, tallies, digests), because "it works" does not survive a week.
- **Files**: only files central to the in-progress work, each with its role and status (new / modified / needs-attention). A handoff listing forty files is noise. Point at the browser-free test command when tests changed.
- **Changes made**: a line per commit, grouped when a group tells the story better. Mark which are verified live and which are not.
- **What failed**: the highest-value section. The approach, what was expected, what happened. Group variations of one idea so the next session does not try variation four. Include process failures (a wrong tool invocation, a bad assumption about the harness, a probe that tested the wrong thing) alongside code ones, and record corrections to your own earlier conclusions.
- **Next steps**: ordered and concrete, saying which step gates which. Branch the ones that depend on an unknown.
- **Conventions**: the repo rules the next session must respect from its first action, including the naming rule and the `/v1` compatibility constraints.

## Step 3: The doc set

Each file has a different job. Update the ones this session touched.

| File | Holds | Convention |
|---|---|---|
| `Handoff.md` (root) | Session state | **Gitignored (`.gitignore:133`). Edit on disk, never `git add` it.** |
| `CHANGELOG.md` `[Unreleased]` | User-facing effects | Benefit-first bold headline, effect not implementation. `.githooks/pre-commit` lints the format. |
| `README.md` | Current behavior and config | Update in the same change as the behavior. Describe what is true now, not the journey. |
| `CLAUDE.md` | Architecture, key decisions, skills index | Only when a non-obvious constraint changes. A measurement that explains a design (why the solver runs on a throwaway page) belongs here, not only in a commit body. |
| `docs/dev/upstream-sync.md` | The sync ledger | Update the audited-through row and add any new deliberate divergence with its reasoning. An audit that does not move this file gets re-run from scratch. |
| Memory store | Durable cross-session facts | `C:\Users\unseensnick\.claude\projects\E--Code-cloudflare-bypasses\memory\`, one fact per file, plus a one-line pointer in `MEMORY.md`. Plain directory, no commit needed. |

**The split that matters most:** `Handoff.md` is session state and dies with the session, the CHANGELOG is what users get told, and anything that will still be true in three months goes to a memory or `CLAUDE.md`. A durable gotcha left only in `Handoff.md` is lost the next time the handoff is rewritten.

For the memory pass, prefer updating an existing file over adding a near-duplicate, and delete any memory this session proved wrong. A memory naming a file, function, or flag is only true while that thing exists.

## Step 4: Commit and verify

1. Commit tracked doc changes (`CHANGELOG.md`, `README.md`, `CLAUDE.md`, `docs/dev/upstream-sync.md`) with a `docs(...)` subject. `Handoff.md` is never in that commit.
2. Run the browser-free tests if any code changed: `PYTHONPATH=src uv run --no-project python -m unittest test_detection test_response_shape test_request_validation test_browser_identity test_geo`.
3. Report the final branch state, so the owner knows whether anything is left to push, and whether the tag and image are published or pending.

## Solverr conventions the global skill cannot know

- **The naming rule has a split the next session will trip over.** `Handoff.md` is gitignored, so target-site names are fine there and useful for continuity. Every tracked surface (commits, `CHANGELOG.md`, `README.md`, `CLAUDE.md`, branch names) must stay generic. Do not lift a sentence from the handoff into the CHANGELOG without genericizing it: `.githooks/commit-msg` and the Standards CI job will reject it.
- **Commit subjects `<= 72` chars**, `type(scope): summary`, no em dashes, no AI watermarks, no bare `#N` (use `owner/repo#N`). The `commit-msg` hook aborts a chained `git add && git commit`, so check the message before chaining.
- **Hooks are local.** They only run in a clone that ran `git config core.hooksPath .githooks`; CI is the backstop. Mention it in the handoff if this clone has not opted in.
- **Python only through `uv`** (`uv run --no-project python ...`). There is no system Python on this machine.
- **Do not change the `"FlareSolverr is ready!"` banner**, and keep `/v1` byte-compatible: additive optional fields only.
- **Unit tests cannot prove a solve.** Any claim that solving still works needs `/live-check`, with a tally or a same-window A/B behind it, never a single request.

## Handing back

After writing, give the owner the next-session prompt verbatim:

> Read `Handoff.md` and continue from where the previous session left off. Before you start working, summarize back to me your understanding of the goal, current state, and what you plan to do next so I can confirm before you proceed.

The summarize-back step catches a misread before the fresh session commits to an action.

## Rules

- Verify state with git, `gh`, and `docker ps` before writing it down. A handoff asserting "pushed and clean" that is neither is worse than none.
- Do not hide failures. A handoff that omits the dead ends walks the next session straight into them.
- Label hypotheses as hypotheses. "Suspect Cloudflare re-challenged", not "Cloudflare re-challenged", unless the tokens were counted.
- Do not pad. Short and honest beats long and hedged.
- No progress narration in the artifact. It is a briefing, not a journal.
- No em dashes. Commas, parentheses, periods, colons.
