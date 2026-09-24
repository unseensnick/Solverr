---
alwaysApply: true
---

# Fork workflow

Solverr is a standalone fork of FlareSolverr. It follows **its own SemVer** (starting at `1.0.0`), not FlareSolverr's version line. `package.json` holds the version; the `.github` workflows tag and release from it.

## CHANGELOG (`CHANGELOG.md`)

After a code change with any user-facing effect, add a bullet under `## [Unreleased]`:

- Categories: `Additions`, `Changes`, `Fixes`, `Other`. Create `## [Unreleased]` at the top if missing.
- **Benefit-first.** Each entry under Additions/Changes/Fixes leads with a self-contained bold headline (a complete user-facing phrase ending in `.`/`!`/`?`), optionally one short sentence after. Lead with the effect, never the implementation. Example: `**Sites that only Camoufox can clear now fall back automatically.**`
- **The bold headline is the entire release note.** `release.yml` reduces each entry with `s/^- \*\*([^*]+)\*\*.*/- \1/`, so everything after the closing `**` is discarded and never reaches the GitHub Release. Anything a deployer must act on (a new env var, a default, a changed limit) goes **inside** the bold text or it is invisible to everyone who reads the release instead of the file. Check by running that `sed` over the entry before committing. `**The passthrough cache can no longer grow without limit.**` fails this: the reader never learns the knob exists.
- **Keep under-the-hood detail out** (class names, mechanisms, refactor rationale): that belongs in the commit body.
- **The CHANGELOG is for people who run Solverr, not people who work on it.** An entry earns its place only when a deployer or an API client could notice the change: behavior, config, the response shape, the image. A change that ships in the image without a visible effect (a dependency bump, a base-image change, a refactor) gets a brief plain line under `Other`, no bold headline.
- **Contributor tooling gets no entry at all.** Agent config under `.claude/`, git hooks, CI workflows, tests, and repo docs never reach a user, so they stay in the commit history where they belong.
- **Don't churn.** If you're iterating on something already in `[Unreleased]`, edit the existing bullet. Don't accumulate "fix X in feature Y" when Y was added in the same block.
- Update the user docs in the same change when behavior or config changes: `README.md` for the quick start, and the matching guide in `docs/` (a new setting gets a row in `docs/configuration.md`). Describe current behavior, not the journey, and write for someone running Solverr for the first time.

## Cutting a release (user-initiated)

1. Rename `## [Unreleased]` to `## [<version>]`, and collapse any entries in it that state the same fact: a fix to something added in the same release folds into that addition's entry.
2. Add a fresh empty `## [Unreleased]` above it.
3. Bump `version` in `package.json` and the version in the response example in `docs/api.md` to `<version>`, and commit.
4. Tag and push: `git tag v<version> && git push origin v<version>`.

The tag triggers `release-docker.yml` (builds + pushes the ghcr image) and `release.yml` (creates the GitHub Release from the `[<version>]` section). `release.yml` can also be run manually from the Actions tab (workflow_dispatch) with the version and an optional note. Don't bump the version mid-cycle; only at release-cut.

## Commits & PRs

Create a commit after a change (do not push unless asked).

- Subject `type(scope): summary`: a real conventional type (`feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`), imperative, lower-case, no trailing period, `<=72` chars. Scope optional (`chrome`, `stealth`, `sessions`, `docker`).
- Non-trivial commits get a body: lead with 1-2 plain-language sentences (what changed and why it matters), then benefit-first bullets. A trivial commit is just the subject.
- No em dashes. No AI watermarks (no `Co-Authored-By: Claude`, no generated-by footer, no robot emoji). A `Co-authored-by` trailer for a person is credit and passes the hook; one naming an AI tool is a watermark and is rejected.
- **Never a bare `#N`** in the subject or body: it silently links to an issue in this repo. Use the explicit `owner/repo#N` form (`FlareSolverr/FlareSolverr#1626`, `ThePhaseless/Byparr#377`).

**Merging a pull request.** A person merges every pull request, with a merge commit, never squash or rebase. A squash subject ends in ` (#N)`, which the bare-`#N` check rejects on `main`, and a merge commit keeps an outside contributor's commits under their name. The standard for contributors is written out in `CONTRIBUTING.md` and the pull request template; keep both in step with this file. When a contributor's commit message breaks the standard, reword it with `git commit --amend` (which keeps them as the author), push it to their branch with `--force-with-lease`, then merge. Never recommit their change under your own name.

### Pre-commit checklist

Run these against the message before committing. The first four are also enforced by `.githooks/commit-msg`; the rest are on you.

1. Subject is `type(scope): summary`, imperative, lower-case, no trailing period, `<=72` chars.
2. No em dash anywhere in the message.
3. No AI watermark.
4. No bare `#N`.
5. No site names or scraping vocabulary (see "Public-facing naming" below).
6. Non-trivial change has a body that leads with plain language, not implementation.

## Public-facing naming

**Keep the names of the sites Solverr is pointed at out of every public surface**: commit messages, branch names, `README.md`, the user guides in `docs/`, `CLAUDE.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, the pull request template, release notes, and the repo description and topics. Solverr is a general-purpose bypass proxy; naming targets makes it read as tooling for one specific site.

Use generic wording instead: "a Cloudflare-gated site", "an indexer", "the default mirror", "example-site.tld" in docs and examples. Site names are fine in local test scratch files, in chat, and in a private indexer definition that lives outside this repo.

Inherited exception: `src/tests_sites.py` and `src/tests.py` carry a site list from upstream FlareSolverr, byte-identical to theirs. Left as-is so the files stay mergeable; do not add to them.

## Git hooks

`.githooks/` holds the tracked copies. Activate them on a clone with:

```
git config core.hooksPath .githooks
```

- `commit-msg` rejects: a subject that is not `type(scope): summary`; a subject over 72 characters; an em dash anywhere; an AI watermark (an AI `Co-authored-by` trailer, "Generated with", the robot emoji); a bare `#N`; a domain-shaped site name or scraping vocabulary. Merge, revert, fixup and squash commits pass untouched.
- `pre-commit` lints the lines a commit adds to `CHANGELOG.md`, `README.md`, `CONTRIBUTING.md`, `CLAUDE.md` and the user guides (`docs/*.md`, not `docs/dev/`) for the naming rule and em dashes, and every `[Unreleased]` entry outside `Other` for a bold headline ending in `.`, `!` or `?`.
- `.githooks/tests/run.sh` proves each rule above still rejects a real violation and passes a clean case. Run it after touching either hook.

CI runs all of it: the Standards workflow runs the hook self-test, then `commit-msg` on every non-merge commit and `pre-commit` over the pushed range, and the Tests workflow runs the browser-free suite on every pull request. Never bypass with `--no-verify`. If a hook fires on something legitimate, fix the hook and its self-test in the same change.

## Approach

- Investigate before planning when context is thin: read the code, trace the pattern, cite `file:line`, then plan.
- Plan non-trivial work before acting; get approval before large changes.
- **Name the check before making the change.** Which of these would catch you being wrong: the browser-free suite, `/live-check`, or the indexer chain? Say which, then run it. A change to detection or the engines that only compiles has not been verified at all.
- **A check that cannot fail is not a check.** The browser-free tests cannot tell you whether a page still clears, and one passing live request is not evidence: Cloudflare's behavior varies by IP reputation and by hour, so reliability claims need a tally and mechanism claims need an A/B in the same window.
- State assumptions rather than picking one silently, especially about which engine, which surface (`/v1` or the passthrough), and whether a change is a port or net-new. `/scout` exists to stop a plan resting on a misread.
- Stop and replan when blocked. Never circumvent (deleting a test, silencing a linter, skipping a hook, forcing past a denial).

## Fork compatibility (don't break)

- The `"FlareSolverr is ready!"` banner must stay: clients detect session support by it.
- Keep the `/v1` request/response shape byte-compatible; add optional fields only.
