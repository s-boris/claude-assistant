// heartbeat: in-process heartbeat for the main session. Replaces typing into tmux; the cron heartbeat
// (/etc/cron.d/heartbeat: `heartbeat 10` every 5 min) stays as the automatic fallback.
//
// Submits one "[heartbeat] ..." prompt once the main session has been idle HEARTBEAT_MINUTES (10) min,
// or 2 min when new notes landed in ~/inbox. Idle means: no main turn running, none started or ended
// within the threshold, and no transcript in the project dir (main or subagent) written within it,
// which is the cron heartbeat's own test. $.prompt.submit only starts a turn once the session is idle,
// so it never answers a permission prompt or dialog, and the mod doesn't even call it during a turn.
//
// No double beats with cron:
//  - it never fires in cron's run window, :x4:50 to :x5:45 (x5 = minutes divisible by 5; cron is done
//    within ~2 s of :x5:00);
//  - a beat from either side starts a turn: that writes the transcript (cron then skips for 10 min)
//    and raises turn.start here (the mod then waits a full threshold again);
//  - both use /tmp/heartbeat as "last beat sent" (cron, ~/bin/restart's waker and this mod touch it),
//    and the mod keeps >= 5 min after any beat.
//
// Loads only in the main session: enabled solely through start-claude's --settings, and inert unless
// the session is interactive and runs in /home/node/workspace.
// Idle backoff (0.2.0, 2026-10-13; the owner got ~15 empty beats a night): a heartbeat turn is empty when its
// beat had no new notes and the turn ended with an answer after at most QUICK_TOOLS tool calls, none of them
// real work (Agent/Task, a Telegram reply), with no other prompt or delivery arriving meanwhile. From the
// BACKOFF_AFTER-th empty beat in a row the plain idle threshold doubles per empty beat (10, 10, 20, 40, ...)
// up to 60 min, 120 min in quiet hours (00:00-08:00 Europe/Zurich), never below HEARTBEAT_MINUTES. Back to
// the base at once on any other prompt (owner, Telegram, task notification), a non-empty heartbeat turn, or
// subagent transcript activity since the last heartbeat turn; new inbox notes still wake after INBOX_MIN.
// No new beat while the last one is unanswered (submitted, but no main turn has ended since: e.g. it sits
// queued behind a dialog), for up to UNANSWERED_MAX_MIN.
// The cron fallback follows the backoff through the state file: ~/.local/bin/heartbeat (ops/heartbeat-cron)
// shadows /usr/local/bin/heartbeat and, while lastTick is < 3 min old, uses idleMin, skips while unanswered and
// until nextDue + 3 min (else it raced this mod's cron-window deferral), unless this mod's beats stop landing.
// Status for humans and scripts: ~/.local/state/heartbeat-mod.json (lastTick about once a minute, beats,
// streak, idleMin = current plain threshold, capMin, unanswered, nextDue).
// Gate (0.3.0, 2026-10-14, owner OK; Hermes "wakeAgent:false"): once a beat is due, the mod first asks the token-free
// ~/bin/hb-gate (source ops/hb-gate) `wake` or `skip`, passing the new notes it counted, its last beat, --first for its
// first beat in this session and the background agents from $.agent.list() (running; finished since the last consult
// or main turn end, since a task notification already starts a turn). The gate wakes for inbox notes, a Due item whose
// time has come, a finished agent, the first beat, >= 3 h since the last beat, or no agent running with startable work;
// it skips while an agent is running (the common wasted beat) or nothing is startable. A skip sends nothing: it counts
// in the state file (skips, lastSkip, lastGate), never in the backoff (streak unchanged: no turn ran, so it says
// nothing about whether beats are empty), and restarts the plain idle clock, so the next check comes a full idle
// threshold later and nextDue moves with it (the cron fallback keeps holding). New notes still wake after INBOX_MIN.
// Fails open: a gate error, timeout or unreadable answer means wake (and lastError). HEARTBEAT_GATE=off disables it.
// HEARTBEAT_MOD_TEST_DIR (tests only, see test/): a minute becomes 6 s, inbox/marker/state move into
// that dir, any cwd is accepted, the cron-window guard is off and the prompt asks for a one-word reply.

export const INBOX_MIN = 2, GAP_MIN = 5
export const BACKOFF_AFTER = 2, CAP_DAY_MIN = 60, CAP_NIGHT_MIN = 120, QUIET_END_H = 8
export const QUICK_TOOLS = 2, UNANSWERED_MAX_MIN = 120
export const GATE = '/home/node/bin/hb-gate', GATE_TIMEOUT_MS = 15_000
const AGENT_ACTIVE = new Set(['pending', 'running', 'waiting'])
const WORK_TOOL = /^(Agent|Task)$|__(reply|send_poll|send_location)$/   // never "just a quick check"
// A beat: "[heartbeat]" early in the first line (cron and the restart waker type it bare; prompt.submit hooks see the
// mod's own bare too), or in the line after the host's frame, which is what turn.start gets for the mod's beat
// (Claude Code 2.1.296: "The heartbeat plugin sent a message:\n[heartbeat] ...\n\nThis is how Claude Code surfaces
// ..."). 0.2.0 expected frame and beat on one line, so no mod beat ever counted and the backoff never engaged.
const BEAT_TEXT = /^(?:[^\n]{0,40}\bheartbeat plugin\b[^\n]{0,60}\n+)?[^\n]{0,80}\[heartbeat\]/

const MAIN_CWD = '/home/node/workspace'
const PROJECTS = '/home/node/.claude/projects'
const PROD = {
  minute: 60_000, guard: true, test: false,
  inbox: '/home/node/inbox', marker: '/tmp/heartbeat', state: '/home/node/.local/state/heartbeat-mod.json',
}

// Cron starts `heartbeat` at second 0 of every minute divisible by 5. UTC minutes are fine: every real
// time zone offset is a multiple of 5 minutes.
export function inCronWindow(ms) {
  const d = new Date(ms), m = d.getUTCMinutes() % 5, s = d.getUTCSeconds()
  return (m === 0 && s < 45) || (m === 4 && s >= 50)
}

// The whole decision, pure (unit-tested in test/logic.test.mjs). s: busy, pending, lastActivity (last main
// turn start/end), lastBeat (this mod's), marker (mtime of /tmp/heartbeat), notes (new inbox notes),
// newest (newest transcript mtime), lastSkip (the gate's last skip: restarts the plain idle clock). Times in ms.
// streak (empty heartbeat turns in a row) and unanswered (last beat not answered yet) drive the backoff.
export function decide(s, now, cfg) {
  if (s.busy) return { fire: false, why: 'turn running' }
  if (s.pending) return { fire: false, why: 'beat pending' }
  if (s.unanswered && now - s.lastBeat < UNANSWERED_MAX_MIN * cfg.minute) return { fire: false, why: 'unanswered' }
  if (cfg.guard && inCronWindow(now)) return { fire: false, why: 'cron window' }
  if (now - Math.max(s.lastBeat, s.marker) < GAP_MIN * cfg.minute) return { fire: false, why: 'gap' }
  const idleMin = s.notes > 0 ? INBOX_MIN : plainIdleMin(s.streak || 0, now, cfg.baseMin)
  if (now - Math.max(s.lastActivity, s.newest) < idleMin * cfg.minute) return { fire: false, why: 'not idle' }
  if (s.notes === 0 && now - (s.lastSkip || 0) < idleMin * cfg.minute) return { fire: false, why: 'gate skipped' }
  return { fire: true, idleMin, notes: s.notes }
}

let hourFmt = null
export function zurichHour(ms) {
  try {
    hourFmt ??= new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Zurich', hour: 'numeric', hourCycle: 'h23' })
    return Number(hourFmt.format(ms)) % 24
  } catch { return new Date(ms).getHours() }   // the container's TZ is Europe/Zurich too
}

export const capMin = now => (zurichHour(now) < QUIET_END_H ? CAP_NIGHT_MIN : CAP_DAY_MIN)

// The idle threshold without new notes after `streak` empty heartbeat turns in a row.
export function plainIdleMin(streak, now, base) {
  const steps = Math.min(10, Math.max(0, streak - BACKOFF_AFTER + 1))
  return Math.max(base, Math.min(capMin(now), base * 2 ** steps))
}

// Turn bookkeeping, pure on a state object (the mod's own `st`, or a test's).
export const isBeatText = text => BEAT_TEXT.test(String(text ?? ''))
export function beatNotes(text) {   // -1 = not a routine beat (e.g. the restart waker's): never empty
  const m = /\b(\d+) new note\(s\)/.exec(String(text ?? ''))
  return m ? Number(m[1]) : -1
}
// Returns true when the turn counts as a beat only through prompt.submit (a beat entered while idle, but turn.start's
// text no longer looks like one): the host's frame changed and BEAT_TEXT needs a look; the hook logs it.
export function turnStarted(s, text, now) {
  s.busy = true
  s.lastActivity = now
  const known = isBeatText(text), drift = !known && s.beatIn != null
  if (known || drift) { s.hb = { notes: known ? beatNotes(text) : s.beatIn, tools: 0, work: false, input: false }; s.hbStart = now }
  else { s.hb = null; s.streak = 0 }   // the owner, Telegram, a task notification, a continuation: news
  s.beatIn = null
  return drift
}
export function toolCalled(s, tool) {   // main loop only
  if (!s.hb) return
  s.hb.tools++
  if (WORK_TOOL.test(String(tool))) s.hb.work = true
}
export function inputArrived(s, text, midTurn = false) {   // prompt.submit: any prompt or delivery, also one queued mid-turn
  if (isBeatText(text)) { if (!midTurn) s.beatIn = beatNotes(text); return }   // entered while idle: its turn.start is next
  s.beatIn = null
  s.streak = 0
  if (s.hb) s.hb.input = true
}
export const emptyBeat = (hb, reason) =>
  reason === 'answer' && hb.notes === 0 && !hb.work && !hb.input && hb.tools <= QUICK_TOOLS
export function turnEnded(s, reason, now) {
  s.busy = false
  s.lastActivity = now
  s.unanswered = false   // the beat was consumed, also by an API error (else one error would hold inbox wakes for hours)
  if (s.hb) {
    if (emptyBeat(s.hb, reason)) s.streak = (s.streak || 0) + 1
    else if (reason === 'answer' || reason === 'aborted') s.streak = 0   // an API error or refusal says nothing
    s.hb = null
  }
}
export function subagentsWrote(s, sub) {   // background work since the last heartbeat turn started
  if (sub > (s.hbStart || 0)) s.streak = 0
}

// The gate's answer ($.process.run's result): skip only on exit 0 with a JSON line saying so; anything else wakes.
export function gateVerdict(r) {
  try {
    if (!r || r.exitCode !== 0) return { skip: false, reason: `gate error: exit ${r ? r.exitCode : 'none'}` }
    const j = JSON.parse(String(r.stdout ?? '').trim().split('\n').pop())
    const reason = String(j.reason ?? '').slice(0, 160)
    return j.decision === 'skip' ? { skip: true, reason } : { skip: false, reason: reason || 'wake' }
  } catch (err) { return { skip: false, reason: 'gate error: ' + String(err).slice(0, 120) } }
}

// Background agents from $.agent.list() against the last snapshot (id -> active): running ids, and the ones that
// finished since (were active, or new and already done; an active one that left the list ended too). snap null =
// no snapshot yet: nothing counts as finished.
export function agentDelta(snap, list) {
  const next = {}, running = [], finished = []
  for (const a of list || []) {
    if (!a || !a.id) continue
    const active = AGENT_ACTIVE.has(a.status)
    next[a.id] = active
    if (active) running.push(a.id)
    else if (snap && a.status !== 'idle' && (snap[a.id] === true || !(a.id in snap))) finished.push(a.id)
  }
  if (snap) for (const [id, active] of Object.entries(snap)) if (active && !(id in next)) finished.push(id)
  return { running, finished, snap: next }
}

// Inbox entries that count as new: regular files modified after `since` (like cron's find -newer).
export const countNew = (entries, since) => entries.filter(f => f.kind === 'file' && f.mtimeMs > since).length

export const projectDir = cwd => `${PROJECTS}/${cwd.replace(/[^A-Za-z0-9]/g, '-')}`

const st = { busy: false, pending: false, lastActivity: 0, lastBeat: 0, started: 0,
  streak: 0, unanswered: false, hb: null, hbStart: 0, beatIn: null,
  lastSkip: 0, sessionBeats: 0, agentSnap: null, lastGate: '' }
let cfg = null, timer = null, ticking = false, ticks = 0, beats = 0, skips = 0, lastError = '', lastShown = ''

const logErr = ($, msg) => { lastError = msg; try { $.ui.log(msg) } catch {} }

async function mtime($, path) {
  try { return (await $.fs.stat(path)).mtimeMs || 0 } catch { return 0 }
}

// Newest mtimes of the current main transcript and of its subagents' transcripts.
async function newestTranscripts($) {
  const t = { main: 0, sub: 0 }
  try {
    const top = (await $.fs.list(cfg.project)).filter(f => f.kind === 'file' && f.name.endsWith('.jsonl'))
    if (!top.length) return t
    const cur = top.reduce((a, b) => (b.mtimeMs > a.mtimeMs ? b : a))
    t.main = cur.mtimeMs
    try {
      for (const f of await $.fs.list(`${cfg.project}/${cur.name.slice(0, -6)}/subagents`))
        if (f.kind === 'file' && f.name.endsWith('.jsonl') && f.mtimeMs > t.sub) t.sub = f.mtimeMs
    } catch {}   // no subagents yet
  } catch (err) {   // ENOENT: no transcript written yet
    if (!String(err).includes('ENOENT')) logErr($, 'transcript check failed: ' + err)
  }
  return t
}

// null when the host has no agent.list, it throws, or it answers no list: the gate then asks agent-watch.
// Spelled as a plain call on purpose: the host's loader (CC 2.1.296+, also `claude plugin validate`) rejects the
// WHOLE module when a $ noun is read as a value (`typeof $.agent?.list` did that, so 0.3.0 never loaded).
// A host without the noun throws a TypeError here, which the catch turns into the fallback.
async function listAgents($) {
  try {
    const list = await $.agent.list()
    return Array.isArray(list) ? list : null
  } catch { return null }
}
async function refreshAgents($) {   // after a main turn: finishes it was told about (task notifications) are not news
  const list = await listAgents($)
  if (list) st.agentSnap = agentDelta(st.agentSnap, list).snap
}

async function consultGate($, d, marker) {
  const args = ['--json', '--notes', String(d.notes), '--last-beat', String(Math.max(st.lastBeat, marker))]
  if (!st.sessionBeats) args.push('--first')
  const list = await listAgents($)
  if (list) {
    const a = agentDelta(st.agentSnap, list)
    st.agentSnap = a.snap
    args.push('--running', String(a.running.length), '--finished', String(a.finished.length))
    if (a.running.length) args.push('--ids', a.running.join(','))
  }
  try { return gateVerdict(await $.process.run([cfg.gate, ...args], { timeoutMs: GATE_TIMEOUT_MS })) }
  catch (err) { return { skip: false, reason: 'gate error: ' + String(err).slice(0, 120) } }
}

async function writeState($, now) {
  try {
    const idleMin = plainIdleMin(st.streak, now, cfg.baseMin)
    const unanswered = st.unanswered && now - st.lastBeat < UNANSWERED_MAX_MIN * cfg.minute
    await $.fs.write(cfg.state, JSON.stringify({
      started: st.started, lastTick: now, lastBeat: st.lastBeat, beats, baseMin: cfg.baseMin, lastError,
      streak: st.streak, idleMin, capMin: capMin(now), unanswered,
      // Earliest next plain beat (ms; a later main/subagent transcript write or a new note moves it), null while held.
      // A gate skip restarts the idle clock, so the cron fallback keeps holding while the gate skips.
      nextDue: unanswered ? null
        : Math.max(Math.max(st.lastActivity, st.lastSkip) + idleMin * cfg.minute, st.lastBeat + GAP_MIN * cfg.minute),
      gate: Boolean(cfg.gate), skips, lastSkip: st.lastSkip, lastGate: st.lastGate,
    }) + '\n')
  } catch {}
}

async function tick($) {
  if (ticking) return
  ticking = true
  try {
    const now = await $.clock.now()
    // About once a minute, and soon after the backoff changed: shows the mod is alive, feeds the cron fallback.
    const shown = `${st.streak}/${st.unanswered}`
    if (++ticks % 4 === 1 || shown !== lastShown) { lastShown = shown; await writeState($, now) }
    // Cheapest checks first: with the most permissive values for what isn't read yet, can it fire at all?
    if (!decide({ ...st, marker: 0, notes: 1, newest: 0 }, now, cfg).fire) return
    const marker = await mtime($, cfg.marker)
    let notes = 0
    try { notes = countNew(await $.fs.list(cfg.inbox), Math.max(st.started, st.lastBeat, marker)) }
    catch (err) { logErr($, 'inbox check failed: ' + err) }
    const t = await newestTranscripts($)
    subagentsWrote(st, t.sub)
    const seen = { marker, notes, newest: Math.max(t.main, t.sub) }
    const d = decide({ ...st, ...seen }, now, cfg)
    if (!d.fire) return
    if (cfg.gate) {
      const g = await consultGate($, d, marker)
      st.lastGate = `${g.skip ? 'skip' : 'wake'}: ${g.reason}`
      if (g.reason.startsWith('gate error')) logErr($, g.reason)
      if (!decide({ ...st, ...seen }, await $.clock.now(), cfg).fire) return   // a turn may have started meanwhile
      if (g.skip) {   // nothing is sent; the next check comes a full idle threshold later (see decide)
        st.lastSkip = now
        skips++
        await writeState($, now)
        return
      }
    }
    st.pending = true
    st.unanswered = true   // until a main turn ends (or the submit is dropped)
    st.lastBeat = now
    st.sessionBeats++
    beats++
    try { await $.fs.write(cfg.marker, '') } catch {}   // cron's "last heartbeat sent" marker
    await writeState($, now)
    const text = `[heartbeat] Idle ${d.idleMin}+ min, ${d.notes} new note(s) in ~/inbox. Run your heartbeat routine.` +
      (cfg.test ? ' TEST ONLY: reply with the single word OK and use no tools.' : '')
    // Resolves once the prompt entered the session and its turn started (or it was queued behind one).
    Promise.resolve().then(() => $.prompt.submit({ text }))
      .then(r => { if (r && r.drop) { st.unanswered = false; st.beatIn = null; logErr($, 'beat dropped: ' + r.drop) } })
      .catch(err => { st.unanswered = false; st.beatIn = null; logErr($, 'submit failed: ' + err) })
      .finally(() => { st.pending = false })
  } catch (err) {
    logErr($, 'tick failed: ' + err)
  } finally {
    ticking = false
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    try {
      if (timer) { timer.cancel(); timer = null }
      const testDir = await $.env.get('HEARTBEAT_MOD_TEST_DIR')
      const base = testDir
        ? { minute: 6_000, guard: false, test: true, inbox: `${testDir}/inbox`, marker: `${testDir}/marker`, state: `${testDir}/state.json` }
        : PROD
      if (e.isInteractive && (testDir || e.cwd === MAIN_CWD)) {
        const gate = (await $.env.get('HEARTBEAT_GATE')) || (testDir ? 'off' : GATE)   // tests opt in
        cfg = { ...base, project: projectDir(e.cwd), gate: gate === 'off' ? null : gate,
          baseMin: Math.max(3, Number(await $.env.get('HEARTBEAT_MINUTES')) || 10) }
        const now = await $.clock.now()
        Object.assign(st, { busy: false, pending: false, lastActivity: now, started: now,
          streak: 0, unanswered: false, hb: null, hbStart: 0, beatIn: null,
          lastSkip: 0, sessionBeats: 0, agentSnap: null, lastGate: '' })
        await writeState($, now)
        timer = $.clock.every(cfg.minute / 4, () => tick($))
      }
    } catch (err) { logErr($, 'start failed: ' + err) }
    return next(e)
  })
  on('session.end', async ($, e, next) => {
    if (timer) { timer.cancel(); timer = null }
    return next(e)
  })
  // The bookkeeping hooks only observe: every one passes its event on unchanged, whatever happens.
  // turn.start's text is the user message as the model reads it: the mod's own beat arrives framed (see BEAT_TEXT).
  on('turn.start', async ($, e, next) => {
    if (e.agentId) return next(e)   // CC >= 2.1.299 raises turn.start for subagent turns too; only the main loop counts
    try {
      if (turnStarted(st, e.text, await $.clock.now()))
        logErr($, 'beat counted via prompt.submit; turn.start text not recognized (host frame changed?): ' +
          JSON.stringify(String(e.text ?? '').slice(0, 60)))
    } catch { st.busy = true }
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    if (!e.agentId) {   // a subagent's turns carry agentId; their transcripts count via newestTranscripts
      try { turnEnded(st, e.reason, await $.clock.now()) } catch { st.busy = false }
      if (cfg?.gate) refreshAgents($).catch(() => {})   // not awaited: the turn's end never waits on it
    }
    return next(e)
  })
  on('tool.call', async ($, e, next) => {
    try { if (!e.agentId) toolCalled(st, e.tool) } catch {}
    return next(e)
  })
  // prompt.submit sees every prompt bare (the host frames a plugin's prompt after these hooks), the mod's own beat
  // included (origin plugin "heartbeat", no turnId: it enters once idle and its turn.start follows).
  on('prompt.submit', async ($, e, next) => {
    try { inputArrived(st, e.text, Boolean(e.turnId)) } catch {}
    return next(e)
  })
}
