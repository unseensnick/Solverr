---
name: code-research
description: Fan-out research over Solverr to answer a broad question that spans many files, such as where the test gaps are, how a request flows end to end through the controller, spine and engines, where untrusted input reaches a browser, or what one surface still duplicates between the engines. Parallel read-only agents gather file:line-cited findings, the high-stakes claims are adversarially verified against current code, and the result is one prioritized report. Use for questions across many files and modules. Not for a one-file lookup, and not for pre-planning one concrete task, which is /scout. Never edits files.
argument-hint: "<research question> (e.g. 'where are the browser-free test gaps', 'how does a request reach a clearing core', 'what does each engine still do on its own')"
disable-model-invocation: false
allowed-tools:
  - Bash(git *)
  - Bash(uv run *)
  - Read
  - Glob
  - Grep
  - Agent
---

Answer the research question in `$ARGUMENTS` by fanning out read-only exploration, verifying what comes back against current code, and writing one report cited by `file:line`. **This skill never edits files and never writes code**, as a rule it follows: acting on the report (tests, fixes, refactors) is a separate step the owner approves.

## When to use this

- Broad questions over many files or a whole subsystem: "where are the browser-free test gaps", "how does a `/v1` request travel from the controller through the spine to a clearing core", "where does request input reach a browser", "what is still written once per engine".
- Audits where you want coverage and confidence together, not a quick pointer.

Not for a single fact or a one-file lookup (read it), and not for pre-planning one task such as a port or a fix (`/scout` is lighter and task-scoped). If invoked for something trivial, push back once and name the lighter tool.

## Standing defaults (no need to ask for these)

- **Depth.** Open the files and verify each claim against current code. Never pattern matching, never inference from a symbol name. A plausible mechanism that was not read is not a finding.
- **Completeness.** Keep researching until every unknown is resolved, or surfaced as an open question. Do not smooth a gap over to finish the report.
- **Adversarial verification is mandatory**, and it covers claims from `Handoff.md`, memories, docs, and subagents alike.
- **Output format** follows [.claude/rules/plan-output.md](../../rules/plan-output.md), including its word cap.

## Step 1: Scope and clarify

Echo the question in one sentence so the owner can correct a misread before agents spend tokens. If it is vague (which surface, which engine, `/v1` or the passthrough, what counts as a gap), use `AskUserQuestion` to narrow it. Decide the angle of decomposition (by surface, by request flow, by concern) and the inclusion bar (what is a finding and what is noise).

## Step 2: Map the terrain on the main thread

Cheap reads first, so the agents are briefed well and do not rediscover the obvious:

1. **`.claude/rules/engine-layer.md`, its seam-depth table.** It says which surfaces are taken over (the spine: `src/assembly.py`, `src/pipeline.py`, `src/budget.py`, `src/sessions.py`, `src/config.py`, `src/dtos.py`) and which stay per engine (each clearing core). The two fail differently: a taken-over surface drops upstream behaviour, a mechanism-depth surface restates one rule at two sites. That decides what the agents look for.
2. **`docs/dev/upstream-sync.md`.** Its "Deliberately different" list and recorded "not applicable" verdicts are not findings unless their reasoning no longer holds.
3. **`CLAUDE.md`** "Architecture (non-obvious)" and "Key decisions (WHY)". Code that looks wrong here usually encodes a measured constraint recorded there.
4. **`Handoff.md`** if present: deferred work, the parked list, and "What failed". The most common research failure is flagging deferred work as missing.
5. `git log --oneline -20` for the recent shape, and for test questions the existing inventory (`src/test_*.py`, with `src/test_engine_conformance.py` as the both-engines rung).

Write down, verbatim, the already-covered and out-of-scope lists to hand to every agent.

## Step 3: Fan out parallel explorers

Split the question into 3 to 6 independent areas and spawn one `Agent` (`subagent_type: Explore`) per area, all in one message. Brief each as a colleague who has not seen this conversation:

- The goal and the inclusion bar.
- Exact paths, and the already-covered and out-of-scope lists.
- **Search recursively from `src/`**, never `src/*.py`: a top-level glob skips `src/engines/` and `src/bottle_plugins/` and reports a confident zero.
- A `file:line` for every concrete claim, a High / Medium / Low rating, and one line on why each finding matters.
- About 500 to 700 words, ending with an "uncertain, would need to confirm" section.

The sibling clones `../FlareSolverr` and `../Byparr` are read-only reference. Hand them over when the question has an upstream side, and never check out, pull, or edit anything there.

## Step 4: Adversarially verify

Explore agents locate and summarize; they do not verify. Re-read current code yourself for the highest-stakes, most surprising, or most bug-like claims. Typical kills:

- "X is untested" when a test exists; "X does not exist" when it moved or was renamed.
- A flagged bug the surrounding code already handles, or that cannot be reached.
- A difference from upstream that the ledger records as deliberate.
- A finding on the deferred list from Step 2.
- Misjudged testability: "add a unit test" for logic that only a live challenge can exercise.

**Library behaviour is cited from the installed source**, under `.venv/Lib/site-packages/`, never from memory of the docs. Check the package's `.dist-info` version against `requirements.txt` first, since the local `.venv` can lag the pin.

Label each surviving finding **verified** (you re-read it) or **reported** (agent-cited, not re-read). Anything that would change the conclusion if wrong must be verified. On an exhaustive question, verification can fan out too, one skeptic per top finding, each told to refute it.

## Step 5: Synthesize the report

One report into the conversation, not a file unless asked, in the structure from [plan-output.md](../../rules/plan-output.md): headline, findings graded High / Medium / Low, stale docs, the plan if the research feeds implementation, open questions.

- **Dedupe across agents.** Two agents finding the same thing is one finding.
- **Deferred work is not a defect.** Say so once and move on.
- **Say how each finding can be proven**: the browser-free suite, or only `/live-check`. A claim about whether a page still clears cannot come from the unit tests.
- **A rule that must hold for both engines** is reported with both engines' sites, since `engine-layer.md` fixes it for both in one commit.

## Step 6: Hand off

End with **"Ready to act"** and the recommended first batch, or **"Open questions block this."** Then stop. Do not start writing tests, fixes, or refactors.

## Scale

Verification is never the knob. Breadth is: a focused question gets one fan-out, "be exhaustive" gets further rounds until one surfaces nothing new, plus a completeness pass asking which area or angle was not covered.

## Rules

- Read-only. Never edits, never writes code, never starts the fix.
- Every concrete claim cites `file:line` from current code. Memory, `Handoff.md`, doc and agent claims are hypotheses until cited.
- Surface stale memories and docs found along the way instead of acting on them.
- Never fill an unresolved gap with an assumption. Research it or surface it as an open question.
- No interim narration: one sentence when the fan-out starts, then nothing until the report.
- No em dashes. Commas, parentheses, periods, colons.
