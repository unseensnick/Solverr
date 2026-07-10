---
alwaysApply: true
---

# Code Quality

## Principles

- **DRY**: before adding a helper, search for an existing equivalent (`postform.py`, `detection.py`, `config.py`).
- **YAGNI**: add only what the task needs. No speculative parameters or abstractions for hypothetical callers.
- **KISS**: simplest correct solution. Justify complexity with a concrete requirement, not elegance.
- **Minimal blast radius**: a fix changes only what's broken; a feature adds only what's asked. Leave working code untouched.
- **No standalone refactor sprints**: refactor alongside the change that motivates it, never as a separate pass unless asked.

## Anti-defaults (counter common Claude tendencies)

- No premature abstractions. Three similar lines beat a helper used once.
- Don't add features or refactor adjacent code beyond what was asked.
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
