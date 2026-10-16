// Offline end-to-end test of the mod's hooks module (register.js) with a fake `$`: a virtual clock, an in-memory
// fs and a recording prompt.submit. Runs the real tick loop, state file and turn bookkeeping, no claude process.
// node --test ~/plugins/heartbeat/test/register.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { GAP_MIN } from '../hooks/register.js'

const MIN = 6_000                                   // test mode: a minute is 6 s
const T0 = Date.UTC(2026, 9, 13, 10, 0, 0)          // 12:00 in Zurich: daytime cap (60 min)
const D = '/hbtest'
// The host's frame around the mod's beat at turn.start (Claude Code 2.1.296, verbatim from the transcript of 2026-10-13).
const FRAME = t => `The heartbeat plugin sent a message:\n${t}\n\nThis is how Claude Code surfaces a prompt a plugin ` +
  "submits between turns \u2014 it starts this turn in the user's place. Address the message above."

let loads = 0
// opts (gate tests): env adds env vars (HEARTBEAT_GATE), process/agent become $.process/$.agent.
async function harness(t0 = T0, opts = {}) {      // a fresh module instance each: st, lastBeat, lastError start clean
  const { register } = await import(`../hooks/register.js?harness=${++loads}`)
  const hooks = {}, files = new Map(), submitted = []
  let now = t0, every = null
  const $ = {
    clock: { now: async () => now, every: (ms, fn) => { every = { ms, fn }; return { cancel() { every = null } } } },
    env: { get: async k => ({ HEARTBEAT_MOD_TEST_DIR: D, HEARTBEAT_MINUTES: '10', ...opts.env })[k] },
    process: opts.process,
    agent: opts.agent,
    fs: {
      stat: async p => { if (!files.has(p)) throw new Error('ENOENT: ' + p); return { mtimeMs: files.get(p).mtimeMs } },
      write: async (p, data) => { files.set(p, { data, mtimeMs: now }) },
      list: async dir => {
        const out = [...files].filter(([p]) => p.startsWith(dir + '/') && !p.slice(dir.length + 1).includes('/'))
          .map(([p, f]) => ({ name: p.slice(dir.length + 1), kind: 'file', mtimeMs: f.mtimeMs }))
        if (!out.length && dir !== `${D}/inbox`) throw new Error('ENOENT: ' + dir)
        return out
      },
    },
    // As in the host: the mod's own prompt.submit hook sees its beat bare (origin plugin, no turnId); the test's
    // turn() then hands turn.start the framed text (FRAME), as the host does.
    prompt: { submit: async ({ text }) => {
      submitted.push({ at: now, text })
      return hooks['prompt.submit']($, { text, origin: { kind: 'plugin', name: 'heartbeat' }, wait: false }, async x => x)
    } },
    ui: { log() {} },
  }
  register((name, fn) => { hooks[name] = fn })
  const fire = (name, e) => hooks[name]($, e, async x => x)
  const flush = () => new Promise(r => setImmediate(r))
  return {
    $, files, submitted, fire,
    get now() { return now },
    state: () => JSON.parse(files.get(`${D}/state.json`).data),
    async ticked() { now += every.ms; await every.fn(); await flush(); return JSON.parse(files.get(`${D}/state.json`).data) },
    async start() { await fire('session.start', { isInteractive: true, cwd: '/home/node/jobs/hbtest' }) },
    async advance(ms) {                              // run the 15 s (virtual) ticks up to now + ms
      const end = now + ms
      while (now + every.ms <= end) { now += every.ms; await every.fn(); await flush() }
      now = end
    },
    async turn(text, { tools = [], reason = 'answer', mins = 0.5 } = {}) {   // one main turn
      await fire('turn.start', { text, turnId: 't' })
      for (const tool of tools) await fire('tool.call', { tool })
      now += mins * MIN
      await fire('turn.complete', { reason })
    },
    note(name) { files.set(`${D}/inbox/${name}`, { data: 'x', mtimeMs: now }) },
  }
}

test('end to end: exact thresholds, hold, inbox within ~2 min, owner message resets', async () => {
  const h = await harness()
  await h.start()
  const until = async (max) => {                   // advance to the next beat, return minutes waited
    const t = h.now
    for (let i = 0; i < max * 4; i++) { await h.advance(MIN / 4); if (h.submitted.length) break }
    const b = h.submitted.shift()
    assert.ok(b, `a beat within ${max} min`)
    return { b, waited: (b.at - t) / MIN }
  }
  const ladder = []
  for (let i = 0; i < 6; i++) { const { b, waited } = await until(130); ladder.push(Math.ceil(waited)); await h.turn(FRAME(b.text), { tools: ['Bash'] }) }
  // 10 after start; then 10, 20, 40, 60, 60 after each empty turn (the turn's own 0.5 min aside)
  assert.deepEqual(ladder, [10, 10, 20, 40, 60, 60])

  // A beat nobody answers (queued behind a dialog): no second beat, not even for an inbox note, until the cap.
  const { b: held } = await until(70)
  h.note('a.md')
  await h.advance(60 * MIN)
  assert.equal(h.submitted.length, 0, 'held while unanswered')
  assert.equal(h.state().unanswered, true)
  await h.turn(FRAME(held.text), { reason: 'error' })      // consumed (here by an API error): the hold ends
  assert.equal((await h.ticked()).unanswered, false, 'state file follows within a tick')

  // An inbox note at 60 min backoff still wakes after ~2 min idle (>= GAP_MIN after the last beat).
  await h.advance(GAP_MIN * MIN)
  h.note('b.md')
  const { b: inbox, waited } = await until(5)
  assert.ok(waited <= 2.5, `inbox wake after ${waited} min`)
  assert.match(inbox.text, /Idle 2\+ min, \d new note\(s\)/)
  await h.turn(FRAME(inbox.text), { tools: ['Read', 'Bash'] })   // notes handled: not an empty beat
  assert.equal((await h.ticked()).streak, 0, 'a beat with notes resets the backoff')

  // Back to the base; build the streak again, then an owner message resets it.
  for (let i = 0; i < 4; i++) { const { b } = await until(130); await h.turn(FRAME(b.text)) }
  assert.ok((await h.ticked()).idleMin >= 40)
  await h.fire('prompt.submit', { text: '<channel source="plugin:telegram-plus:telegram">hi</channel>', origin: { kind: 'user' } })
  await h.turn('<channel source="plugin:telegram-plus:telegram">hi</channel>', { tools: ['mcp__plugin_telegram-plus_telegram__reply'] })
  const st = await h.ticked()
  assert.equal(st.streak, 0)
  assert.equal(st.idleMin, 10)
  const { waited: afterOwner } = await until(15)
  assert.equal(Math.ceil(afterOwner), 10, 'base interval right after an owner message')

  // The state file stays fresh for the cron fallback through the backoff (written about once a minute).
  assert.ok(h.now - h.state().lastTick <= MIN, 'lastTick fresh')
})

async function nextBeat(h, max) {                   // advance to the next beat: [beat, minutes waited]
  const t = h.now
  for (let i = 0; i < max * 4 && !h.submitted.length; i++) await h.advance(MIN / 4)
  const b = h.submitted.shift()
  assert.ok(b, `a beat within ${max} min`)
  return [b, Math.ceil((b.at - t) / MIN)]
}

test('regression 2026-10-13 night: framed beats, one quick Bash call each, back off 10, 10, 20, 40, 80, 120', async () => {
  const h = await harness(Date.UTC(2026, 9, 13, 2, 1, 48))   // the restart at 04:01:48 Zurich: night cap 120
  await h.start()
  const ladder = [], status = []
  for (let i = 0; i < 6; i++) {                     // 0.2.0: streak 0 and a beat every 10 min all night
    const [b, waited] = await nextBeat(h, 130)
    ladder.push(waited)
    assert.match(b.text, /^\[heartbeat\] Idle \d+\+ min, 0 new note\(s\)/)
    await h.turn(FRAME(b.text), { tools: ['Bash'] })
    const s = await h.ticked()
    status.push(`${s.streak}/${s.idleMin}`)
  }
  assert.deepEqual(status, ['1/10', '2/20', '3/40', '4/80', '5/120', '6/120'])
  assert.deepEqual(ladder, [10, 10, 20, 40, 80, 120])
  assert.equal(h.state().lastError, '', 'the real frame is recognized, no drift flagged')
})

test('frame drift: still counted through prompt.submit, flagged in lastError', async () => {
  const h = await harness()
  await h.start()
  const [b] = await nextBeat(h, 15)
  await h.turn(`<plugin_message source="heartbeat">\n${b.text}`, { tools: ['Bash'] })
  const s = await h.ticked()
  assert.equal(s.streak, 1)
  assert.match(s.lastError, /host frame changed/)
})

test('a subagent turn.start (CC >= 2.1.299, carries agentId) does not mark the main loop busy', async () => {
  const h = await harness()
  await h.start()
  await h.fire('turn.start', { text: 'subagent prompt', turnId: 's', agentId: 'a123' })
  await h.advance(11 * MIN)
  assert.equal(h.submitted.length, 1, 'the beat still fires while a subagent runs')
})

// --- gate (0.3.0) -----------------------------------------------------------------------------------------------
// A fake hb-gate: answers from `answer` (a function of the argv, or a thrown error), records every call.
function fakeGate(answer) {
  const calls = []
  return {
    calls,
    process: { run: async (argv, init) => {
      calls.push({ argv: [...argv], init })
      let a = typeof answer === 'function' ? answer(argv) : answer
      if (typeof a === 'function') a = a(argv)   // answer: () => current (a value or a function of the argv)
      if (a instanceof Error) throw a
      return { exitCode: 0, stdout: JSON.stringify(a) + '\n', stderr: '' }
    } },
  }
}
const GATE_ENV = { HEARTBEAT_GATE: '/fake/hb-gate' }

test('gate skip: nothing is submitted, the skip is counted, the backoff is untouched, nextDue moves on', async () => {
  let answer = { decision: 'skip', reason: 'agent running: 1 (a1)' }
  const g = fakeGate(() => answer)
  const agents = [{ id: 'a1', status: 'running', description: 'build', type: 'general-purpose' }]
  const h = await harness(T0, { env: GATE_ENV, process: g.process, agent: { list: async () => agents } })
  await h.start()
  await h.advance(31 * MIN)
  assert.equal(h.submitted.length, 0, 'a skipped beat never reaches prompt.submit')
  assert.equal(g.calls.length, 3, 'consulted once per idle threshold (10, 20, 30 min), not every tick')
  const [argv] = g.calls.map(c => c.argv)
  assert.equal(argv[0], '/fake/hb-gate')
  assert.deepEqual(argv.slice(1, 4), ['--json', '--notes', '0'])
  assert.ok(argv.includes('--first'), 'first beat of the session is flagged')
  assert.deepEqual(argv.slice(argv.indexOf('--running'), argv.indexOf('--running') + 6),
    ['--running', '1', '--finished', '0', '--ids', 'a1'])
  assert.ok(g.calls[0].init.timeoutMs > 0, 'bounded')
  const s = await h.ticked()
  assert.equal(s.skips, 3)
  assert.equal(s.beats, 0)
  assert.equal(s.streak, 0, 'skips are not empty beats')
  assert.equal(s.unanswered, false)
  assert.match(s.lastGate, /^skip: agent running/)
  assert.ok(s.nextDue >= s.lastSkip + 10 * MIN, 'nextDue follows the skip: the cron fallback keeps holding')
  assert.equal(s.lastError, '')

  // The agent finishes: the gate now says wake, and the beat goes out.
  agents[0].status = 'completed'
  answer = argv => argv.includes('--finished') && argv[argv.indexOf('--finished') + 1] === '1'
    ? { decision: 'wake', reason: '1 agent(s) finished since the last beat' } : { decision: 'skip', reason: 'x' }
  const [b] = await nextBeat(h, 15)
  assert.match(b.text, /^\[heartbeat\] Idle 10\+ min, 0 new note\(s\)/)
  assert.match(h.state().lastGate, /^wake: 1 agent\(s\) finished/)
  await h.turn(FRAME(b.text), { tools: ['Bash'] })
  const before = g.calls.length
  answer = { decision: 'skip', reason: 'no agent running, nothing startable' }
  await h.advance(11 * MIN)
  assert.equal(h.submitted.length, 0)
  assert.ok(!g.calls.at(-1).argv.includes('--first'), 'only the first beat is flagged')
  assert.ok(g.calls.length > before)
  assert.equal(h.state().streak, 1, 'the empty beat that ran still counts; the skip after it adds nothing')
})

test('gate: inbox notes still wake within ~2 min after a skip (the gate is asked and says wake)', async () => {
  const g = fakeGate(argv => argv[argv.indexOf('--notes') + 1] !== '0'
    ? { decision: 'wake', reason: 'inbox: 1 note(s)' } : { decision: 'skip', reason: 'agent running: 1' })
  const h = await harness(T0, { env: GATE_ENV, process: g.process, agent: { list: async () => [] } })
  await h.start()
  await h.advance(11 * MIN)
  assert.equal(h.submitted.length, 0)
  h.note('a.md')
  const [b, waited] = await nextBeat(h, 5)
  assert.ok(waited <= 3, `inbox wake after ${waited} min`)
  assert.match(b.text, /1 new note\(s\)/)
})

test('gate fails open: a crash, a timeout, garbage or a missing $.agent all mean wake', async () => {
  for (const [name, answer, agent] of [
    ['throws', new Error('spawn ENOENT'), { list: async () => [] }],
    ['garbage', 'skip', { list: async () => [] }],
    ['no $.agent', { decision: 'wake', reason: 'safety valve' }, undefined],
    ['agent.list throws', { decision: 'wake', reason: 'x' }, { list: async () => { throw new Error('no') } }],
  ]) {
    const g = fakeGate(answer)
    const h = await harness(T0, { env: GATE_ENV, process: g.process, agent })
    await h.start()
    await h.advance(11 * MIN)
    assert.equal(h.submitted.length, 1, `${name}: the beat goes out`)
    assert.equal(g.calls.length, 1, name)
    if (!agent || name === 'agent.list throws')
      assert.ok(!g.calls[0].argv.includes('--running'), `${name}: no agent info, the gate asks agent-watch`)
    if (name === 'throws') assert.match(h.state().lastError, /gate error/)
  }
  const h = await harness(T0, { process: { run: async () => { throw new Error('must not run') } } })
  await h.start()
  await h.advance(11 * MIN)
  assert.equal(h.submitted.length, 1, 'no HEARTBEAT_GATE in test mode: no gate')
  assert.equal(h.state().gate, false)
})

test('0.3.1: $.agent.list() is called as spelled (the loader rejects a noun read as a value); a non-list answer falls back', async () => {
  // The host's load rule (CC 2.1.296+, same as `claude plugin validate`): every $ noun is spelled $.noun.event(...)
  // at the call site. 0.3.0's `typeof $.agent?.list === 'function'` broke it, and the whole module never loaded.
  const code = readFileSync(new URL('../hooks/register.js', import.meta.url), 'utf8').replace(/\/\/.*$/gm, '')
  const bad = [...code.matchAll(/(?<![\w$])\$\.\w+(?!\w)(?!\.\w+\()/g)].map(m => m[0])
  assert.deepEqual(bad, [], 'every $.noun is followed by .event( at its call site')
  assert.doesNotMatch(code, /(?<![\w$])\$\??\.\w+\?\./, 'no optional chaining on $ nouns')
  // The fixed path: the gate gets the running agents from $.agent.list() itself, called as a method.
  let calls = 0
  const agent = { list: async function () { calls++; assert.equal(this, agent); return [{ id: 'a7', status: 'running' }] } }
  const g = fakeGate({ decision: 'skip', reason: 'agent running: 1 (a7)' })
  const h = await harness(T0, { env: GATE_ENV, process: g.process, agent })
  await h.start()
  await h.advance(11 * MIN)
  assert.ok(calls >= 1, 'agent.list was called')
  const argv = g.calls.at(-1).argv
  assert.deepEqual(argv.slice(argv.indexOf('--running'), argv.indexOf('--running') + 2), ['--running', '1'])
  assert.equal(argv[argv.indexOf('--ids') + 1], 'a7')
  assert.equal(h.submitted.length, 0, 'the gate said skip')
  // A host whose agent.list answers no list: no agent info, the gate asks agent-watch, the beat still goes out.
  const g2 = fakeGate({ decision: 'wake', reason: 'x' })
  const h2 = await harness(T0, { env: GATE_ENV, process: g2.process, agent: { list: async () => undefined } })
  await h2.start()
  await h2.advance(11 * MIN)
  assert.ok(!g2.calls[0].argv.includes('--running'), 'non-list answer: the gate asks agent-watch')
  assert.equal(h2.submitted.length, 1)
})

test('gate: a finish the main session already saw (its task notification turn) is not news', async () => {
  const agents = [{ id: 'a1', status: 'running' }, { id: 'a2', status: 'running' }]
  const g = fakeGate({ decision: 'skip', reason: 'agent running' })
  const h = await harness(T0, { env: GATE_ENV, process: g.process, agent: { list: async () => agents } })
  await h.start()
  await h.advance(11 * MIN)                          // first consult: snapshot a1, a2 running
  agents[0].status = 'completed'
  await h.turn('<task-notification>a1 completed</task-notification>')   // the notification turn refreshes the snapshot
  await h.advance(11 * MIN)
  const argv = g.calls.at(-1).argv
  assert.deepEqual(argv.slice(argv.indexOf('--running'), argv.indexOf('--running') + 4), ['--running', '1', '--finished', '0'])
  agents[1].status = 'failed'                        // a finish with no main turn since: news
  await h.advance(11 * MIN)
  const last = g.calls.at(-1).argv
  assert.equal(last[last.indexOf('--finished') + 1], '1')
})
