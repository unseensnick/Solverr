---
alwaysApply: true
---

# Fork workflow

Solverr is a standalone fork of FlareSolverr. It follows **its own SemVer** (starting at `1.0.0`), not FlareSolverr's version line. `package.json` holds the version; the `.github` workflows tag and release from it.

## CHANGELOG (`CHANGELOG.md`)

After a code change with any user-facing effect, add a bullet under `## [Unreleased]`:

- Categories: `Additions`, `Changes`, `Fixes`, `Other`. Create `## [Unreleased]` at the top if missing.
- **Benefit-first.** Each entry under Additions/Changes/Fixes leads with a self-contained bold headline (a complete user-facing phrase ending in `.`/`!`/`?`), optionally one short sentence after. Lead with the effect, never the implementation. Example: `**Sites that only Camoufox can clear now fall back automatically.**`
- **Keep under-the-hood detail out** (class names, mechanisms, refactor rationale): that belongs in the commit body. Pure-internal changes (refactors, dependency/tooling bumps, infra) get a brief plain line under `Other`, no bold headline.
- **Don't churn.** If you're iterating on something already in `[Unreleased]`, edit the existing bullet. Don't accumulate "fix X in feature Y" when Y was added in the same block.
- Update `README.md` in the same change when behavior or config changes. Describe current behavior, not the journey.

## Cutting a release (user-initiated)

1. Rename `## [Unreleased]` to `## [<version>]` with the date.
2. Add a fresh empty `## [Unreleased]` above it.
3. Bump `version` in `package.json` to `<version>`.

Pushing that to `main` lets `autotag.yml` create the `v<version>` tag, which triggers `release.yml` (GitHub release) and `release-docker.yml` (ghcr image). Don't bump the version mid-cycle; only at release-cut.

## Commits & PRs

Create a commit after a change (do not push unless asked).

- Subject `type(scope): summary`: a real conventional type (`feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`), imperative, lower-case, no trailing period, `<=72` chars. Scope optional (`chrome`, `stealth`, `sessions`, `docker`).
- Non-trivial commits get a body: lead with 1-2 plain-language sentences (what changed and why it matters), then benefit-first bullets. A trivial commit is just the subject.
- No em dashes. No AI watermarks (no `Co-Authored-By: Claude`, no generated-by footer, no robot emoji).

## Approach

- Investigate before planning when context is thin: read the code, trace the pattern, cite `file:line`, then plan.
- Plan non-trivial work before acting; get approval before large changes.
- Stop and replan when blocked. Never circumvent (deleting a test, silencing a linter, skipping a hook, forcing past a denial).

## Fork compatibility (don't break)

- The `"FlareSolverr is ready!"` banner must stay: clients detect session support by it.
- Keep the `/v1` request/response shape byte-compatible; add optional fields only.
