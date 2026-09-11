---
alwaysApply: true
---

# The engine layer

Solverr serves every request through one of two engines, and both of them are derived from an
upstream it still syncs from. This file is the law. The rationale, the measurements behind each
ruling and the sequencing live in
[engine-layer-architecture.md](../../docs/dev/engine-layer-architecture.md); read that before
designing, read this before touching anything.

The goal is **parity and anti-divergence**. Collapsing two implementations into one is the
mechanism rather than the point: the point is that a change to one engine cannot silently miss the
other. Every duplicate is a cost tracked against that.

## What is upstream and what is ours

Solverr has two upstreams and owes both a mergeable diff. The ledger
([upstream-sync.md](../../docs/dev/upstream-sync.md)) tracks each with its own audited-through row.

- **The Chrome clearing core is FlareSolverr's.** Measured 2026-08-25: 166 lines against upstream's
  157, about 30 diverged after normalising naming. It still syncs mechanically.
- **The stealth clearing core is Byparr's.** It shares Byparr's algorithm and its widget constants by
  name and role (`ANCESTOR_DEPTHS`, `MIN_WIDTH`, `MIN_HEIGHT`, `MAX_HEIGHT`, `COOLDOWN`), and the
  ledger's Taken section records four separate ports into it.
- **Everything wrapped around those cores is ours.** Request-option handling, session lifecycle and
  result assembly used to be implemented once per engine, which is where the same defect kept
  landing twice. They now live once in the spine (the seam-depth table below); what stays per
  engine is each adapter, and duplication there is ordinary duplication with no exemption.

**The line runs inside each engine, not between them.** The two cores are two mechanisms, not two
implementations of one rule, so collapsing them would fork both engines from their upstream and buy
nothing.

## Ownership

- **Neither clearing core is taken over.** Both are live sync surfaces. This decline rests on the
  measurements above, so it expires if either diff grows to where the sync tax is already being paid
  anyway. Re-measure before citing it.
- **Neither engine dissolves.** Both keep a live core and lose their wrapper.
- **Adapters are the only seam.** The spine talks to each engine through an adapter, so a renamed
  upstream field breaks one file at import time instead of hiding until a live check.
- **Never reimplement a clearing core in the spine.** A step that starts reimplementing what
  `WebDriverWait` over the challenge selectors does, or what the coordinate click through the closed
  shadow root does, has gone too far.
- **Session ids stay bare strings.** A `SessionRef(engine, id)` was planned and then dropped once
  measured: the `/v1` contract has clients send a bare id, and resolving which engine holds one is
  the controller's job rather than a mistake a type could prevent. The same id legitimately exists
  in both pools after a fallback, which no identity type fixes. See the record for the full reason;
  do not re-propose it without new evidence.

## The rules that bind every change

- **Write once, both engines get it.** Any change to behaviour a client can observe lands for Chrome
  and stealth in the same commit, not the next one and not a follow-up item. The only exit is a
  named browser-automation mechanism an engine genuinely cannot provide, cited in the commit and
  recorded in the ledger. "The engines are structured differently", "the other side needs a rewrite
  first" and "no caller needs it yet" are not exits, they are the work. If the second half cannot
  ship in the same commit, the change goes back to planning as one item covering both.
- **What a clearing core owns alone falls under the clearing-core decline, not write-once.** A
  tuning knob that only parameterizes one core's own loop (`BROWSER_WAIT_TIMEOUT`, Chrome's
  per-attempt wait) has nothing to tune on the other engine, and a dependency only one engine uses
  (Playwright and invisible-playwright for stealth, Selenium for Chrome) moves with a live check
  rather than a second-engine half. Both are documented as engine-specific and recorded in the
  ledger, never silent. The moment either changes what a client can observe, write-once applies.
- **Sharing the implementation is a means, not the rule.** Declining a code collapse stays allowed
  on cited mechanism grounds (the two clearing cores are the standing example), and it never
  licenses a behaviour fork. Two implementations that must behave identically are pinned by one
  conformance test.
- **Divergent bits are typed capability slots.** Never a nullable field, never a boolean-flag
  combination, never a per-engine branch inside shared code. A capability an engine cannot support
  is routed to one that can, or refused by name. One boolean the spine takes from each adapter is
  not a combination and is allowed while it is the only divergent bit on its surface
  (`turnstile_is_a_challenge` on `pipeline.verdict`); a second one on the same surface turns both
  into one typed capability. Never a silent no-op: `tabs_till_verify` quietly
  doing nothing on the stealth engine is the defect this rule exists to stop.
- **A shared component either derives a piece of state or does not own it.** Sharing the storage
  while each engine interprets it its own way is a fork wearing shared-code clothing, and nobody
  rules on it because it looks unified.
- **A rule that must hold for both engines exists once**, in this order of preference: a shared
  kernel both call, a typed capability the protocol forces both to answer, or one conformance test
  parameterized over both adapters. A hand-written pair is the last resort and it drifts. The
  conformance rung is `src/test_engine_conformance.py`, driven by `src/engine_fakes.py`; add to it
  rather than writing a second per-engine test, and delete the per-engine test it supersedes.
- **Parity is the default; a gap needs a ruling to stay open.** A gap you notice on a surface you are
  touching is levelled up in that change unless the owner gates it. A gate is the owner's ruling
  and is never self-issued by whoever is doing the work. A gap that predates this rule is paid
  when its surface is next touched, never in a sweep of its own.
- **A decline expires with its evidence.** Record the premise with the decline and treat the decline
  as void once that premise changes.
- **Verify by mutation.** A new test is not done until the production clause it names has been
  deleted, the test seen red, and the clause restored.

## How deep the seam goes, per surface

Every surface sits at a different depth. Assuming one is deeper than it is, is the usual way this
work gets mis-planned.

| Surface | Depth | What is shared | Status |
|---|---|---|---|
| Request boundary | Full takeover | Parsing, typing and validation of every `/v1` parameter | **Done**, `validate_request_types` |
| Result assembly | Full takeover | `assembly.py`: the read order and every field rule | **Done** |
| Solve orchestration | Takeover of orchestration | The page verdict and the navigation order (`pipeline.py`), the solve deadline (`budget.py`). The even split and the fallback were already single-sourced in `_resolve_challenge` | **Done** |
| Challenge clearing | Engine only, by mechanism | Nothing. Declined, see Ownership | Standing decline |
| Sessions | Takeover | One `SessionStore` per engine, one implementation (`sessions.py`). No `SessionRef`: see the record | **Done** |
| Config | Takeover | One reader per setting. `utils.get_config_*` stays where upstream put it, see the record | **Done** |
| Passthrough | Not an engine surface | Single implementation already | n/a |

**The two depths fail differently, so look for different things.** A taken-over surface produces
**upstream-drop** bugs: behaviour the replaced code had that ours silently lost. A surface left at
mechanism depth produces **duplicate-implementation** bugs: one rule restated at two sites with one
of them wrong. The cookie-ordering defect found on 2026-08-25 was the second kind. Neither class
shows up in the other's review.

## A takeover is not complete until its behaviour is inventoried

Cutting a surface over and passing `/live-check` is not the completion bar. A live check finds what
you thought to test. A takeover is done when the replaced code's behaviour has been walked end to
end and every item marked **present**, **deliberately dropped** with the reason, or **missing**. The
ledger catches an upstream changing a file after a takeover; nothing else catches what the takeover
failed to carry across in the first place.
