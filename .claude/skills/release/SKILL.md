---
name: release
description: Cut a Solverr release end to end. Decides the SemVer bump from what is in [Unreleased], runs the preflight, rewrites the CHANGELOG section, bumps package.json, commits, pushes, tags, then verifies both workflows and the published image digests. Use when the owner asks to cut, ship, or publish a version. Pushing the tag publishes the GitHub Release and the ghcr image, so it always confirms before that step.
argument-hint: "[version] (e.g. '1.3.0', omit to have the bump proposed from the changelog)"
disable-model-invocation: true
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
  - Bash(uv run *)
  - Bash(docker manifest inspect *)
---

Cut a release. The mechanical steps are in [.claude/rules/workflow.md](../../rules/workflow.md) "Cutting a release"; this skill adds the decision, the preflight, and the verification that the ritual alone does not cover.

**Tagging is publication.** The tag push triggers `release-docker.yml` and `release.yml`, which build the multi-arch image and create the public GitHub Release. Confirm with the owner before pushing, every time, even when they asked for a release.

## Step 1: Decide the version

If `$ARGUMENTS` names a version, use it, but still state whether it matches what the changelog implies and say so if it does not.

Otherwise read the `[Unreleased]` block and propose:

- Anything under **Additions**, or a new field on the `/v1` response, is a **minor**.
- Only **Fixes** and **Other** is a **patch**.
- A **major** needs a real break: a field renamed, retyped, or removed from `/v1`, or the banner changed. Solverr's whole compatibility promise is the FlareSolverr contract, so a change that moves *toward* that contract is a fix, not a break, even when it changes an existing field's shape. Say that reasoning out loud rather than assuming it.

Flag anything in the block that could surprise an existing client, and make sure that bullet carries the actionable detail (the field name, the setting) rather than only the effect. `release.yml` extracts just the bold headline into the release body, so detail buried in a follow-up sentence never reaches the notes.

## Step 2: Preflight

Stop and report rather than continuing if any of these fail:

1. `git status` is clean apart from what you are about to commit, and the branch is `main`.
2. The browser-free tests pass: `PYTHONPATH=src uv run --no-project python -m unittest test_detection test_response_shape`.
3. Compile check: `uv run --no-project python -m py_compile src/*.py src/engines/*.py`.
4. `[Unreleased]` actually has entries. An empty block means there is nothing to release.
5. The version in `package.json` is the previous one, and no tag for the new version exists yet (`git tag -l v<version>`).
6. If any solving code changed since the last release, `/live-check` has been run this session. If it has not, say so and ask whether to run it before cutting.

## Step 3: Cut

1. Rename `## [Unreleased]` to `## [<version>]` and add a fresh empty `## [Unreleased]` above it.
2. Bump `version` in `package.json`.
3. Commit as `chore(release): <version>`, nothing else in that commit.

## Step 4: Push, then tag

Confirm with the owner first. Then:

1. `git push origin main`. Verify with `git ls-remote origin refs/heads/main` that the remote head is your commit. The tag must point at commits that already exist on the remote.
2. `git tag v<version> && git push origin v<version>`, then verify with `git ls-remote --tags origin`.

If the push is not a fast-forward, stop. Do not force. Report the divergence.

## Step 5: Verify the publication

`gh` resolves the repo from git remotes, and this repo has an `upstream` remote pointing at FlareSolverr, so **always pass `-R unseensnick/Solverr` explicitly**. Without it you will read the upstream project's workflow runs and conclude, wrongly, that nothing triggered.

1. `gh run list -R unseensnick/Solverr --limit 4`: both `Release` and `Docker release` should appear on the new tag.
2. Wait out the Docker build with `gh run watch <id> -R unseensnick/Solverr --exit-status`. It is a multi-arch build and takes a while.
3. `gh release view v<version> -R unseensnick/Solverr` to check the generated body reads correctly.
4. `docker manifest inspect ghcr.io/unseensnick/solverr:<version>` and `:latest`, and confirm both resolve to the same digests for `linux/amd64` and `linux/arm64`. Matching digests are what prove `:latest` actually moved.

## Step 6: Hand back

Report the tag, the release URL, both workflow outcomes, and the digests. Then give the owner the pull command for their own deployment, and name anything still pending: a release-body edit, docs that reference the old version, or a `[Unreleased]` item that was deliberately held back.

## Rules

- Never push a tag without explicit confirmation in the same conversation.
- Never force-push, and never re-tag a published version. A mistake gets a new patch version.
- The release commit contains only the CHANGELOG rename and the version bump.
- Re-running `release.yml` by hand regenerates the notes and discards any manual edit to the release body. Say so if the body was edited.
- No em dashes. Commas, parentheses, periods, colons.
