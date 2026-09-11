---
alwaysApply: true
---

# Code Quality

## Principles

- **DRY**: before adding a helper, search for an existing equivalent (`postform.py`, `detection.py`, `config.py`).
- **YAGNI**: add only what the task needs. No speculative parameters or abstractions for hypothetical callers.
- **KISS**: simplest correct solution. Justify complexity with a concrete requirement, not elegance.
- **Fix the defect, not the instance that reproduced.** A bug present in five places is one bug with five sites. Fix all five, or name the ones you left and why, with one exception: an engine pair is never split. A change a client can observe lands for both engines in the same commit, and the only exit is the named mechanism in [engine-layer.md](engine-layer.md). Search for the sibling instances before calling a fix done. The shared spine (`assembly.py`, `pipeline.py`, `budget.py`, `sessions.py`) now holds the rules that used to be written once per engine, so a defect in one of those is a defect for both; what remains genuinely per-engine is each clearing core.
- **Minimal blast radius is measured against the defect, not against the diff.** Leave genuinely unrelated code alone. A small diff is not the goal: when the correct fix needs a helper extracted, a signature changed, or a call site moved, do that instead of threading a workaround through the shape that is already there.
- **Refactor when the fix needs it**, in the same change, with the reason in the commit body. Still no standalone refactor sprints, and still nothing adjacent riding along uninvited. One exception comes from the engine-layer law: a parity gap you notice on an engine surface you are touching is levelled up in that change, unless the owner gates it. That never licenses cleanup on a file just because it was open.
- **One standing exemption to that ban** (owner, 2026-08-25): the engine layer program in [engine-layer.md](engine-layer.md), whose steps are refactors with no fix attached. It exists because the per-engine duplication produced the same defect in both engines at once, which a fix-shaped change cannot prevent recurring. The exemption covers only the sequenced steps recorded in [engine-layer-architecture.md](../../docs/dev/engine-layer-architecture.md); anything else is still an ordinary refactor and still needs a fix to ride with. All six steps are done, so the exemption covers nothing further: a new behaviour-free move needs the owner's approval like any other refactor.
- **Prefer the proper fix over the patch.** If the patch is genuinely the right call (a risky area, a release in flight), say so explicitly and record what the proper fix would be. An unstated tradeoff reads as an oversight to whoever finds it next.

## Anti-defaults (counter common Claude tendencies)

- No premature abstractions. Three similar lines beat a helper used once. A rule that must hold for both engines is never "used once": it has two callers by definition, so it belongs in the spine.
- Don't add features beyond what was asked. The second engine is not beyond what was asked (write-once, above). Refactoring is the different case: do it when the correct fix requires it, not as a separate pass and not as adjacent cleanup.
- Don't stop at the first site that made the bug visible. "The reported case now passes" is not the same as "the bug is fixed".
- No dead code or commented-out blocks. Git has history.
- Comments say WHY: why this approach, why not the obvious one, what breaks otherwise. A WHAT comment is allowed when the what is not visible in the code at hand: an invariant, how an upstream or library dependency behaves, what a magic value means, how this piece couples to a distant one. A comment that restates the adjacent code is dead weight; rename instead. Docstrings at module/engine boundaries, not every internal function.
- No em dashes in code, comments, or docs. Use commas, parentheses, periods, or colons.
- No AI watermarks: no "Co-Authored-By: Claude", no "Generated with Claude Code", no robot-emoji footers.

## Naming (Python)

- Modules and functions: `snake_case`. Classes: `PascalCase`. Constants: `SCREAMING_SNAKE`.
- Booleans / predicates: `is_` / `has_` / `should_` prefix. Verb-first functions (`get_webdriver`, `solve_captcha`).
- Abbreviations only when universally known (`id`, `url`, `req`, `ctx`).

## File Organization

- Imports grouped: standard library, third-party, local. Blank line between groups (matches the existing `src/` files).
- Keep the `flat` import style the app uses (`import utils`, `from engines.base import ...`); the app runs with `src/` on `sys.path`.
- Function order: public API first, then helpers in call order.
