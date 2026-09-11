export const meta = {
  name: 'deep-audit',
  description: 'Read-only range audit of Solverr: many lenses per slice, two-ends tracing, adversarial verification, mutation checks',
  whenToUse: 'Run by the /deep-audit skill: first with mode "map", then with mode "audit" and the approved map',
  phases: [
    { title: 'Map', detail: 'slice the range, find write/read pairs, classify commits and surfaces' },
    { title: 'Find', detail: 'every lens over every slice, pair, commit batch and surface' },
    { title: 'Verify', detail: 'skeptics try to refute each deduplicated finding' },
    { title: 'Critic', detail: 'name uncovered ground and audit it once' },
    { title: 'Mutate', detail: 'real mutation checks of new tests in a throwaway worktree' },
  ],
}

const A = args || {}
if (A.mode !== 'map' && A.mode !== 'audit') throw new Error('args.mode must be "map" or "audit"')
if (!A.base || !A.head) throw new Error('args.base and args.head are required')

const RANGE = `${A.base}...${A.head}`
const SCOPE = A.pathFilter ? `${RANGE}, limited to ${A.pathFilter}` : RANGE
const GROUND = A.ground || {}
const SUITE = "PYTHONPATH=src uv run --no-project python -m unittest discover -s src -p 'test_*.py' -t src"

const RULE_FILES = ['engine-layer.md', 'code-quality.md', 'architecture.md', 'error-handling.md', 'security.md', 'testing.md']
const RULE_GROUP = 4
const SIBLING_BATCH = 10
const PARITY_BATCH = 10
const CODE_LENSES = ['correctness', 'security', 'performance', 'concurrency', 'dead']
const ALL_LENSES = ['correctness', 'security', 'performance', 'concurrency', 'dead', 'rules', 'docs', 'tests', 'tooling', 'wiring', 'parity', 'sibling', 'upstream', 'twoends']

const PREAMBLE = `You are one agent in a read-only audit of Solverr, a FlareSolverr fork: a Python 3.14 bypass proxy (bottle + waitress, synchronous WSGI) serving the FlareSolverr /v1 API, with two browser engines behind one interface. "chrome" is Selenium with the vendored undetected_chromedriver; "stealth" is Camoufox via invisible_playwright, run on one background asyncio loop thread (src/async_runtime.py). What both engines must do the same way lives once in a shared spine (src/assembly.py, src/pipeline.py, src/budget.py, src/sessions.py); each engine is an adapter over a clearing core derived from its upstream. The current directory is the repo root. Read-only reference clones of both upstreams sit beside it: ../FlareSolverr and ../Byparr.

The range under audit is ${SCOPE}. Commits whose subject says they sync FlareSolverr or Byparr are ports: audit Solverr's adaptation, not upstream's own choices.

Hard rules:
- Read-only. Never edit, create, stage or commit a tracked file, never start Docker or a browser, and never run src/tests.py (it launches real browsers against live sites). Python runs only through uv. The browser-free suite is: ${SUITE}. One module is: PYTHONPATH=src uv run --no-project python -m unittest <module>.
- Every finding cites a file:line you read in this run. A claim you cannot cite is not a finding.
- Label evidence honestly. "executed" means a command's output decides the claim (a whole-tree grep, git log or show, a browser-free test run, a small uv run --no-project python -c snippet reproducing pure logic). Everything else is "traced". A traced call chain proves the code exists, not that it does what you claim: follow the data to where it is used, not to the first function that agrees with you.
- Whether a page still clears a real challenge cannot be settled here. That needs /live-check; name it as the probe instead of guessing.
- Not findings: items on the parked list below; anything docs/dev/upstream-sync.md records under "Deliberately different"; a ruling in docs/dev/engine-layer-architecture.md or .claude/rules/engine-layer.md (read them before reporting; a decline whose stated premise no longer holds IS a finding); ledger entries below whose refutation still holds; upstream idiom inside files kept byte-identical to FlareSolverr (src/undetected_chromedriver/, src/tests.py, src/tests_sites.py, html_samples/, src/bottle_plugins/ except prometheus_plugin.py) or inside either clearing core, unless the range changed those lines.
- The project law is CLAUDE.md and .claude/rules/*.md. The seam-depth table in .claude/rules/engine-layer.md says which surfaces are shared and which stay per engine; read it before judging duplication. Code that looks wrong often encodes a measured constraint (.claude/rules/architecture.md lists them): ask for the measurement rather than calling it a bug.
- Tool traps on this machine: the Bash tool turns a double backslash into one before bash sees it, so build literal backslashes in Python with chr(92); working copies may be CRLF while the upstream clones are LF, so diff against them with --strip-trailing-cr; a Docker path argument from Git Bash needs MSYS_NO_PATHCONV=1. A search that returns zero is suspect: re-run it another way before believing it.

Parked, not findings:
${GROUND.parked || '(none given)'}

Ledger of previously refuted findings:
${GROUND.ledger || '(empty)'}`

const LENS_BRIEFS = {
  correctness: { agentType: 'code-reviewer', brief: 'Find real correctness defects: wrong logic, None and empty handling, bool-versus-int and truthiness on request input, state bugs, error handling that swallows a failure or leaks a traceback into the response, and anything that breaks the /v1 contract (additive optional fields only, the "FlareSolverr is ready!" banner). The changes have to work together, so follow each changed function to its callers inside and outside the slice, and when a change lands in one engine, open the other.' },
  security: { agentType: 'security-reviewer', brief: 'Find security defects per .claude/rules/security.md: untrusted /v1 and passthrough input reaching a browser (only http(s) URLs, postData escaping in postform.py, proxy validation in geo.proxy_to_config, the passthrough host allow list), secrets or solved cookies reaching a log or an error message, proxy credentials left on disk, a default flipped to exposed. Solverr has no auth by design; do not report its absence.' },
  performance: { agentType: 'performance-reviewer', brief: 'Find real bottlenecks: extra browser launches, work that spends the maxTimeout budget twice, anything blocking the single stealth event loop, locks held across I/O or a whole solve, waitress threads held longer than a solve needs, and caches, dicts or label sets with no bound.' },
  concurrency: { brief: 'Hunt ordering and lifecycle bugs: two waitress threads racing on shared module state without a lock; a session found and then used in separate lock acquisitions, so the reaper or the cap can act in between (src/test_session_reaping.py shows the deterministic way to prove a race); a lock held across a socket write or a browser call; a coroutine not awaited, or a task nobody holds, on the stealth loop; AsyncRuntime.run called from the loop thread itself; a deadline computed on one clock and compared on another; a browser, context, page or temp directory that leaks when a launch or solve fails part-way (cleanup belongs in finally). Name the interleaving that breaks and what the client sees.' },
  dead: { brief: 'Find dead and unused code the range added or left behind: unused functions, parameters, constants, environment variables and config readers, branches that can no longer be reached, commented-out blocks. Before calling anything unused, search the WHOLE repository, including src/test_*.py, src/engine_fakes.py, .github/, .githooks/, .claude/, Dockerfile, docker-compose.yml, README.md and docs/; bottle route and plugin decorators, getattr, and names read from the environment all count as uses. "Nothing calls X" is the claim class that fails most, so put exactly what you searched in evidenceDetail.' },
  rules: { brief: 'Audit against one rule file. Read it first and turn it into a checklist, then walk the slices. Each finding quotes a few words of the exact rule it breaks. Rules a hook already enforces (em dashes, commit message shape, site names in docs) are findings only where the hook cannot see them.' },
  docs: { agentType: 'doc-reviewer', brief: 'Check every claim in the docs in this slice against current code: files, symbols, environment variables and their defaults, counts, behaviour. Re-derive any stated count rather than trusting it. Also check claims in CLAUDE.md, .claude/rules and .claude/skills about code or tooling the range changed, and that CONTRIBUTING.md and .github/pull_request_template.md still match .claude/rules/workflow.md.' },
  tests: { brief: 'Audit the tests in this slice: tests that cannot fail (asserting the value just set, asserting a mock was called without checking its arguments, a loop that never iterates), names that claim more than the body checks, a rule for both engines pinned by a hand-maintained per-engine pair instead of one case in src/test_engine_conformance.py, and a rule for both engines pinned on only one. For up to five new tests, do a reasoned mutation: name the production clause the test pins and say whether deleting it would turn the test red. If it would not, that is a finding, labelled traced.' },
  tooling: { brief: 'Audit the repository tooling in this slice: .githooks/ scripts and their self-test, .claude/hooks/ guards and their fixtures, .claude/settings.json permissions and hook wiring, and .github/workflows/. Look for a regex that cannot match what it claims (Windows backslash paths, PowerShell spellings, case) or that matches far more than intended, a guard or rule with no fixture that would fail without it, permission rules where deny-then-ask-then-allow precedence gives a different answer than the comment intends, a CI step that cannot fail, an action not pinned to a commit, and docs (CLAUDE.md, .claude/rules/workflow.md, CONTRIBUTING.md) that describe the tooling differently from what it does. Running bash .githooks/tests/run.sh or bash .claude/hooks/tests/run-all.sh is allowed and counts as executed evidence; the second takes about 90 seconds on this machine, so give it a long timeout.' },
  wiring: { brief: '' },
  parity: { brief: 'For each user-visible change below, check that the Chrome and stealth engines both got it in this range, per "Write once, both engines get it" in .claude/rules/engine-layer.md. A gap is a finding unless it falls under a recorded exit: a named browser-automation mechanism one engine genuinely lacks, cited in the commit and recorded in docs/dev/upstream-sync.md, or the clearing-core decline for a knob or dependency only one core uses. Also report a capability that silently does nothing on one engine instead of being routed or refused by name, and a per-engine branch or a new boolean-flag combination inside the shared spine.' },
  sibling: { brief: 'Each commit below fixed a defect. For each one, read its diff, state the defect as a pattern, then search the whole tree for other sites with the same pattern: the other engine\'s adapter or clearing core, the other /v1 command, the passthrough, the same call shape elsewhere. Report every site that still carries the defect. A site the commit message or docs/dev/upstream-sync.md deliberately left, with a reason, is not a finding.' },
  upstream: { brief: 'This surface was taken over from an upstream into Solverr\'s shared spine. Walk the replaced upstream code\'s behaviour end to end, starting from theirs rather than ours: ../FlareSolverr for the Chrome side, the controller and sessions; ../Byparr for the stealth side. Mark each behaviour present (cite ours), deliberately dropped (cite docs/dev/upstream-sync.md or docs/dev/engine-layer-architecture.md) or missing. Report only the missing ones as findings.' },
  twoends: { brief: '' },
}

const WIRING_TASKS = [
  { target: 'packaging', brief: 'Audit packaging for the range: the Dockerfile (base image, installed packages, the Camoufox fetch, the baked geoip database and STEALTHFOX_GEOIP_MMDB, HEALTHCHECK, the user and paths the code expects), docker-compose.yml, a .dockerignore entry excluding something the image needs, requirements.txt pins against what src/ imports and against each other (one pin capping another), test-requirements.txt, the package.json version, and the release workflows that build and publish the image.' },
  { target: 'config and contract', brief: 'Audit config and contract wiring for the range: every environment variable src/ reads (config.py, utils.py, flaresolverr.py, and anywhere else os.environ appears) against the README configuration table, its default in code against the default the README states and docker-compose.yml sets, and variables documented but never read; /v1 request fields declared in src/dtos.py against the fields the controller and engines read; response fields emitted against the README response example; error messages clients or the fallback match on; and the CHANGELOG [Unreleased] entries against what the range actually changed.' },
]

const SEV = { type: 'string', enum: ['high', 'medium', 'low'] }
const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'the defect as a one-line claim' },
          file: { type: 'string', description: 'repo-relative path' },
          line: { type: 'integer' },
          severity: SEV,
          claim: { type: 'string', description: 'the defect in two or three sentences' },
          failureScenario: { type: 'string', description: 'concrete request, config or state, and the wrong result a client or deployer sees' },
          evidence: { type: 'string', enum: ['executed', 'traced'] },
          evidenceDetail: { type: 'string', description: 'the command and its output, or the lines read' },
          surface: { type: 'string', description: 'surface or subsystem, e.g. "sessions", "result assembly", "passthrough", "tooling"' },
        },
        required: ['title', 'file', 'line', 'severity', 'claim', 'failureScenario', 'evidence', 'evidenceDetail', 'surface'],
      },
    },
    searched: { type: 'string', description: 'what you covered, so an empty result is distinguishable from an unsearched one' },
  },
  required: ['findings', 'searched'],
}

const CONTRACT = {
  type: 'object',
  properties: {
    sites: { type: 'array', items: { type: 'string' }, description: 'file:line of every site on your side' },
    key: { type: 'string', description: 'exact name, key, field, env var or message shape' },
    type: { type: 'string' },
    unitsAndScale: { type: 'string', description: 'e.g. ms or s, bytes, 0-based index, a list or a dict' },
    defaultAndEmpty: { type: 'string', description: 'default value, and what None, empty or missing means' },
    timing: { type: 'string', description: 'when this side acts relative to the request, the solve, the lock, and the other side' },
    otherParties: { type: 'string', description: 'every other writer or reader you found, including the other engine' },
    notes: { type: 'string' },
  },
  required: ['sites', 'key', 'type', 'unitsAndScale', 'defaultAndEmpty', 'timing', 'otherParties', 'notes'],
}

const VERDICT = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    evidence: { type: 'string', enum: ['executed', 'traced'] },
    probe: { type: 'string', description: 'if not settled by execution, the one probe (a browser-free test, a log line, a /live-check run) that would settle it' },
    severity: SEV,
  },
  required: ['refuted', 'reason', 'evidence', 'probe', 'severity'],
}

const chunk = (xs, n) => {
  const out = []
  for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n))
  return out
}
const sliceText = s => `Slice "${s.id}" (${s.title}; surface: ${s.surface}; engines: ${s.engines}). Its changed files: run git diff --name-only ${RANGE} -- ${s.paths.join(' ')}`
const findingsPrompt = (brief, target) => `${PREAMBLE}\n\nYour lens: ${brief}\n\nYour target:\n${target}\n\nReturn every finding that survives your own re-read. An empty list is a valid answer when "searched" says what you covered.`

// One task per lens and target. Shared by map mode (for the estimate) and audit mode.
function planTasks(map, lenses) {
  const on = new Set(lenses && lenses.length ? lenses : ALL_LENSES)
  const code = map.slices.filter(s => s.kind === 'code')
  const tasks = []
  const add = (lens, target, prompt, agentType) => tasks.push({ lens, target, prompt, agentType })
  for (const lens of CODE_LENSES) {
    if (!on.has(lens)) continue
    for (const s of code) add(lens, s.id, findingsPrompt(LENS_BRIEFS[lens].brief, sliceText(s)), LENS_BRIEFS[lens].agentType)
  }
  if (on.has('rules')) {
    for (const rule of RULE_FILES) {
      for (const group of chunk(code, RULE_GROUP)) {
        add('rules', `${rule}: ${group.map(s => s.id).join(', ')}`,
          findingsPrompt(`${LENS_BRIEFS.rules.brief} The rule file is .claude/rules/${rule}.`, group.map(sliceText).join('\n')))
      }
    }
  }
  if (on.has('docs')) for (const s of map.slices.filter(x => x.kind === 'docs')) add('docs', s.id, findingsPrompt(LENS_BRIEFS.docs.brief, sliceText(s)), 'doc-reviewer')
  if (on.has('tests')) for (const s of map.slices.filter(x => x.kind === 'tests')) add('tests', s.id, findingsPrompt(LENS_BRIEFS.tests.brief, sliceText(s)))
  if (on.has('tooling')) for (const s of map.slices.filter(x => x.kind === 'tooling')) add('tooling', s.id, findingsPrompt(LENS_BRIEFS.tooling.brief, sliceText(s)))
  if (on.has('wiring')) for (const w of WIRING_TASKS) add('wiring', w.target, findingsPrompt(w.brief, `The whole range ${SCOPE}.`))
  if (on.has('parity')) {
    for (const batch of chunk(map.userVisibleChanges, PARITY_BATCH)) {
      add('parity', batch.map(c => c.summary).join(' | ').slice(0, 120),
        findingsPrompt(LENS_BRIEFS.parity.brief, batch.map(c => `- ${c.summary} (source: ${c.source}; engines: ${c.engines})`).join('\n')))
    }
  }
  if (on.has('sibling')) {
    for (const batch of chunk(map.fixCommits, SIBLING_BATCH)) {
      add('sibling', batch.map(c => c.sha).join(','),
        findingsPrompt(LENS_BRIEFS.sibling.brief, batch.map(c => `- ${c.sha} ${c.subject}`).join('\n')))
    }
  }
  if (on.has('upstream')) {
    for (const s of map.surfaces) {
      add('upstream', s.surface, findingsPrompt(LENS_BRIEFS.upstream.brief,
        `Surface: ${s.surface}. Spine files: ${s.spineFiles.join(', ')}. Replaced upstream code: ${s.replacedUpstream.join(', ')}. Record: ${s.record}`))
    }
  }
  if (on.has('twoends')) for (const p of map.pairs) tasks.push({ lens: 'twoends', target: p.id, pair: p })
  return tasks
}

function countAgents(tasks) {
  return tasks.reduce((n, t) => n + (t.lens === 'twoends' ? 3 : 1), 0)
}

// ---------------------------------------------------------------- map mode

if (A.mode === 'map') {
  phase('Map')
  const mapBase = `${PREAMBLE}\n\nYou are building the audit's map, not auditing. Be complete: whatever you leave out goes unaudited.`
  const [sl, pr, cm, su] = await parallel([
    () => agent(`${mapBase}\n\nSplit the files changed in ${SCOPE} (git diff --name-only ${RANGE}${A.pathFilter ? ' -- ' + A.pathFilter : ''}) into cohesive slices by subsystem, not by file. Kinds: "code" (Python under src/ other than tests), "tests" (src/test_*.py and src/engine_fakes.py), "docs" (markdown, including .claude/rules, .claude/skills, .claude/agents and docs/), "tooling" (.githooks/, .claude/hooks/ scripts and fixtures, .claude/settings.json, .github/workflows/, renovate.json, .gitattributes, .editorconfig, and .claude/skills/*/*.js). Keep each code slice to roughly 40 changed files or 3000 changed lines, and each docs, tests or tooling slice to roughly 30 files. Give each slice repo-relative paths or directories that pathspec-match its files. Say which engines each slice concerns. List under "excluded", each with why: binary assets, html_samples/, the vendored src/undetected_chromedriver/ unless the range changed it, and packaging files (Dockerfile, docker-compose.yml, requirements*.txt, .dockerignore, package.json), since a separate wiring lens covers those.`, {
      label: 'map:slices', phase: 'Map', effort: 'high',
      schema: {
        type: 'object',
        properties: {
          slices: { type: 'array', items: { type: 'object', properties: {
            id: { type: 'string' }, kind: { type: 'string', enum: ['code', 'docs', 'tests', 'tooling'] }, title: { type: 'string' },
            paths: { type: 'array', items: { type: 'string' } }, surface: { type: 'string' },
            engines: { type: 'string', enum: ['chrome', 'stealth', 'both', 'neutral'] }, approxFiles: { type: 'integer' },
          }, required: ['id', 'kind', 'title', 'paths', 'surface', 'engines', 'approxFiles'] } },
          excluded: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, why: { type: 'string' } }, required: ['what', 'why'] } },
        },
        required: ['slices', 'excluded'],
      },
    }),
    () => agent(`${mapBase}\n\nFind the write/read pairs this range touches: places where one side produces a value and another consumes it, so two independent tracers can meet in the middle. Look especially at: a /v1 request field parsed and typed in src/dtos.py and read by the controller or an engine; the response assembled in src/assembly.py and serialised to the client; an environment variable read in src/config.py or src/utils.py against the README table and docker-compose.yml; a session created and stored in SessionStore against the reaper and the cap that evict it; cookies set through the pipeline against the cookie jar read back in assembly; the proxy dict validated in geo.proxy_to_config against the browser launch that uses it; the passthrough cache written and read; the timezone and language resolved once in geo.py and handed to both engines; a value logged against what the log may contain. Mark a value that goes out and comes back (serialise and deserialise, set and read back) as "round-trip", and a value both engines produce for one consumer as "cross-engine". Rank by risk; return at most 15.`, {
      label: 'map:pairs', phase: 'Map', effort: 'high',
      schema: {
        type: 'object',
        properties: { pairs: { type: 'array', items: { type: 'object', properties: {
          id: { type: 'string' }, value: { type: 'string' }, writeSide: { type: 'string', description: 'file:symbol' },
          readSide: { type: 'string', description: 'file:symbol' }, kind: { type: 'string', enum: ['write-read', 'round-trip', 'cross-engine'] }, why: { type: 'string' },
        }, required: ['id', 'value', 'writeSide', 'readSide', 'kind', 'why'] } } },
        required: ['pairs'],
      },
    }),
    () => agent(`${mapBase}\n\nClassify commits in ${SCOPE}. "fixCommits": every commit that fixed a defect (usually a "fix" subject), with sha and subject. "userVisibleChanges": everything a deployer or an API client can observe that the range changed. The [Unreleased] entries the range added to CHANGELOG.md are the primary source, one per bullet (git diff ${RANGE} -- CHANGELOG.md shows them); add any feat or fix commit that changed observable behaviour without an entry. Say which engines each change claims to cover.`, {
      label: 'map:commits', phase: 'Map', effort: 'medium',
      schema: {
        type: 'object',
        properties: {
          fixCommits: { type: 'array', items: { type: 'object', properties: { sha: { type: 'string' }, subject: { type: 'string' } }, required: ['sha', 'subject'] } },
          userVisibleChanges: { type: 'array', items: { type: 'object', properties: {
            summary: { type: 'string' }, source: { type: 'string' }, engines: { type: 'string', enum: ['chrome', 'stealth', 'both', 'neutral'] },
          }, required: ['summary', 'source', 'engines'] } },
        },
        required: ['fixCommits', 'userVisibleChanges'],
      },
    }),
    () => agent(`${mapBase}\n\nList the surfaces this range touches that were taken over into the shared spine: the rows the seam-depth table in .claude/rules/engine-layer.md marks Done (request boundary, result assembly, solve orchestration, sessions, config). For each, give the spine files the range touched, the upstream code the spine replaced (file and function in ../FlareSolverr or ../Byparr), and the record that documents the takeover (a section of docs/dev/engine-layer-architecture.md or docs/dev/upstream-sync.md). An empty list is right when the range touches none of them.`, {
      label: 'map:surfaces', phase: 'Map', effort: 'medium',
      schema: {
        type: 'object',
        properties: { surfaces: { type: 'array', items: { type: 'object', properties: {
          surface: { type: 'string' }, spineFiles: { type: 'array', items: { type: 'string' } },
          replacedUpstream: { type: 'array', items: { type: 'string' } }, record: { type: 'string' },
        }, required: ['surface', 'spineFiles', 'replacedUpstream', 'record'] } } },
        required: ['surfaces'],
      },
    }),
  ])
  if (!sl || !pr || !cm || !su) throw new Error('a map agent failed; re-run map mode')
  const map = {
    slices: sl.slices, excluded: sl.excluded, pairs: pr.pairs,
    fixCommits: cm.fixCommits, userVisibleChanges: cm.userVisibleChanges, surfaces: su.surfaces,
  }
  const tasks = planTasks(map, A.lenses)
  const byLens = {}
  for (const t of tasks) byLens[t.lens] = (byLens[t.lens] || 0) + (t.lens === 'twoends' ? 3 : 1)
  return { map, estimate: { finderAgents: countAgents(tasks), byLens, note: 'verification adds about one agent per medium or low finding and three per high one, plus one critic and, unless --no-mutate, one mutation agent' } }
}

// ---------------------------------------------------------------- audit mode

if (!A.map) throw new Error('audit mode needs args.map from an approved map run')

async function runTwoEnds(p) {
  const side = (which, start) => agent(`${PREAMBLE}\n\nTwo-ends tracing. You own ONE end of a value that crosses a boundary; another agent owns the other end and neither sees the other. Value: ${p.value}. Start at the ${which} side: ${start}. Trace where the value is ${which === 'write' ? 'produced and written' : 'read and consumed'} and document the contract as your side sees it. Do not read the other side's code beyond finding its name.`, {
    label: `twoends:${p.id}:${which}`, phase: 'Find', schema: CONTRACT,
  })
  const [w, r] = await parallel([() => side('write', p.writeSide), () => side('read', p.readSide)])
  if (!w || !r) return null
  return agent(`${PREAMBLE}\n\nReconcile two independent traces of one value (${p.value}, ${p.kind}). Writer's contract:\n${JSON.stringify(w, null, 2)}\n\nReader's contract:\n${JSON.stringify(r, null, 2)}\n\nEvery mismatch is a candidate: a key written but never read or read under another name, different defaults, different units or scale, empty meaning different things, a reader that can run before the writer or outside the lock the writer holds, a second writer one side does not know about (the other engine counts), a field that goes out and does not come back. Re-read the cited lines before reporting each one.`, {
    label: `twoends:${p.id}:reconcile`, phase: 'Find', schema: FINDINGS,
  })
}

async function runTask(t) {
  try {
    const out = t.lens === 'twoends'
      ? await runTwoEnds(t.pair)
      : await agent(t.prompt, { label: `${t.lens}:${t.target}`.slice(0, 80), phase: 'Find', schema: FINDINGS, agentType: t.agentType })
    return { t, out }
  } catch (e) {
    return { t, out: null }
  }
}

const RANK = { high: 3, medium: 2, low: 1 }
function dedupe(findings) {
  const out = []
  for (const f of findings) {
    const m = out.find(o => o.file === f.file && Math.abs(o.line - f.line) <= 3)
    if (!m) { out.push({ ...f, lenses: [f.lens], claims: [f.claim] }); continue }
    if (!m.lenses.includes(f.lens)) m.lenses.push(f.lens)
    m.claims.push(f.claim)
    if (RANK[f.severity] > RANK[m.severity]) m.severity = f.severity
    if (f.evidence === 'executed') m.evidence = 'executed'
  }
  return out
}

function collect(results, coverage) {
  const found = []
  for (const r of results) {
    if (!r) continue
    coverage.push({ lens: r.t.lens, target: r.t.target, status: r.out ? 'ok' : 'failed', findings: r.out ? r.out.findings.length : 0, searched: r.out ? r.out.searched : '' })
    if (r.out) for (const f of r.out.findings) found.push({ ...f, lens: r.t.lens })
  }
  return found
}

const VERIFY_LENSES = {
  code: 'Re-read the cited lines and enough surrounding code to decide whether the defect is real as stated. Check the callers and the data actually flowing in, and for an engine change, what the other engine does.',
  execute: `Settle it by running something that changes no tracked file: a whole-tree grep, git log or show, a browser-free test module, a uv run --no-project python -c snippet reproducing the logic, or a hook self-test (bash .githooks/tests/run.sh, bash .claude/hooks/tests/run-all.sh). If only a live browser could settle it, do not refute on that ground; set evidence to traced and name /live-check as the probe.`,
  ruled: 'Decide whether this is recorded as deliberate in docs/dev/upstream-sync.md, ruled in docs/dev/engine-layer-architecture.md or .claude/rules/engine-layer.md (including the clearing-core decline and the one-boolean allowance), on the parked list, in the ledger with a reason that still holds, intended per an owner ruling in .claude/rules, or contradicted by a gate that passes (the browser-free suite, bash .githooks/tests/run.sh, bash .claude/hooks/tests/run-all.sh). Any of those refutes it.',
}
const ALL_VERIFY = Object.values(VERIFY_LENSES).join(' ')

async function verify(f) {
  const subject = `Finding (from lenses: ${f.lenses.join(', ')}; severity ${f.severity}):\n${f.title}\n${f.file}:${f.line}\nClaims:\n${f.claims.map(c => '- ' + c).join('\n')}\nFailure scenario: ${f.failureScenario}\nFinder's evidence (${f.evidence}): ${f.evidenceDetail}`
  const ask = lens => agent(`${PREAMBLE}\n\nYou are a skeptic. Try to REFUTE the finding below. Default to refuted=true when uncertain. A negative claim ("nothing calls X", "never read") must be re-searched across the whole repository, tests, tooling and docs included, before you accept it.\n\nYour check: ${lens}\n\n${subject}`, {
    label: `verify:${f.file.split('/').pop()}:${f.line}`, phase: 'Verify', schema: VERDICT, effort: f.severity === 'high' ? 'high' : undefined,
  })
  const votes = (f.severity === 'high'
    ? await parallel(Object.values(VERIFY_LENSES).map(l => () => ask(l)))
    : [await ask(ALL_VERIFY)]).filter(Boolean)
  if (!votes.length) return { ...f, survives: false, verdicts: [], unverifiable: true }
  const holding = votes.filter(v => !v.refuted)
  const survives = f.severity === 'high' ? holding.length >= 2 : holding.length === 1
  const executed = holding.some(v => v.evidence === 'executed')
  const probe = (holding.find(v => v.probe) || {}).probe || ''
  const severity = holding.length ? holding.map(v => v.severity).sort((a, b) => RANK[b] - RANK[a])[0] : f.severity
  return { ...f, survives, evidence: executed || f.evidence === 'executed' ? 'executed' : 'traced', probe, severity, verdicts: votes }
}

phase('Find')
const tasks = planTasks(A.map, A.lenses)
log(`${tasks.length} finder tasks, about ${countAgents(tasks)} finder agents`)
const coverage = []
let found = collect(await parallel(tasks.map(t => () => runTask(t))), coverage)
const failed = coverage.filter(c => c.status === 'failed')
if (failed.length) log(`${failed.length} finder tasks failed and are reported as uncovered`)

// Barrier on purpose: lenses overlap (correctness and concurrency flag the same line), so dedupe before paying for verification.
phase('Verify')
let deduped = dedupe(found)
log(`${found.length} raw findings, ${deduped.length} after dedupe`)
let verified = (await parallel(deduped.map(f => () => verify(f)))).filter(Boolean)

phase('Critic')
const gaps = await agent(`${PREAMBLE}\n\nYou are the completeness critic for this audit. Coverage so far (lens, target, status, findings, what was searched):\n${JSON.stringify(coverage, null, 1)}\n\nSlices in the map:\n${A.map.slices.map(s => `${s.id} (${s.kind}): ${s.paths.join(' ')}`).join('\n')}\n\nExcluded:\n${A.map.excluded.map(e => `${e.what}: ${e.why}`).join('\n')}\n\nConfirmed so far:\n${verified.filter(v => v.survives).map(v => `- ${v.title} (${v.file}:${v.line})`).join('\n') || '(none)'}\n\nName what is missing: a failed or thin task, a slice whose "searched" shows it was skimmed, an exclusion that hides real code, a write/read pair nobody traced, an engine the range changed that no slice or parity task looked at, a subsystem the range changed that no slice owns. Return at most 10 gaps, most important first; an empty list is fine.`, {
  label: 'critic', phase: 'Critic', effort: 'high',
  schema: {
    type: 'object',
    properties: { gaps: { type: 'array', items: { type: 'object', properties: {
      lens: { type: 'string', enum: ALL_LENSES.filter(l => l !== 'twoends' && l !== 'wiring') }, target: { type: 'string', description: 'paths, commits or the behaviour to audit' }, why: { type: 'string' },
    }, required: ['lens', 'target', 'why'] } } },
    required: ['gaps'],
  },
})
const selected = A.lenses && A.lenses.length ? A.lenses : ALL_LENSES
const gapList = gaps ? gaps.gaps.filter(g => selected.includes(g.lens)) : []
if (gapList.length) {
  log(`critic named ${gapList.length} gaps; auditing them once (no further rounds)`)
  const gapTasks = gapList.map(g => ({
    lens: g.lens, target: `gap: ${g.target}`.slice(0, 120), agentType: LENS_BRIEFS[g.lens].agentType,
    prompt: findingsPrompt(g.lens === 'rules' ? `${LENS_BRIEFS.rules.brief} Pick the rule file the gap names.` : LENS_BRIEFS[g.lens].brief, `${g.target}\n(Why this was missed: ${g.why})`),
  }))
  const more = collect(await parallel(gapTasks.map(t => () => runTask(t))), coverage)
  found = found.concat(more)
  const fresh = dedupe(more).filter(f => !deduped.some(o => o.file === f.file && Math.abs(o.line - f.line) <= 3))
  verified = verified.concat((await parallel(fresh.map(f => () => verify(f)))).filter(Boolean))
}

let mutation = null
if (A.mutate) {
  phase('Mutate')
  const suspects = verified.filter(v => v.survives && v.lenses.includes('tests')).map(v => `${v.file}:${v.line} ${v.title}`)
  mutation = await agent(`You work in a throwaway git worktree of the Solverr repo, so edits here never reach the owner's tree. Find the tests added in ${RANGE} (git diff ${RANGE} -- 'src/test_*.py', then the new test methods in those files) and pick up to five, preferring these suspects:\n${suspects.join('\n') || '(none flagged; pick tests whose production clause is easy to isolate)'}\n\nFor each, one at a time: delete or neutralise the production clause the test claims to pin, run only that test from the worktree root with PYTHONPATH=src uv run --no-project python -m unittest <module>.<Class>.<test_method>, record red or green, restore the file with git checkout -- <file>, and move on. Python runs only through uv. Never run src/tests.py, which needs a browser. A test that stays green with its clause deleted is a confirmed finding with evidence "executed". If a test cannot run, report that rather than guessing.`, {
    label: 'mutate', phase: 'Mutate', isolation: 'worktree', schema: FINDINGS,
  })
}

const confirmed = verified.filter(v => v.survives).sort((a, b) => RANK[b.severity] - RANK[a.severity])
return {
  confirmed,
  mutationFindings: mutation ? mutation.findings : [],
  mutationSearched: mutation ? mutation.searched : '',
  refuted: verified.filter(v => !v.survives).map(v => ({ title: v.title, file: v.file, line: v.line, lenses: v.lenses, reasons: v.verdicts.filter(x => x.refuted).map(x => x.reason), unverifiable: !!v.unverifiable })),
  coverage,
  gaps: gapList,
  counts: { tasks: tasks.length, rawFindings: found.length, verified: verified.length, confirmed: confirmed.length },
}
