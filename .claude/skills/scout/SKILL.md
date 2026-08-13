---
name: scout
description: Investigate a non-trivial task, then produce the plan for it. Emits a file:line-cited findings report covering current behavior, the upstream equivalent in FlareSolverr or Byparr, the engine and event-loop constraints that bind the approach, helpers to reuse, stale docs, then a sequenced plan and any blocking open questions. Use before porting an upstream change, touching the engines or session lifecycle, or any change that crosses the controller. The point is to ground the plan in evidence, not memory. Investigates deeply and verifies adversarially by default; never edits files.
argument-hint: "<task description> (e.g., 'port Byparr's PDF handling', 'make sessions survive a reaper race')"
disable-model-invocation: false
allowed-tools:
  - Bash(git *)
  - Bash(uv run *)
  - Glob
  - Grep
---

Investigate the task described by `$ARGUMENTS` deeply enough that the resulting plan cannot contain invented functions, stale memories, or assumed library behavior. Output is a findings report **and the plan that follows from it**. **This skill never edits files and never writes code**: it proposes, the owner approves, implementation is a separate step.

## Standing defaults (no need to ask for these)

- **Depth.** Open the files and verify each claim against current code. Never pattern matching, never inference from a symbol name. A plausible mechanism that was not read is not a finding.
- **Completeness.** Keep investigating until every unknown is resolved. Do not stop at the first coherent story, and do not fill a gap with an assumption to finish the report.
- **Open questions get asked, not guessed.** Anything unresolvable from the code becomes a numbered question, answered before implementation starts.
- **Adversarial verification is mandatory.** Re-read the claims that would most distort the plan if wrong, including claims from `Handoff.md`, memories, and subagent findings.
- **Output format** follows [.claude/rules/plan-output.md](../../rules/plan-output.md).

## When to use this

- Porting a change from `../FlareSolverr` (the Chrome engine and `/v1` origin) or `../Byparr` (the stealth stack origin).
- Touching the stealth engine, the async runtime, sessions, or the reaper: everything there runs on one shared background event loop and a mistake surfaces as an unrelated failure later.
- Changing the controller's engine selection, fallback, or per-host memory.
- Changing anything that reaches the `/v1` response shape, which must stay byte-compatible.
- Acting on a claim from memory or `Handoff.md` that names a specific file, function, or flag.

Do NOT use it for one-line tweaks, comment edits, or work inside a file you just wrote this session. If invoked for trivial work, push back once: the skill costs tokens.

## Step 1: Parse and scope

Echo the task in one sentence so the owner can correct a misreading before you spend agent tokens. If the task is vague ("fix sessions", "make stealth better"), use `AskUserQuestion` to narrow: which engine, which surface (`/v1`, passthrough, both), port from upstream or net-new, and whether a `Handoff.md` or recent commit range frames it. A vague scout report is noise.

## Step 2: Cheap reads before delegating

On the main thread, so the subagents get briefed properly:

1. `Handoff.md` if present (gitignored, so it may be absent). Note what shipped, what was deferred, and the dead ends recorded under "What Failed". **The most common audit failure is re-proposing something already recorded as a dead end.**
2. `git log --oneline -20` for the recent shape of work.
3. `docs/dev/upstream-sync.md` when the task involves either upstream: it records what has already been taken and what is deliberately different.
4. `CLAUDE.md` for the architecture constraints, and the relevant file in `.claude/rules/`.

Write down for Step 3: the scoped task, the recent commit shape in one line, whether the area is Chrome-engine or stealth-engine (or the controller above both), and the deferred and dead-end lists verbatim.

## Step 3: Identify investigation areas

Brief one `Agent` with `subagent_type: Explore` per area that applies, all in a single message so they run in parallel.

| Area | When relevant | What to brief |
|---|---|---|
| Current Solverr code | Always | Target files, callers, callees, the controller path that reaches them, existing tests. |
| Upstream equivalent | Ports, drift checks | `../FlareSolverr` for the Chrome engine, `/v1`, sessions; `../Byparr` for the stealth stack. Hand over the matching file paths and `git -C <ref> log --oneline -15 -- <path>`. Ask what differs and why. |
| Engine and runtime constraints | Anything touching solving | `src/async_runtime.py` (one shared loop), `src/engines/stealth_engine.py` (per-context lock, throwaway click page), `src/session_reaper.py`. Ask what runs on which thread and what is serialized. |
| Contract surface | Anything reaching a response | `src/dtos.py`, `_to_challenge_resolution` in `src/flaresolverr_service.py`, `utils.object_to_dict`. Ask what an unset field serializes to. |
| Existing helpers (DRY) | New helper tempting | `src/detection.py`, `src/config.py`, `src/postform.py`, `src/utils.py`. Search before letting the plan invent one. |
| Test coverage | Behavior changes | The browser-free tests (`src/test_*.py`) versus the browser suite (`src/tests.py`). Which one can actually cover the change. |

Brief each agent as a colleague who has not seen this conversation: state the goal, hand over exact paths and git commands, demand `file:line` citations, cap at ~500 words, and ask for a "things I'm uncertain about" section.

## Step 4: Adversarially verify

Explore agents locate and summarize; they do not verify. Re-read the current code yourself for the claims that would most distort the plan. Typical kills:

- A claim that something "doesn't exist" when it moved or was renamed.
- A flagged problem the surrounding code already handles, or that cannot be reached.
- A finding that is on the deferred or dead-end list from Step 2.
- An upstream difference that is deliberate, recorded in `docs/dev/upstream-sync.md`, and not drift at all.

Treat claims from `Handoff.md` and memories exactly like subagent claims. Label each surviving finding **verified** (re-read) or **reported** (cited, not re-read); anything load-bearing must be verified.

## Step 5: Synthesize the report and the plan

Into the conversation, not a file unless asked, in the structure from [.claude/rules/plan-output.md](../../rules/plan-output.md): headline, findings graded High / Medium / Low, stale docs, the plan, open questions.

Additions specific to this skill:

- **A claim without a `file:line` from code actually read** goes in Open questions, never in Findings.
- **A source contradiction is itself a finding.** When memory, `Handoff.md`, or a doc disagrees with current code, trust the code and record it under Stale docs.
- **The plan names the helper it reuses**, citing it. A step that invents a utility the repo already has is a failed scout.
- **Say whether the change is verifiable without a browser.** If it can only be proven against a live challenge, the plan's last step is `/live-check`, not "run the tests".

## Step 6: Hand off

End with one of: **"Ready to implement."**, **"Open questions block implementation."** (list the blocking ones), or **"Investigation incomplete."** (name what is unchecked). Then stop. Do not start editing and do not call `EnterPlanMode`.

## Rules

- Never edits files, never writes code, never calls `EnterPlanMode`.
- Every claim cites `file:line` from current code. Memory and `Handoff.md` claims are hypotheses until cited.
- Never fill an unresolved gap with an assumption. Investigate it or surface it.
- Cap each subagent at ~500 words; the report follows the ~1500 word cap in `plan-output.md`. A bigger task needs decomposition, not a longer report.
- No em dashes. Commas, parentheses, periods, colons.
- No assumptions about library behavior. If the plan leans on what Playwright, Selenium, or playwright-captcha does, cite the installed source under `.venv/Lib/site-packages/`, not documentation from memory.
- If an Explore agent comes back vague, the brief was too loose. Re-spawn with a sharper scope before synthesizing.
