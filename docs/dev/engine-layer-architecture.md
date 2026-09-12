# Engine layer architecture

## Goal

One Solverr-owned spine over two upstream-derived clearing cores, so a change to observable
behaviour is written once and reaches both engines, and neither upstream stops being mergeable. The
end state: the engines own only what a browser stack can genuinely do differently, and every rule a
client can observe lives at one site.

## Why

The diagnosis this program was written from, as it stood on 2026-08-25. Two problems with one root
cause: the shared seam was drawn too low (only `detection.py`, `geo.py` and `postform.py` were
shared) and nothing enforced parity above it.

- **Duplication is the daily cost, and it produces bugs.** `disableMedia`, cookie-set-and-reload,
  `waitInSeconds`, `returnOnlyCookies`, `returnScreenshot`, access-denied detection and the whole
  result assembly were each implemented once per engine. On 2026-08-25 the same cookie-ordering
  defect was found in both, at `chrome_engine.py:211` and `stealth_engine.py:517`, because the rule
  was written twice. `CLAUDE.md` already half-acknowledged this by telling reviewers to expect it
  ("the same mistake usually appears in both engines, since they were written against each other"),
  which is a reviewer instruction where a structural answer belongs.
- **Divergence had nowhere to be declared.** `tabs_till_verify` was a silent no-op on the stealth
  engine, documented only in the README, until `Engine.presses_checkbox_unaided` gave it a slot. `solution.headers` was `{}` on both because filling it for
  one would create an asymmetry, so a capability both engines could answer stayed unbuilt until
  step 6 shipped it behind `RESPONSE_HEADERS`.
- **The request boundary was untyped.** `V1RequestBase.__init__` was `self.__dict__.update(_dict)`,
  so the class annotations were documentation. Measured 2026-08-25: a string in a boolean parameter
  silently inverts it, a string proxy silently disables proxying, and four parameters fail deep
  inside an engine rather than at the boundary.

## Approach

Four layers, one hard ownership rule.

- **Both clearing cores stay upstream-derived and live.** Measured 2026-08-25: Chrome's `_evil_logic`
  is 166 lines against FlareSolverr's 157, about 30 diverged after normalising naming, so it still
  syncs mechanically. The stealth core shares Byparr's algorithm and widget constants by name, and
  the ledger records four separate ports into it. Taking either one over would start paying a sync
  tax nobody is paying, against two upstreams rather than one.
- **Adapters are the only seam.** Each engine exposes primitives; a renamed upstream field breaks one
  adapter at import time rather than during a live check.
- **The spine is Solverr-owned** and holds everything derived from a request option, plus the order
  of operations and the result assembly, written once and driving both engines.

**The shared spine** (both engines): request parsing and validation, media blocking, the
cookie-set-and-reload rule, access-denied raising, challenge detection, the shared `maxTimeout`
budget and its even split, engine selection and fallback, per-host memory, `waitInSeconds`
placement, `returnOnlyCookies` gating, screenshot gating, cookie dialect translation, and the whole
result assembly.

**Capability slots** are typed, so the shared model never rots into nullable soup.

| Capability | Chrome | Stealth | Today | Target |
|---|---|---|---|---|
| `TAB_DRIVEN_TURNSTILE` | yes | no, clicks by coordinate | **declared** as `presses_checkbox_unaided`: the count is used, or reported as unneeded | as today |
| `RAW_DOCUMENT_BODY` | no | yes | README tells the client to pin stealth | routed automatically |
| `RESPONSE_HEADERS` | CDP performance log | main-frame response | **shipped**, behind one setting |
| `PAID_ESCALATION` | no | yes | dormant, undeclared | declared |

Solverr can go further than a capability flag here, because it already chooses the engine: a
capability need becomes routing input rather than a documentation note.

**Reconciliations the seam must handle.** Cookies are Selenium's dialect on one side and
Playwright's on the other, already translated by `_to_client_cookies`; the stealth engine tracks a
main-frame response for PDF detection and Chrome has no equivalent object; sessions are two
independent pools that can hold the same id, which `_cmd_sessions_list` deduplicates and which the
controller resolves by per-host memory rather than pool order;
and Chrome's clearing loop is bounded by `func_timeout` from outside while the stealth loop carries
its own deadline.

## The seam

```python
class Engine(Protocol):
    name: str
    capabilities: frozenset[Capability]

    def start(self, ref: SessionRef | None, proxy: Proxy | None) -> Handle
    def configure(self, h: Handle, *, block_media: bool) -> None
    def navigate(self, h: Handle, url: str, method: Method, post_data: str | None) -> None
    def set_cookies(self, h: Handle, cookies: list[Cookie]) -> None
    def read(self, h: Handle) -> PageView
    def clear(self, h: Handle, kind: ChallengeKind, deadline: float) -> bool
    def screenshot(self, h: Handle) -> bytes
    def document(self, h: Handle) -> Document | None
```

`clear` is the upstream-derived core and is the one method the spine never reimplements.

## Key files

Current homes and where they land.

- Spine: `src/assembly.py` (the read order and the field rules), `src/pipeline.py` (the page
  verdict and the navigate-cookies-reload order) and `src/budget.py` (the solve deadline and what
  is left of an engine's share). These land in a `core/` package once there is enough to justify
  the move; the reshape is behaviour-free and deliberately not bundled with a behaviour change.
- Boundary: new `api/` holding the typed request model that replaces `dtos.py`'s
  `__dict__.update`, and the response serialization.
- Engines: `engines/chrome/` and `engines/stealth/`, each an adapter plus its upstream-derived core.
  `engines/base.py` becomes the protocol plus the capability enum.
- Sessions: `sessions.py` and the stealth engine's own pool become one registry keyed by
  `SessionRef`.
- Unchanged: `geo.py`, `postform.py`, `passthrough.py`, `utils.py` and the vendored driver.
- Enforcement: `src/test_engine_conformance.py` over both engines, driven by `src/engine_fakes.py`,
  asserting the rules the spine cannot reach by construction. A rule that moves into the spine in a
  later step stays asserted here, which is what makes the move checkable.

## Sequencing

One surface at a time, extracting the shared behaviour before folding the second implementation
onto it, so no step gambles a surface on one adapter. Each step is independently shippable and
live-checked.

1. **Request boundary.** Done 2026-08-25. `validate_request_types` in `dtos.py` derives the checks
   from `V1RequestBase.__annotations__`, so a parameter added to the class is checked without
   anyone remembering to add it anywhere, and runs once in `_controller_v1_handler` beside
   `_validate_max_timeout`. Two documented exceptions: `maxTimeout` stays coerced rather than
   type-checked, and an unknown parameter is logged and kept rather than refused, because the
   contract takes additive optional fields. The validation lives next to the annotations rather
   than in a new `api/` package: moving files and changing behaviour in one diff would make both
   harder to review, so the package reshape is a later, behaviour-free move.
2. **Conformance suite against today's engines.** Done 2026-08-25. `test_engine_conformance.py`
   runs its assertions over both engines through `engine_fakes.py`, which drives each one
   browser-free from a single neutral `World` and renders it in that browser's own dialect. The
   Chrome-only cookie tests it supersedes were deleted rather than left beside it, so the rules it
   covers are pinned once. Verified by mutation on each engine separately: moving either engine's
   cookie read back before the wait turns the suite red and names that engine.
3. **Result assembly.** Done 2026-08-25, as `assembly.py`. Not the pure function this document
   first proposed: one engine is synchronous on the request thread and the other asynchronous on
   the shared event loop, and one function body cannot be both. The order is a generator that says
   what to read and in what order, and each engine supplies only how to read it, a flat mapping
   with no rules in it. Chosen over the alternatives because it needs no change to either
   concurrency model: running the kernel under `asyncio.run` inside the Chrome worker thread would
   have put an event loop in a path `func_timeout` kills asynchronously, and moving the stealth
   tail onto the request thread would have meant holding a context lock across a thread hop.
   Proved by mutation: reordering the reads in that one file now turns the conformance suite red
   for **both** engines, where the same defect previously took two separate edits to produce.
4. **Solve orchestration.** Detection done 2026-08-25, as `pipeline.py`; the rest is not. Both
   engines scanned the same four lists in the same order and reached the same verdict, in code
   written twice, so the lists were shared and the rule that reads them was not. The rule is now a
   generator like `assembly.py`, for the same sync/async reason. The one difference between the
   engines became a parameter rather than a fork: `turnstile_is_a_challenge` is false for Chrome,
   which cannot reach a checkbox without a `tabs_till_verify` count from the caller, so treating a
   bare widget as a challenge would spend the whole budget in a wait loop it cannot win. That is a
   capability boundary and it is now asserted rather than implicit.

   Characterisation came first, as step 2's discipline requires: nothing covered the engines' own
   detection scan, so the conformance suite gained six cases over both engines before any code
   moved. That is what surfaced the turnstile asymmetry as a fact rather than a surprise.

   Chrome's cost is unchanged at 15 selector round trips on a clean page, because the kernel only
   looks for a widget when the engine could act on one.

   Finished the same day. Two of the three remaining items turned out to need nothing: the even
   split and the fallback were already single-sourced in `_resolve_challenge`, so this document
   listed them as work when they were already done. What was genuinely duplicated was the
   navigate-then-set-cookies-then-renavigate order, now `pipeline.approach`, and the solve deadline
   `max(1.0, timeout - 3)`, which appeared in both engines with the constant named on one side and
   bare on the other, now `budget.solve_deadline`.

   **What the engines still sequence themselves**, deliberately: between `approach` and `verdict`
   each one does its own interleaved work, Chrome resolving a `tabs_till_verify` token and holding
   the `<html>` element for its staleness wait, the stealth engine dumping HTML and settling on
   networkidle before re-detecting. Those are engine mechanisms rather than a shared order, so
   forcing them into one kernel would have meant a kernel shaped around both engines' internals.
5. **Sessions.** Done 2026-08-25, with one part of the plan dropped on measurement. The two
   registries were structural twins, nine methods each with the same rules and different payloads,
   and both carried the same expiry race: the busy check and the replacement were separate lock
   acquisitions, so a request arriving in between took a session whose browser was then closed. The
   stealth side was worse, reading `ctx.lock.locked()` with no registry lock held at all. Both now
   use one `SessionStore`, which takes the decision and the removal under a single acquisition and
   only replaces a session this request is the sole holder of. The stealth engine's busy signal
   moved from its asyncio lock to the same in-use mark the Chrome engine takes, which is what makes
   the reaper and the cap agree across engines; its lock stays, for serialising requests on one
   context.

   **`SessionRef` was dropped.** It was proposed by analogy to the reference project's `EntryId`,
   and the analogy does not hold. `EntryId` flows through a UI that project owns end to end, while a
   Solverr session id arrives as a bare string in the `/v1` contract, which this fork cannot change.
   Resolving which engine holds an id is the controller's actual job, not a mistake a type would
   prevent, and the case that motivated it, the same id living in both pools after a fallback, is
   legitimate and no identity type fixes it.

   A second window was closed straight after, found while writing up the first: a session was
   handed back from `create` before being marked in use, so for that instant it was findable and
   idle, and the reaper or the cap could close its browser before the caller claimed it. `create`
   now claims under the same acquisition that finds or stores the session, so there is no unmarked
   moment at all.

   The race tests are worth knowing about: rather than threads and timing, `_WindowLock` runs
   another operation at each lock release in turn, which is deterministic. The expiry race fails at
   window 2 on the old code, the release right after the busy check; the handout race fails at
   window 1, the release right after `create` finds the session.
6. **Config, then `RESPONSE_HEADERS`.** Done 2026-08-25.

   The config half was smaller than planned and larger than expected elsewhere. Moving
   `utils.get_config_*` into `config.py` was **declined**: those three readers are upstream's, in a
   file the ledger tracks as near-identical, and no rule is duplicated by their living there, so the
   move would buy tidiness and cost mergeability. What was genuinely duplicated is the environment
   proxy, read in three places with the copies disagreeing. The startup timezone lookup built
   `{"url": ...}` without the credentials, and `geo` caches per proxy *server* rather than per
   credential set, so with an authenticated proxy that lookup was refused and the fallback zone and
   language were then cached for every request that followed. One `config.env_proxy()` now serves
   both Solverr-owned call sites; upstream's own copy in `flaresolverr.py` is left alone.

   `RESPONSE_HEADERS` is the first feature written once under the seam, which is what it was
   sequenced last to demonstrate. `assembly.py` gained one read, and each engine answers it from a
   completely different place: the Chrome engine drains the browser's CDP performance log and takes
   the last main-document response, the stealth engine reads the navigation response it already
   tracked for PDF detection. One setting gates both, so `solution.headers` never depends on which
   engine solved. Off by default for two reasons kept in the config docstring: the field has been an
   empty map since the fork, and the Chrome half changes what the browser does on every request in a
   way no fingerprinting check has measured yet.

Steps 1 and 3 alone remove most of the duplication class.

## Status

Designed 2026-08-25, step 1 shipped the same day. Grounded by a whole-codebase audit and by divergence
measurements against both upstreams. This is a deliberate refactor sprint, exempted from the
no-standalone-refactor line in [code-quality.md](../../.claude/rules/code-quality.md) by the owner,
because the per-engine duplication is not sustainable and has now produced a defect in both engines
at once.

The rules that bind live in [.claude/rules/engine-layer.md](../../.claude/rules/engine-layer.md),
which loads every session. The depth table lives there and is deliberately not copied here, so the
copy cannot drift from the law.

## Decisions and tradeoffs

- **Neither clearing core is taken over.** The measurements above are the whole case. The decline
  expires if either diff grows to where the sync tax is already paid, which is what happened to the
  reference project's library surface before it ruled the other way.
- **The line runs inside each engine, not between them.** An earlier draft framed this as one
  upstream engine plus one home-grown engine and was wrong: the stealth engine is Byparr-derived,
  so both halves have sync obligations. What is Solverr's own is the wrapper around each core, and
  that is exactly what is duplicated. This framing is what makes the seam obvious.
- **No exemption is needed or granted for the duplication.** Two upstream-derived cores are two
  mechanisms. Two Solverr-owned wrappers twinning each other are ordinary duplication, so they get
  judged by the code rules like any other duplicate.
- **Divergent bits are typed capability slots, never nullable fields or per-engine forks.** The
  counter-example used to be `tabs_till_verify`, a silent no-op rather than a declared incapability.
  `Engine.presses_checkbox_unaided` is that declaration, and `pipeline.verdict`'s
  `turnstile_is_a_challenge` is derived from it rather than hardcoded per engine, so the two cannot
  drift apart.
- **`RESPONSE_HEADERS` is in scope** (owner, 2026-08-25). The stealth half is close to free since the
  engine already tracks the main-frame response. The Chrome half reads the CDP performance log,
  which the vendored driver already supports through the `goog:loggingPrefs` capability; the
  `Reactor` that ships alongside it is deliberately not used, since it starts a polling thread per
  driver. Three caveats bound the work: the capability is set at driver creation so it is a config
  decision rather than a per-request one, the log must be drained per request or it grows on a
  long-lived session, and it needs a fingerprint A/B before it could ever be on by default. It
  shipped off by default for that reason.
- **Identity stays a bare id string.** A sealed `SessionRef` was proposed because the two pools can
  hold the same id, and dropped once measured: the `/v1` contract has clients send a bare id, and
  resolving which engine holds one is the controller's job. Step 5 above records the full reason.
- **The conformance suite is the pin, and the spine is the kernel.** Both rungs of the ladder are
  available here because the codebase is small enough, so an unpinned twin should not exist at all.
- **Sequencing puts characterisation second, not last.** `/live-check` is user-invoked and takes tens
  of minutes, so it cannot gate a commit. The browser-free suite is the only per-commit gate, and it
  does not currently cover the solve paths being moved.
