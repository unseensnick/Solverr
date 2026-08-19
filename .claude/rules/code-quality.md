---
alwaysApply: true
---

# Code Quality

## Principles

- **DRY**: before adding a helper, search for an existing equivalent (`postform.py`, `detection.py`, `config.py`).
- **YAGNI**: add only what the task needs. No speculative parameters or abstractions for hypothetical callers.
- **KISS**: simplest correct solution. Justify complexity with a concrete requirement, not elegance.
- **Fix the defect, not the instance that reproduced.** A bug present in five places is one bug with five sites. Fix all five, or name the ones you left and why. Search for the sibling instances before calling a fix done; the same mistake usually appears in both engines, since they were written against each other.
- **Minimal blast radius is measured against the defect, not against the diff.** Leave genuinely unrelated code alone. A small diff is not the goal: when the correct fix needs a helper extracted, a signature changed, or a call site moved, do that instead of threading a workaround through the shape that is already there.
- **Refactor when the fix needs it**, in the same change, with the reason in the commit body. Still no standalone refactor sprints, and still nothing adjacent riding along uninvited.
- **Prefer the proper fix over the patch.** If the patch is genuinely the right call (a risky area, a release in flight), say so explicitly and record what the proper fix would be. An unstated tradeoff reads as an oversight to whoever finds it next.

## Anti-defaults (counter common Claude tendencies)

- No premature abstractions. Three similar lines beat a helper used once.
- Don't add features beyond what was asked. Refactoring is the different case: do it when the correct fix requires it, not as a separate pass and not as adjacent cleanup.
- Don't stop at the first site that made the bug visible. "The reported case now passes" is not the same as "the bug is fixed".
- No dead code or commented-out blocks. Git has history.
- WHY comments, never WHAT. If code needs a "what" comment, rename instead. Docstrings at module/engine boundaries, not every internal function.
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
