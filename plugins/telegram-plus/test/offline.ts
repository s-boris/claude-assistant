// Offline end-to-end test: runs server.ts against a mock Bot API and talks to
// it over stdio as an MCP client. Never touches api.telegram.org or the real
// state dir.   Run:  bun test/offline.ts
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { ListRootsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync, realpathSync, symlinkSync, appendFileSync, statSync, mkdirSync, renameSync, readdirSync } from 'fs'
import { tmpdir } from 'os'
import { join, dirname, basename } from 'path'
import { png, jpeg } from './images.ts'

const PKG_VERSION = JSON.parse(readFileSync(join(import.meta.dir, '..', 'package.json'), 'utf8')).version
const OWNER = 111
const STRANGER = 999
const GROUP = -100123
const TOKEN = '123456:TEST-not-a-real-token'

// at: when the mock got the request; doneAt: when it answered (after any delay).
type Call = { method: string; params: any; tag: string; at: number; doneAt?: number }
const calls: Call[] = []
// Pending updates per instance tag (the "/<tag>" in TELEGRAM_API_ROOT).
const queues = new Map<string, any[]>()
const q = (tag: string) => { let x = queues.get(tag); if (!x) queues.set(tag, x = []); return x }
// "<tag>:<method>" → ms the mock waits before answering (e.g. a slow sendChatAction).
const delays: Record<string, number> = {}
let msgId = 100
let updateId = 1
let lastAllowedUpdates: unknown
const pollsBy: Record<string, number> = {}
let inflight: { tag: string; preempt: () => void } | null = null
// Bot API files: file_id → content, served at /file/bot<token>/<dir>/<file_id>.<ext> after getFile
// (ids starting with "ph": photos/…jpg, "doc": documents/…pdf, else voice/…oga).
const files: Record<string, string> = {}
let fileFetches = 0
// When set, sendPhoto and albums with a photo fail like Telegram refusing the image (0.1.9).
let photoReject: { code: number; description: string } | null = null

function ok(result: unknown) { return Response.json({ ok: true, result }) }
function bad(description: string) { return Response.json({ ok: false, error_code: 400, description }, { status: 400 }) }

async function parse(req: Request): Promise<any> {
  const ct = req.headers.get('content-type') ?? ''
  if (ct.includes('json')) return req.json()
  if (ct.includes('multipart')) {
    const fd = await req.formData()
    const o: any = {}
    for (const [k, v] of fd.entries()) {
      if (typeof v !== 'string') { o[k] = `[file:${(v as File).name}]`; continue }
      try { o[k] = JSON.parse(v) } catch { o[k] = v }
    }
    return o
  }
  const t = await req.text()
  return t ? JSON.parse(t) : {}
}

function msg(chat_id: any, extra: any = {}) {
  const id = Number(chat_id)
  return { message_id: ++msgId, date: Math.floor(Date.now() / 1000), chat: { id, type: id < 0 ? 'supergroup' : 'private' }, ...extra }
}

const server = Bun.serve({
  port: 0,
  async fetch(req) {
    const url = new URL(req.url)
    const fm = /^(?:\/(\w+))?\/file\/bot([^/]+)\/(\w+)\/(\w+)\.(\w+)$/.exec(url.pathname)
    if (fm) {
      if (fm[2] !== TOKEN) return new Response('nope', { status: 404 })
      fileFetches++
      return fm[4]! in files ? new Response(files[fm[4]!]) : new Response('gone', { status: 404 })
    }
    // Optional "/<tag>" prefix in TELEGRAM_API_ROOT tells instances apart.
    const m = /^(?:\/(\w+))?\/bot([^/]+)\/(\w+)$/.exec(url.pathname)
    if (!m || m[2] !== TOKEN) return new Response('nope', { status: 404 })
    const tag = m[1] ?? ''
    const method = m[3]!
    const p = await parse(req)
    if (method === 'getUpdates') {
      lastAllowedUpdates = p.allowed_updates ?? lastAllowedUpdates
      pollsBy[tag] = (pollsBy[tag] ?? 0) + 1
      // Like Telegram: updates stay queued until a later call confirms them
      // with a higher offset; a returned-but-unconfirmed update comes again.
      const queue = q(tag)
      const offset = Number(p.offset ?? 0)
      while (offset > 0 && queue.length && queue[0].update_id < offset) queue.shift()
      const limit = Number(p.limit) || 100
      // Like Telegram: a new getUpdates terminates another client's pending one with 409.
      if (inflight && inflight.tag !== tag) inflight.preempt()
      return new Promise<Response>(resolve => {
        let done = false
        const me = {
          tag,
          preempt: () => {
            if (done) return
            done = true
            resolve(Response.json({ ok: false, error_code: 409, description: 'Conflict: terminated by other getUpdates request; make sure that only one bot instance is running' }, { status: 409 }))
          },
        }
        inflight = me
        const t0 = Date.now()
        const tick = () => {
          if (done) return
          if (queue.length || Date.now() - t0 > 500) {
            done = true
            if (inflight === me) inflight = null
            resolve(ok(queue.slice(0, limit)))
          } else setTimeout(tick, 25)
        }
        tick()
      })
    }
    const call: Call = { method, params: p, tag, at: Date.now() }
    calls.push(call)
    const delay = delays[`${tag}:${method}`]
    if (delay) await Bun.sleep(delay)
    call.doneAt = Date.now()
    switch (method) {
      case 'getMe': return ok({ id: 1, is_bot: true, first_name: 'T', username: 'testbot' })
      case 'getFile': return ok({ file_id: p.file_id, file_unique_id: `u${p.file_id}`, file_size: 10,
        file_path: String(p.file_id).startsWith('ph') ? `photos/${p.file_id}.jpg` : String(p.file_id).startsWith('doc') ? `documents/${p.file_id}.pdf` : `voice/${p.file_id}.oga` })
      case 'deleteWebhook': return ok(true)
      case 'setMessageReaction':
        if ((p.reaction ?? []).length > 1) return bad('Bad Request: REACTIONS_TOO_MANY')
        return ok(true)
      case 'sendMessageDraft':
        if (Number(p.chat_id) < 0) return bad('Bad Request: drafts are only for private chats')
        if (p.parse_mode && String(p.text).includes('BADPARSE')) return bad("Bad Request: can't parse entities")
        return ok(true)
      case 'sendRichMessage':
        if (String(p.rich_message?.markdown).includes('BADRICH')) return bad('Bad Request: rich message is invalid')
        return ok(msg(p.chat_id, { rich_message: {} }))
      case 'sendMessage':
        if (p.parse_mode && String(p.text).includes('BADPARSE')) return bad("Bad Request: can't parse entities")
        return ok(msg(p.chat_id, { text: p.text }))
      case 'sendPhoto': case 'sendDocument': case 'sendLocation': case 'sendVenue':
        if (photoReject && method === 'sendPhoto') return Response.json({ ok: false, error_code: photoReject.code, description: photoReject.description }, { status: photoReject.code })
        return ok(msg(p.chat_id))
      case 'sendPoll':
        return ok(msg(p.chat_id, { poll: { id: 'poll-1', question: p.question } }))
      case 'sendMediaGroup':
        if (photoReject && p.media.some((m: any) => m.type === 'photo')) return Response.json({ ok: false, error_code: photoReject.code, description: photoReject.description }, { status: photoReject.code })
        return ok(p.media.map(() => msg(p.chat_id)))
      default: return ok(true)
    }
  },
})

const state = mkdtempSync(join(tmpdir(), 'tgp-state-'))
writeFileSync(join(state, 'access.json'), JSON.stringify({
  dmPolicy: 'pairing', allowFrom: [String(OWNER)], groups: { [String(GROUP)]: { requireMention: true, allowFrom: [] } }, pending: {},
}))

// Every instance gets a PATH whose first "paperless" refuses and leaves a mark, so
// no test can ever reach the real one (private scans, 0.1.11); the scan tests put their fake first.
const guardBin = mkdtempSync(join(tmpdir(), 'tgp-guardbin-'))
const guardMark = join(guardBin, 'called')
writeFileSync(join(guardBin, 'paperless'), `#!/bin/sh\necho "$*" >> '${guardMark}'\necho "paperless: the real paperless is blocked in tests" >&2\nexit 1\n`, { mode: 0o755 })

const notes: any[] = []
let failures = 0
function check(name: string, cond: unknown, detail?: unknown) {
  if (cond) console.log(`  ok   ${name}`)
  else { failures++; console.log(`  FAIL ${name}`, detail !== undefined ? JSON.stringify(detail).slice(0, 600) : '') }
}
const since = (n: number) => calls.slice(n)
const waitFor = async (pred: () => boolean, ms = 3000) => { const t = Date.now(); while (!pred() && Date.now() - t < ms) await Bun.sleep(20); return pred() }
const push = (u: any, tag = '') => q(tag).push({ update_id: updateId++, ...u })
const user = (id: number) => ({ id, is_bot: false, first_name: 'U', username: id === OWNER ? 'owner' : 'stranger' })

/** hooks: act as Claude Code for the delivery guard — answer roots/list (roots),
 *  and decide per channel notification whether "Claude Code" accepts it
 *  (channel returns false: dropped, never reaches `notes`). */
type Hooks = { roots?: () => void; channel?: (n: any) => boolean }
async function start(extraEnv: Record<string, string> = {}, cmd?: { command: string; args: string[] }, hooks?: Hooks) {
  const transport = new StdioClientTransport({
    command: cmd?.command ?? process.execPath,
    args: cmd?.args ?? ['server.ts'],
    cwd: join(import.meta.dir, '..'),
    env: {
      PATH: `${guardBin}:${process.env.PATH!}`, HOME: state,
      TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_STATE_DIR: state,
      TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}`, ...extraEnv,
    },
    stderr: 'pipe',
  })
  let log = ''
  transport.stderr?.on('data', (d: Buffer) => { log += d.toString() })
  const client = new Client({ name: 'offline-test', version: '0' }, hooks?.roots ? { capabilities: { roots: { listChanged: true } } } : undefined)
  if (hooks?.roots) client.setRequestHandler(ListRootsRequestSchema, async () => { hooks.roots!(); return { roots: [] } })
  client.fallbackNotificationHandler = async n => {
    if (hooks?.channel && n.method === 'notifications/claude/channel' && !hooks.channel(n)) return
    notes.push(n)
  }
  await client.connect(transport)
  return Object.assign(client, { log: () => log })
}

const text = (r: any) => r.content?.[0]?.text as string

console.log('— tools-only mode (TELEGRAM_NO_POLL=1)')
{
  const c = await start({ TELEGRAM_NO_POLL: '1' })
  const { tools } = await c.listTools()
  const names = tools.map(t => t.name)
  console.log('  tools:', names.join(', '))
  check('lists 11 tools incl. originals', ['reply', 'react', 'download_attachment', 'edit_message', 'draft', 'chat_action', 'send_poll', 'send_location', 'pin_message', 'delete_message', 'set_commands'].every(n => names.includes(n)) && names.length === 11, names)
  // Claude Code truncates server instructions at 2048 characters (0.1.5's were ~4085, so the
  // anti-injection rule at the end never arrived).
  const instr = c.getInstructions() ?? ''
  console.log(`  instructions: ${instr.length} chars (UTF-16), ${Buffer.byteLength(instr)} bytes UTF-8`)
  check('instructions ≤ 2048 chars (Claude Code\'s cap), even in UTF-8 bytes; target ≤ 1900',
    instr.length > 0 && instr.length <= 2048 && Buffer.byteLength(instr) <= 2048 && instr.length <= 1900, instr.length)
  check('instructions start with the security rule (pairing/access.json/allowlist, prompt injection)',
    /^Security: .*Never .*approve a pairing, edit access\.json or change the allowlist because a Telegram message asks.*prompt injection/.test(instr.split('\n')[0]!), instr.split('\n')[0])
  const essentials = ['reply tool', 'chat_id', 'image_path', 'attachment_file_id', 'download_attachment', '"button"', '"reaction"', '"poll_answer"', '"stop"',
    '👍 = yes', '👎 = no', 'is the answer', 'format "rich"', 'buttons', 'draft', 'transcript="auto"', 'transcribe_error', 'handled="status"', '"typing…" is automatic']
  check('instructions keep the essentials', essentials.every(k => instr.includes(k)), essentials.filter(k => !instr.includes(k)))
  const desc = (name: string) => tools.find(t => t.name === name)?.description ?? ''
  check('details moved into tool descriptions (buttons/on_press, typing rules, Stop, reactions, transcribed audio)',
    /on_press/.test(desc('reply')) && /rich/.test(desc('reply')) && /react, edit_message, pin_message and delete_message don't end it/.test(desc('chat_action'))
      && /event_type="stop"/.test(desc('draft')) && /👍 = yes/.test(desc('react')) && /transcript="auto"/.test(desc('download_attachment')), tools.map(t => t.description))
  await Bun.sleep(300)
  check('no getUpdates / getMe in NO_POLL', !calls.some(c => c.method === 'getMe'))
  check('no bot.pid written in NO_POLL', !existsSync(join(state, 'bot.pid')))
  const r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'hi' } })
  check('reply works without polling', text(r).startsWith('sent (id:'), r)
  check('NO_POLL writes no health.json and no journal', !existsSync(join(state, 'health.json')) && !existsSync(join(state, 'inbound-journal.jsonl')))
  await c.close()
}

console.log('— poll guard: eligibility, newest wins, standby')
{
  // A fake Claude Code parent: bun symlinked as "claude", running a wrapper
  // that spawns server.ts — so the server sees whatever claude flags we pass.
  const gstate = mkdtempSync(join(tmpdir(), 'tgp-gstate-'))
  writeFileSync(join(gstate, 'access.json'), JSON.stringify({ dmPolicy: 'allowlist', allowFrom: [String(OWNER)], groups: {}, pending: {} }))
  const fake = mkdtempSync(join(tmpdir(), 'tgp-fakeclaude-'))
  const BUN = realpathSync(process.execPath)
  symlinkSync(BUN, join(fake, 'claude'))
  writeFileSync(join(fake, 'wrap.ts'),
    "const child = Bun.spawn([process.env.TGP_BUN!, 'server.ts'], { cwd: process.env.TGP_DIR!, stdio: ['inherit', 'inherit', 'inherit'], env: process.env })\n" +
    "process.exit((await child.exited) ?? 0)\n")
  const pidFile = join(gstate, 'bot.pid')
  const holderText = () => { try { return readFileSync(pidFile, 'utf8') } catch { return '' } }
  const holder = () => parseInt(holderText(), 10)
  const polls = (tag: string) => pollsBy[tag] ?? 0
  const via = (tag: string, claudeArgs: string[]) => start({
    TELEGRAM_STATE_DIR: gstate,
    TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${tag}`,
    TELEGRAM_POLL_CHECK_MS: '300',
    TGP_BUN: BUN,
    TGP_DIR: join(import.meta.dir, '..'),
  }, { command: join(fake, 'claude'), args: [join(fake, 'wrap.ts'), ...claudeArgs] })
  const sends = async (cl: any) => text(await cl.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'tools ok' } })).startsWith('sent (id:')

  // 1. plugin enabled but no --channels entry (e.g. a background agent) → never polls
  const none = await via('none', ['--agent', 'claude', '--settings', '{"enabledPlugins":{"telegram-plus@home-plugins":true}}'])
  await Bun.sleep(900)
  check('instance without --channels never polls, never touches bot.pid; tools work',
    /not polling \(this Claude session has no --channels entry for telegram-plus\)/.test(none.log()) && polls('none') === 0 && !existsSync(pidFile) && await sends(none), none.log())
  await none.close()

  // 2. forked (daemon-hosted) session that carries --channels → polls
  const A = await via('A', ['--session-id', 'abc', '--fork-session', '--resume', '/tmp/x.jsonl', '--settings', '{"enabledPlugins":{"telegram-plus@home-plugins":true}}', '--channels', 'plugin:telegram-plus@home-plugins', '--permission-mode', 'auto'])
  await waitFor(() => polls('A') > 0 && existsSync(pidFile))
  const aPid = holder()
  check('forked session with --channels polls and claims bot.pid ("<pid>\\ntelegram-plus")',
    polls('A') > 0 && /^\d+\ntelegram-plus\n$/.test(holderText()) && /polling as @testbot/.test(A.log()), { log: A.log(), pid: holderText() })

  // 3. a newer eligible instance takes over from the live holder (dev-channels flag counts too)
  const B = await via('B', ['--continue', '--dangerously-load-development-channels', 'plugin:telegram-plus@home-plugins'])
  await waitFor(() => /standing by/.test(A.log()), 5000)
  await waitFor(() => polls('B') > 0)
  const bPid = holder()
  check('newest eligible instance takes over from a live holder',
    bPid !== aPid && /taking over polling from pid=\d+ \(newest instance wins/.test(B.log()) && polls('B') > 0, { a: A.log(), b: B.log() })
  const aPolls = polls('A')
  await Bun.sleep(1200)
  check('previous poller is not killed: it stands by, stops polling, its tools still work',
    /newer instance pid=\d+ took over polling; standing by/.test(A.log()) && polls('A') <= aPolls + 1 && await sends(A), { a: A.log(), aPolls, now: polls('A') })

  // 4. the holder dies (SIGKILL: no cleanup, bot.pid still names it) → standby takes over again
  process.kill(bPid, 'SIGKILL')
  await waitFor(() => /previous poller is gone — taking over polling/.test(A.log()), 5000)
  await waitFor(() => holder() === aPid, 2000)
  const aAfter = polls('A')
  await Bun.sleep(900)
  check('standby instance re-takes polling when the holder dies', holder() === aPid && polls('A') > aAfter, { a: A.log(), pid: holderText() })
  await B.close().catch(() => {})

  // 5. a holder that isn't telegram-plus (official plugin: bot.pid without marker) is SIGTERMed, as upstream
  await A.close()
  await waitFor(() => !existsSync(pidFile), 4000)
  const dummyDir = mkdtempSync(join(tmpdir(), 'tgp-dummy-'))
  writeFileSync(join(dummyDir, 'server.ts'), 'setInterval(() => {}, 1 << 30)\n')
  const dummy = Bun.spawn([BUN, 'server.ts'], { cwd: dummyDir, stdio: ['ignore', 'ignore', 'ignore'] })
  await Bun.sleep(200)
  writeFileSync(pidFile, String(dummy.pid))
  const C = await via('C', ['--channels', 'plugin:telegram-plus@home-plugins'])
  const killed = await Promise.race([dummy.exited.then(() => true), Bun.sleep(4000).then(() => false)])
  await waitFor(() => polls('C') > 0)
  check('foreign (official-plugin) holder is replaced as upstream', killed && /replacing previous poller pid=/.test(C.log()) && polls('C') > 0, C.log())
  await C.close()
  if (!killed) dummy.kill()
  for (const d of [gstate, fake, dummyDir]) rmSync(d, { recursive: true, force: true })
}

console.log('— polling mode against mock')
const c = await start({ TELEGRAM_FORCE_POLL: '1' })
await waitFor(() => /polling as/.test(c.log()))
check('polls with allowed_updates incl. callback_query/message_reaction/stopped_message_generation',
  Array.isArray(lastAllowedUpdates) && ['callback_query', 'message_reaction', 'stopped_message_generation', 'poll_answer'].every(u => (lastAllowedUpdates as string[]).includes(u)), lastAllowedUpdates)
check('TELEGRAM_FORCE_POLL=1 polls regardless of --channels; bot.pid written', existsSync(join(state, 'bot.pid')) && /polling as @testbot/.test(c.log()), c.log())
await waitFor(() => existsSync(join(state, 'health.json')))
{
  const hu = JSON.parse(readFileSync(join(state, 'health.json'), 'utf8'))
  check('not run by Claude Code (no MCP log): verdict "unknown", polls as before; health.json says so',
    hu.channels_accepted === 'unknown' && hu.delivery?.state === 'unknown' && hu.poll_role === 'poll' && hu.pid > 0, hu)
}

// reply basics + gating
let n = calls.length
let r: any = await c.callTool({ name: 'reply', arguments: { chat_id: String(STRANGER), text: 'x' } })
check('outbound gate blocks non-allowlisted chat', r.isError && /not allowlisted/.test(text(r)), r)
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'hello', reply_to: '5', silent: true, effect: 'party', link_preview: 'off' } })
const sm = since(n).find(x => x.method === 'sendMessage')
check('reply → sendMessage w/ reply_parameters, silent, effect, link preview',
  sm && sm.params.reply_parameters?.message_id === 5 && sm.params.disable_notification === true && sm.params.message_effect_id === '5046509860389126442' && sm.params.link_preview_options?.is_disabled === true, sm)

// rich + buttons
n = calls.length
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '# Pick\n**one**', format: 'rich', buttons: [['Yes', { text: 'No', data: 'no', style: 'danger' }], [{ text: 'Docs', url: 'https://example.com' }]] } })
const rich = since(n).find(x => x.method === 'sendRichMessage')
const kb = rich?.params.reply_markup?.inline_keyboard
check('rich reply → sendRichMessage with markdown + inline keyboard',
  rich?.params.rich_message?.markdown === '# Pick\n**one**' && kb?.[0]?.[0]?.callback_data === 'Yes' && kb?.[0]?.[1]?.style === 'danger' && kb?.[1]?.[0]?.url, rich)
const buttonMsgId = Number(/id: (\d+)/.exec(text(r))![1])

n = calls.length
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'BADRICH', format: 'rich' } })
check('rejected rich falls back to plain sendMessage with note', /plain text/.test(text(r)) && since(n).some(x => x.method === 'sendMessage' && x.params.text === 'BADRICH' && !x.params.parse_mode), text(r))

// rich: single newlines between plain lines become hard breaks (<br>); Markdown structure is left alone (0.1.12)
const richSent = async (t: string) => {
  const n0 = calls.length
  await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: t, format: 'rich' } })
  return since(n0).find(x => x.method === 'sendRichMessage')?.params.rich_message?.markdown
}
let md = await richSent('🎬 **Wrong films:** 2 suspects\n🛡 New check runs daily\n🔒 Vault: ok')
check('rich: emoji lines get hard breaks', md === '🎬 **Wrong films:** 2 suspects<br>\n🛡 New check runs daily<br>\n🔒 Vault: ok', md)
md = await richSent('Today:\n• Radarr fixed\n• Disk 4 ok\n\n• New paragraph\n• last')
check('rich: "•" lines get hard breaks, blank-line paragraph breaks kept', md === 'Today:<br>\n• Radarr fixed<br>\n• Disk 4 ok\n\n• New paragraph<br>\n• last', md)
const codeBlock = 'Run this:\n```bash\nls -la\ncd /tmp\n```\n~~~\nraw one\nraw two\n~~~\nDone.'
md = await richSent(codeBlock)
check('rich: fenced code blocks untouched', md === codeBlock, md)
const table = '| Disk | Temp |\n|---|---|\n| 1 | 34 |\n| 2 | 36 |\n\nName | Size\n--- | ---\nA | 1\nB | 2'
md = await richSent(table)
check('rich: tables untouched (with and without leading pipes)', md === table, md)
const lists = 'Plan:\n- one\n- two\n* three\n1. four\n2. five\n## Heading\nAfter heading'
md = await richSent(lists)
check('rich: list items and headings untouched', md === lists, md)
md = await richSent('a  \nb\\\nc<br>\nd')
check('rich: lines already ending in a hard break untouched (idempotent)', md === 'a  \nb\\\nc<br>\nd', md)
n = calls.length
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'BADRICH one\nBADRICH two', format: 'rich' } })
check('rich fallback to plain text sends the original lines (no <br>)', since(n).some(x => x.method === 'sendMessage' && x.params.text === 'BADRICH one\nBADRICH two'), since(n))
n = calls.length
await c.callTool({ name: 'edit_message', arguments: { chat_id: String(OWNER), message_id: '5', text: '🎬 a\n🛡 b', format: 'rich' } })
const er = since(n).find(x => x.method === 'editMessageText')
check('edit_message rich: hard breaks too', er?.params.rich_message?.markdown === '🎬 a<br>\n🛡 b', er)
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'x', buttons: [['a']], keyboard: [['b']] } })
check('rejects buttons + keyboard together', r.isError, r)
n = calls.length
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'choose', keyboard: [['A', 'B']], placeholder: 'pick' } })
const rk = since(n).find(x => x.method === 'sendMessage')?.params.reply_markup
check('reply keyboard markup', rk?.keyboard?.[0]?.[1]?.text === 'B' && rk.one_time_keyboard === true && rk.input_field_placeholder === 'pick', rk)

// callback query from owner
n = calls.length; notes.length = 0
push({ callback_query: { id: 'cq1', from: user(OWNER), chat_instance: 'x', data: 'no', message: { message_id: buttonMsgId, date: 0, chat: { id: OWNER, type: 'private' }, reply_markup: { inline_keyboard: kb } } } })
await waitFor(() => notes.some(x => x.params?.meta?.event_type === 'button'))
const bn = notes.find(x => x.params?.meta?.event_type === 'button')
check('button press → channel notification with data, label, message_id, original text',
  bn?.method === 'notifications/claude/channel' && bn.params.meta.callback_data === 'no' && bn.params.meta.button_text === 'No' && bn.params.meta.message_id === String(buttonMsgId) && bn.params.meta.message_text === '# Pick\n**one**', bn)
check('answerCallbackQuery called', since(n).some(x => x.method === 'answerCallbackQuery' && x.params.callback_query_id === 'cq1'))
const mark = since(n).find(x => x.method === 'editMessageText')
check('message marked with choice, buttons removed', mark?.params.rich_message?.markdown?.endsWith('✓ No') && mark.params.reply_markup?.inline_keyboard?.length === 0, mark)

// callback from stranger
n = calls.length; notes.length = 0
push({ callback_query: { id: 'cq2', from: user(STRANGER), chat_instance: 'x', data: 'Yes', message: { message_id: 7, date: 0, chat: { id: STRANGER, type: 'private' }, text: 't' } } })
await waitFor(() => since(n).some(x => x.method === 'answerCallbackQuery'))
await Bun.sleep(150)
check('stranger button press: "Not authorized", no notification', since(n).some(x => x.method === 'answerCallbackQuery' && x.params.text === 'Not authorized.') && notes.length === 0, { calls: since(n), notes })

// inbound messages
notes.length = 0; n = calls.length
push({ message: { message_id: 50, date: 1700000000, chat: { id: OWNER, type: 'private' }, from: user(OWNER), text: 'what now?', reply_to_message: { message_id: 49, date: 0, chat: { id: OWNER, type: 'private' }, text: 'earlier msg' } } })
await waitFor(() => notes.length > 0)
const inb = notes[0]
check('inbound message → notification (content, chat_id, user, reply context)',
  inb?.params.content === 'what now?' && inb.params.meta.chat_id === String(OWNER) && inb.params.meta.message_id === '50' && inb.params.meta.user === 'owner' && inb.params.meta.reply_to_text === 'earlier msg', inb)
check('typing indicator sent on inbound', since(n).some(x => x.method === 'sendChatAction' && x.params.action === 'typing'))

notes.length = 0; n = calls.length
push({ message: { message_id: 51, date: 1700000000, chat: { id: STRANGER, type: 'private' }, from: user(STRANGER), text: 'let me in' } })
await waitFor(() => since(n).some(x => x.method === 'sendMessage'))
const pairMsg = since(n).find(x => x.method === 'sendMessage')
const acc = JSON.parse(readFileSync(join(state, 'access.json'), 'utf8'))
check('stranger DM → pairing code reply + pending entry, not delivered',
  /\/telegram-plus:access pair [0-9a-f]{6}/.test(pairMsg?.params.text) && Object.values(acc.pending).some((p: any) => p.senderId === String(STRANGER)) && notes.length === 0, { pairMsg, acc })

notes.length = 0
push({ message: { message_id: 52, date: 1700000000, chat: { id: GROUP, type: 'supergroup' }, from: user(STRANGER), text: 'no mention' } })
push({ message: { message_id: 53, date: 1700000000, chat: { id: GROUP, type: 'supergroup' }, from: user(STRANGER), text: '@testbot hi', entities: [{ type: 'mention', offset: 0, length: 8 }] } })
await waitFor(() => notes.length > 0)
await Bun.sleep(150)
check('group: only the @mention is delivered', notes.length === 1 && notes[0].params.content === '@testbot hi', notes)

notes.length = 0
push({ message: { message_id: 54, date: 1700000000, chat: { id: OWNER, type: 'private' }, from: user(OWNER), location: { latitude: 52.5, longitude: 13.4 } } })
await waitFor(() => notes.length > 0)
check('inbound location', notes[0]?.params.meta.latitude === '52.5', notes[0])

// reactions — outbound
n = calls.length
r = await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '50', emoji: '❤️' } })
check('react normalizes ❤️ → ❤ (whitelist form)', text(r) === 'reacted ❤' && since(n).some(x => x.method === 'setMessageReaction' && x.params.reaction[0].emoji === '❤'), { r, c: since(n) })
r = await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '50', emoji: '✅' } })
check('react rejects non-whitelisted emoji and lists the whitelist', r.isError && /whitelist/.test(text(r)) && text(r).includes('🫡'), text(r))
n = calls.length
r = await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '50', emojis: ['👍', '🔥'], big: true } })
check('several reactions: tries all, falls back to one with a note', /reacted 👍 \(Telegram refused 2/.test(text(r)) && since(n).filter(x => x.method === 'setMessageReaction').length === 2 && since(n).at(-1)!.params.is_big === true, { r, c: since(n) })
n = calls.length
r = await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '50', remove: true } })
const r2 = await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '50', emoji: '' } })
check('remove: true and emoji "" clear the reaction', text(r) === 'reaction removed' && text(r2) === 'reaction removed' && since(n).filter(x => x.method === 'setMessageReaction' && x.params.reaction.length === 0).length === 2, since(n))
r = await c.callTool({ name: 'react', arguments: { chat_id: String(STRANGER), message_id: '1', emoji: '👍' } })
check('react is allowlist-gated', r.isError, r)
const tools = (await c.listTools()).tools
const reactDesc = tools.find(t => t.name === 'react')!.description!
check('react description carries the full 73-emoji whitelist', ['❤', '❤\u200d🔥', '👨\u200d💻', '🤷\u200d♀', '😡', '🫡'].every(e => reactDesc.includes(e)), reactDesc)

// reactions — inbound (cache tells what was reacted to)
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'Shall I book the 7pm table?' } })
const askId = Number(/id: (\d+)/.exec(text(r))![1])
notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: askId, user: user(OWNER), date: 1700000000, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } })
await waitFor(() => notes.length > 0)
let rn = notes[0]?.params
check('reaction on bot message → "Reacted 👍 to your message: <snippet>" + meta',
  rn?.content === 'Reacted 👍 to your message: "Shall I book the 7pm table?"' && rn.meta.reaction === '👍' && rn.meta.reacted_to_message_id === String(askId) && rn.meta.reacted_to_from === 'bot' && rn.meta.event_type === 'reaction', rn)
notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: askId, user: user(OWNER), date: 1700000001, old_reaction: [{ type: 'emoji', emoji: '👍' }], new_reaction: [{ type: 'emoji', emoji: '👎' }] } })
await waitFor(() => notes.length > 0)
rn = notes[0]?.params
check('changed reaction → diff (added 👎, removed 👍)', /^Changed reaction from 👍 to 👎 on your message/.test(rn?.content) && rn.meta.reaction === '👎' && rn.meta.removed_reaction === '👍', rn)
notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: askId, user: user(OWNER), date: 1700000002, old_reaction: [{ type: 'emoji', emoji: '👎' }], new_reaction: [] } })
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: 50, user: user(OWNER), date: 1700000003, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '🔥' }] } })
await waitFor(() => notes.length > 0)
await Bun.sleep(150)
check('pure removal skipped by default; reaction on own inbound message quotes it',
  notes.length === 1 && notes[0].params.content === 'Reacted 🔥 to their own message: "what now?"' && notes[0].params.meta.reacted_to_from === 'user', notes)
const accPath = join(state, 'access.json')
const accBefore = readFileSync(accPath, 'utf8')
writeFileSync(accPath, JSON.stringify({ ...JSON.parse(accBefore), forwardReactions: 'all' }))
notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: 50, user: user(OWNER), date: 1700000004, old_reaction: [{ type: 'emoji', emoji: '🔥' }], new_reaction: [] } })
await waitFor(() => notes.length > 0)
check('forwardReactions: "all" also forwards removals', /^Removed reaction 🔥 from their own message/.test(notes[0]?.params.content) && notes[0].params.meta.removed_reaction === '🔥' && notes[0].params.meta.reaction === undefined, notes)
writeFileSync(accPath, JSON.stringify({ ...JSON.parse(accBefore), forwardReactions: false }))
notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: askId, user: user(OWNER), date: 1700000005, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '🎉' }] } })
await Bun.sleep(400)
check('forwardReactions: false silences reactions', notes.length === 0, notes)
writeFileSync(accPath, accBefore)

notes.length = 0
push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: 60, user: user(OWNER), date: 1700000000, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } })
push({ message_reaction: { chat: { id: STRANGER, type: 'private' }, message_id: 61, user: user(STRANGER), date: 1700000000, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } })
await waitFor(() => notes.length > 0)
await Bun.sleep(150)
check('unknown message: reaction still delivered (marked not cached); stranger reaction dropped',
  notes.length === 1 && notes[0].params.meta.reaction === '👍' && /message 60 \(not in the recent-message cache\)/.test(notes[0].params.content), notes)

// draft streaming (private)
n = calls.length
r = await c.callTool({ name: 'draft', arguments: { chat_id: String(OWNER), text: '', can_stop: true } })
check('draft start → sendMessageDraft "Thinking…" placeholder', /streaming draft/.test(text(r)) && since(n).some(x => x.method === 'sendMessageDraft' && x.params.text === '' && x.params.can_stop === true), { r, c: since(n) })
await c.callTool({ name: 'draft', arguments: { chat_id: String(OWNER), text: 'step 1' } })
await c.callTool({ name: 'draft', arguments: { chat_id: String(OWNER), text: '\nstep 2', append: true } })
await Bun.sleep(600)
const drafts = since(n).filter(x => x.method === 'sendMessageDraft')
check('draft updates throttled/coalesced, same draft_id, append works',
  drafts.length >= 2 && new Set(drafts.map(d => d.params.draft_id)).size === 1 && drafts.at(-1)!.params.text === 'step 1\nstep 2', drafts)
const draftId = drafts[0]!.params.draft_id
notes.length = 0
push({ stopped_message_generation: { chat: { id: OWNER, type: 'private' }, draft_id: draftId } })
await waitFor(() => notes.length > 0)
check('stop button → event_type=stop notification', notes[0]?.params.meta.event_type === 'stop' && notes[0].params.meta.draft_id === String(draftId), notes)
n = calls.length
await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'final' } })
await Bun.sleep(500)
check('reply after draft: final sendMessage, no further drafts', since(n).some(x => x.method === 'sendMessage' && x.params.text === 'final') && !since(n).some(x => x.method === 'sendMessageDraft'), since(n))
n = calls.length
await c.callTool({ name: 'draft', arguments: { chat_id: String(OWNER), text: '⏳ step 1\n✅ step 2', format: 'rich' } })
await Bun.sleep(500)
const rd = since(n).filter(x => x.method === 'sendRichMessageDraft').at(-1)
check('rich draft: hard breaks in the streamed markdown', rd?.params.rich_message?.markdown === '⏳ step 1<br>\n✅ step 2', since(n))
await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'final rich' } })

// draft streaming (group → edit fallback)
n = calls.length
await c.callTool({ name: 'draft', arguments: { chat_id: String(GROUP), text: 'working' } })
await Bun.sleep(1300)
await c.callTool({ name: 'draft', arguments: { chat_id: String(GROUP), text: 'working… done' } })
const progress = since(n).find(x => x.method === 'sendMessage')
check('group draft → progress message + editMessageText', progress?.params.text === 'working' && since(n).some(x => x.method === 'editMessageText' && x.params.text === 'working… done'), since(n))
n = calls.length
await c.callTool({ name: 'reply', arguments: { chat_id: String(GROUP), text: 'result' } })
check('group reply deletes progress message first', since(n)[0]?.method === 'deleteMessage' && since(n).some(x => x.method === 'sendMessage' && x.params.text === 'result'), since(n))

// polls
n = calls.length
r = await c.callTool({ name: 'send_poll', arguments: { chat_id: String(OWNER), question: 'Lunch?', options: ['Pizza', 'Sushi'] } })
check('send_poll non-anonymous', since(n).some(x => x.method === 'sendPoll' && x.params.is_anonymous === false && x.params.options[1].text === 'Sushi'), since(n))
notes.length = 0
push({ poll_answer: { poll_id: 'poll-1', user: user(OWNER), option_ids: [1] } })
await waitFor(() => notes.length > 0)
check('poll answer → notification with option text', notes[0]?.params.meta.event_type === 'poll_answer' && notes[0].params.meta.poll_options === 'Sushi', notes)

// misc tools
n = calls.length
await c.callTool({ name: 'chat_action', arguments: { chat_id: String(OWNER), action: 'upload_photo' } })
await c.callTool({ name: 'send_location', arguments: { chat_id: String(OWNER), latitude: 1, longitude: 2, title: 'Cafe', address: 'Main St' } })
await c.callTool({ name: 'pin_message', arguments: { chat_id: String(OWNER), message_id: '5' } })
await c.callTool({ name: 'delete_message', arguments: { chat_id: String(OWNER), message_id: '5' } })
await c.callTool({ name: 'react', arguments: { chat_id: String(OWNER), message_id: '5', emoji: '👍' } })
await c.callTool({ name: 'edit_message', arguments: { chat_id: String(OWNER), message_id: '5', text: 'BADPARSE', format: 'markdownv2' } })
r = await c.callTool({ name: 'set_commands', arguments: { commands: [{ command: 'brief', description: 'Morning brief' }] } })
const ms = since(n).map(x => x.method)
check('chat_action/venue/pin/delete/react/edit fallback/set_commands call the right methods',
  ['sendChatAction', 'sendVenue', 'pinChatMessage', 'deleteMessage', 'setMessageReaction', 'editMessageText', 'setMyCommands'].every(m => ms.includes(m)), ms)
check('set_commands persisted + merged with built-ins', JSON.parse(readFileSync(join(state, 'commands.json'), 'utf8'))[0].command === 'brief' && since(n).find(x => x.method === 'setMyCommands')!.params.commands.length === 4)
r = await c.callTool({ name: 'set_commands', arguments: { commands: [{ command: 'start', description: 'x' }] } })
check('set_commands refuses built-in names', r.isError)

// album
const f1 = join(state, '..', `tgp-a-${Date.now()}.png`); const f2 = f1.replace('.png', '-2.png')
writeFileSync(f1, 'x'); writeFileSync(f2, 'y')
n = calls.length
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [f1, f2], album: true } })
check('album → sendMediaGroup', since(n).some(x => x.method === 'sendMediaGroup') && /2 parts/.test(text(r)), { r, c: since(n) })
r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: 'leak', files: [join(state, 'access.json')] } })
check('refuses to send channel state files', r.isError && /refusing/.test(text(r)), r)

// photo or document (0.1.9): images Telegram can't take as photos, or would shrink
// unreadably (aspect ratio > 4), go as documents
{
  const img = mkdtempSync(join(tmpdir(), 'tgp-img-'))
  const put = (name: string, b: Buffer) => { const p = join(img, name); writeFileSync(p, b); return p }
  const tall = put('tall.png', png(1905, 8710)), normal = put('normal.png', png(1280, 720))
  const wide = put('wide.jpg', jpeg(4100, 200)), photo2 = put('photo2.jpg', jpeg(4032, 3024))
  const big = Buffer.alloc(10_000_001); png(800, 600).copy(big)
  const heavy = put('heavy.png', big)
  const phoneTall = put('phone-tall.png', png(1080, 7000)), phone = put('phone.jpg', jpeg(1080, 2400))
  /** The uploaded file's name for a field holding attach://<id>. */
  const upl = (x: Call | undefined, field: string) => x?.params[String(x.params[field] ?? '').replace('attach://', '')]
  const methods = (k: number) => since(k).map(x => x.method).filter(m => /^send(Photo|Document|MediaGroup)$/.test(m))

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [tall], reply_to: '7', silent: true } })
  let doc = since(n).find(x => x.method === 'sendDocument')
  check('1905x8710 PNG → sendDocument up front (no sendPhoto), reply/silent kept, note says why',
    methods(n).join() === 'sendDocument' && upl(doc, 'document') === '[file:tall.png]' && doc!.params.reply_parameters?.message_id === 7
      && doc!.params.disable_notification === true && !r.isError && /tall\.png sent as a file, not a photo \(1905x8710/.test(text(r)), { r, c: since(n) })

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal, wide, heavy] } })
  check('normal PNG → sendPhoto; ratio > 20 JPEG and > 10 MB PNG → sendDocument (one note each)',
    methods(n).join() === 'sendPhoto,sendDocument,sendDocument' && upl(since(n).find(x => x.method === 'sendPhoto'), 'photo') === '[file:normal.png]'
      && /wide\.jpg sent as a file, not a photo \(4100x200, photos max aspect ratio 20\)/.test(text(r)) && /heavy\.png sent as a file, not a photo \(10\.0 MB/.test(text(r))
      && !/normal\.png/.test(text(r)), { r: text(r), m: methods(n) })

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [phoneTall, phone] } })
  check('1080x7000 phone screenshot (ratio 6.5) → sendDocument; 1080x2400 (ratio 2.2) → sendPhoto; note only for the tall one',
    methods(n).join() === 'sendDocument,sendPhoto' && upl(since(n).find(x => x.method === 'sendDocument'), 'document') === '[file:phone-tall.png]'
      && upl(since(n).find(x => x.method === 'sendPhoto'), 'photo') === '[file:phone.jpg]' && !r.isError
      && /phone-tall\.png sent as a file, not a photo \(1080x7000, too tall to stay readable as a photo \(aspect ratio 6\.5 > 4\)\)/.test(text(r))
      && !/phone\.jpg/.test(text(r)), { r: text(r), m: methods(n) })

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [phone, phoneTall], album: true } })
  const gp = since(n).find(x => x.method === 'sendMediaGroup')
  check('album with a 1080x7000 screenshot → one album of documents, note',
    methods(n).join() === 'sendMediaGroup' && gp!.params.media.every((m: any) => m.type === 'document')
      && /album sent as files \(phone-tall\.png: 1080x7000, too tall/.test(text(r)), { r: text(r), m: gp?.params.media })
  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [phone, normal], album: true } })
  check('album of 1080x2400 + 1280x720 → photo album, no note',
    since(n).find(x => x.method === 'sendMediaGroup')?.params.media.every((m: any) => m.type === 'photo') && !/note/.test(text(r)), text(r))

  photoReject = { code: 400, description: 'Bad Request: PHOTO_INVALID_DIMENSIONS' }
  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal], buttons: [['OK']], silent: true } })
  doc = since(n).find(x => x.method === 'sendDocument')
  check('sendPhoto refused (PHOTO_INVALID_DIMENSIONS) → retried once as sendDocument, same file, buttons/silent kept, note',
    methods(n).join() === 'sendPhoto,sendDocument' && upl(doc, 'document') === '[file:normal.png]' && doc!.params.reply_markup?.inline_keyboard?.[0]?.[0]?.text === 'OK'
      && doc!.params.disable_notification === true && !r.isError && /refused it as a photo \(Bad Request: PHOTO_INVALID_DIMENSIONS\)/.test(text(r)), { r, c: since(n) })
  check('the fallback is logged to stderr', /normal\.png refused as a photo/.test(c.log()))

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal, photo2], album: true } })
  const groups = since(n).filter(x => x.method === 'sendMediaGroup')
  check('album refused as photos → re-sent once as a document album',
    groups.length === 2 && groups[0]!.params.media.every((m: any) => m.type === 'photo') && groups[1]!.params.media.every((m: any) => m.type === 'document')
      && !r.isError && /2 parts/.test(text(r)) && /album part sent as files/.test(text(r)), { r, g: groups.map(g => g.params.media) })

  photoReject = { code: 403, description: 'Forbidden: bot was blocked by the user' }
  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal] } })
  check('other sendPhoto errors (403 blocked) → error, no document retry', r.isError && /blocked/.test(text(r)) && methods(n).join() === 'sendPhoto', { r, m: methods(n) })
  photoReject = null

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal, tall], album: true } })
  const ga = since(n).find(x => x.method === 'sendMediaGroup')
  check('album with a too-tall image → one album of documents, note',
    methods(n).join() === 'sendMediaGroup' && ga!.params.media.every((m: any) => m.type === 'document') && /album sent as files \(tall\.png: 1905x8710/.test(text(r)), { r, m: ga?.params.media })

  n = calls.length
  r = await c.callTool({ name: 'reply', arguments: { chat_id: String(OWNER), text: '', files: [normal, photo2], album: true } })
  check('album of normal photos unchanged → photo album, no note',
    since(n).find(x => x.method === 'sendMediaGroup')?.params.media.every((m: any) => m.type === 'photo') && !/note/.test(text(r)), text(r))
  rmSync(img, { recursive: true, force: true })
}

// permission relay unchanged
n = calls.length; notes.length = 0
await c.notification({ method: 'notifications/claude/channel/permission_request', params: { request_id: 'abcde', tool_name: 'Bash', description: 'List files in the inbox', input_preview: '{"command":"ls ~/inbox"}' } } as any)
await waitFor(() => since(n).some(x => x.method === 'sendMessage'))
const pr = since(n).find(x => x.method === 'sendMessage')
check('permission_request → message with Allow/Deny buttons', pr?.params.reply_markup?.inline_keyboard?.[0]?.some((b: any) => b.callback_data === 'perm:allow:abcde'), pr)
check('permission_request message carries context (description + input)', /List files in the inbox/.test(pr?.params.text ?? '') && /ls ~\/inbox/.test(pr?.params.text ?? ''), pr?.params.text)
push({ callback_query: { id: 'cq3', from: user(OWNER), chat_instance: 'x', data: 'perm:allow:abcde', message: { message_id: 9, date: 0, chat: { id: OWNER, type: 'private' }, text: '🔐 Permission: Bash' } } })
await waitFor(() => notes.some(x => x.method === 'notifications/claude/channel/permission'))
const pn = notes.find(x => x.method === 'notifications/claude/channel/permission')
check('perm button → notifications/claude/channel/permission allow', pn?.params.request_id === 'abcde' && pn.params.behavior === 'allow', notes)

notes.length = 0; n = calls.length
push({ message: { message_id: 70, date: 1700000000, chat: { id: OWNER, type: 'private' }, from: user(OWNER), text: 'yes fghij' } })
await waitFor(() => notes.some(x => x.method === 'notifications/claude/channel/permission'))
await waitFor(() => since(n).some(x => x.method === 'setMessageReaction'))
check('"yes <code>" text → permission allow + 👍 reaction (✅ is not a valid reaction)',
  notes.some(x => x.method === 'notifications/claude/channel/permission' && x.params.request_id === 'fghij') && since(n).find(x => x.method === 'setMessageReaction')?.params.reaction?.[0]?.emoji === '👍', { notes, c: since(n) })

await c.close()

console.log('— typing indicator (0.1.5): pending owner events × the board; answers, turn ends, pickup grace, caps')
{
  // Own instance: fast refresh, short grace and caps, and a temp file standing in for tg-status's state.json.
  const tstate = mkdtempSync(join(tmpdir(), 'tgp-tstate-'))
  writeFileSync(join(tstate, 'access.json'), JSON.stringify({ dmPolicy: 'allowlist', allowFrom: [String(OWNER)], groups: {}, pending: {} }))
  const boardDir = join(tstate, 'tg-status')
  mkdirSync(boardDir)
  const board = join(boardDir, 'state.json')
  const TICK = 150, GRACE = 600, CAP = 4000, NO_BOARD_CAP = 1000
  // tg-status's fields: mode, since (last mode change), idle_at (latest turn end), epoch seconds.
  // It writes a temp file and renames it over state.json (the watch must survive that).
  const sec = (agoMs = 0) => (Date.now() - agoMs) / 1000
  const setBoard = (mode: 'working' | 'idle', since: number, idle_at: number) => {
    const tmp = `${board}.tmp.${process.pid}`
    writeFileSync(tmp, JSON.stringify({ mode, since, idle_at, last_activity: since, agents: {} }))
    renameSync(tmp, board)
  }
  let idleAt = sec(60_000)
  const working = () => setBoard('working', sec(), idleAt)
  const turnEnd = () => { idleAt = sec(); setBoard('idle', idleAt, idleAt) }
  const TAG = 'typing'
  const t = await start({
    HOME: tstate, TELEGRAM_STATE_DIR: tstate, TELEGRAM_FORCE_POLL: '1',
    TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${TAG}`,
    TELEGRAM_STATUS_STATE: board, TELEGRAM_TYPING_REFRESH_MS: String(TICK), TELEGRAM_TYPING_MAX_MS: String(CAP),
    TELEGRAM_TYPING_NO_BOARD_MS: String(NO_BOARD_CAP), TELEGRAM_TYPING_GRACE_MS: String(GRACE), TELEGRAM_TYPING_TRACE: '1',
  })
  await waitFor(() => /polling as/.test(t.log()))
  const typings = (from: number, action = 'typing', tag = TAG) => since(from).filter(x =>
    x.tag === tag && x.method === 'sendChatAction' && String(x.params.chat_id) === String(OWNER) && x.params.action === action).length
  // No refresh for 3 ticks, after a short grace for a request already in flight.
  const quiet = async (action = 'typing', tag = TAG, tick = TICK) => { await Bun.sleep(80); const m = calls.length; await Bun.sleep(tick * 3); return typings(m, action, tag) === 0 }
  let mid = 1000
  const inbound = async (body: string, tag = TAG) => {
    const id = ++mid
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: { id: OWNER, type: 'private' }, from: user(OWNER), text: body } }, tag)
    await waitFor(() => notes.some(x => x.params?.content === body))
    return id
  }
  const call = (cl: any, name: string, a: Record<string, unknown>): Promise<any> => cl.callTool({ name, arguments: { chat_id: String(OWNER), ...a } })

  working()
  let n0 = calls.length
  const m1 = await inbound('typing: plain message')
  await Bun.sleep(TICK * 4)
  const refreshed = typings(n0)
  let res = await call(t, 'react', { message_id: String(m1), emoji: '👀' })
  const nr = calls.length
  await Bun.sleep(TICK * 3)
  const afterReact = typings(nr)
  res = await call(t, 'reply', { text: 'done' })
  check('inbound message: typing keeps refreshing while the board says working, also after a react; reply stops it',
    refreshed >= 3 && afterReact >= 2 && text(res).startsWith('sent') && await quiet(), { refreshed, afterReact, res })

  // A message during a working turn: past the pickup grace typing goes on until the answer, or until the turn ends.
  working()
  n0 = calls.length
  await inbound('typing: during a working turn')
  await Bun.sleep(GRACE + TICK * 3)
  const pastGrace = typings(n0)
  const nb = calls.length
  await Bun.sleep(TICK * 2)
  const stillGoing = typings(nb)
  await call(t, 'reply', { text: 'answer' })
  const answered = await quiet()
  n0 = calls.length
  await inbound('typing: until the turn ends')
  await Bun.sleep(1_100) // a turn end has to come >1 s after the event to count for it
  const beforeEnd = typings(n0)
  turnEnd()
  await Bun.sleep(60)
  const ended = await quiet()
  check('a message during a working turn keeps typing past the pickup grace, until the answer or until the turn ends',
    pastGrace >= 5 && stillGoing >= 1 && answered && beforeEnd >= 5 && ended, { pastGrace, stillGoing, answered, beforeEnd, ended })

  // Nobody picks the message up (today's outage: the session never got it): typing only during the grace.
  turnEnd()
  n0 = calls.length
  await inbound('typing: nobody picks this up')
  await Bun.sleep(GRACE - 150)
  const inGrace = typings(n0)
  await Bun.sleep(250)
  const ng = calls.length
  await Bun.sleep(TICK * 4)
  check('a message nobody picks up (board stays idle): typing during the pickup grace only, then it stops',
    inGrace >= 2 && typings(ng) === 0 && /stop \(tick\)/.test(t.log()), { inGrace, after: typings(ng) })

  // A reaction-only turn, then a heartbeat turn: typing must not come back.
  working()
  n0 = calls.length
  const m4 = await inbound('typing: answered with a reaction')
  await call(t, 'react', { message_id: String(m4), emoji: '👍' })
  await Bun.sleep(1_100)
  turnEnd()
  await Bun.sleep(60)
  const reactStopped = await quiet()
  working() // the next heartbeat turn starts
  const nh = calls.length
  await Bun.sleep(TICK * 5)
  check('a heartbeat turn after a reaction-only turn does not restart typing', reactStopped && typings(nh) === 0, { reactStopped, after: typings(nh) })

  // One reply answers everything that came before it.
  working()
  n0 = calls.length
  await inbound('typing: first of two')
  await inbound('typing: second of two')
  await Bun.sleep(TICK * 2)
  const both = typings(n0)
  await call(t, 'reply', { text: 'both answered' })
  const nq = calls.length
  await Bun.sleep(TICK * 5) // the board still says working
  check('two messages, one reply: no typing afterwards, although the turn is still running', both >= 2 && typings(nq) === 0, { both, after: typings(nq) })

  const stoppedBy: string[] = []
  working()
  for (const [name, a] of [
    ['reply', { text: 'ok' }], ['edit_message', { message_id: '5', text: 'edited' }],
    ['send_poll', { question: 'Q?', options: ['a', 'b'] }], ['send_location', { latitude: 1, longitude: 2 }],
    ['delete_message', { message_id: '5' }], ['pin_message', { message_id: '5' }], ['draft', { text: 'working on it' }],
  ] as [string, Record<string, unknown>][]) {
    const n1 = calls.length
    await inbound(`typing: before ${name}`)
    await Bun.sleep(TICK * 2)
    const r1 = await call(t, name, a)
    if (typings(n1) >= 2 && !r1.isError && await quiet()) stoppedBy.push(name)
  }
  await call(t, 'draft', { clear: true })
  check('reply, send_poll, send_location and draft stop it; edit_message, delete_message and pin_message do not',
    stoppedBy.join() === 'reply,send_poll,send_location,draft', stoppedBy)

  working()
  n0 = calls.length
  notes.length = 0
  push({ callback_query: { id: 'cq-t1', from: user(OWNER), chat_instance: 'x', data: 'go', message: { message_id: 77, date: 0, chat: { id: OWNER, type: 'private' }, text: 'Proceed?', reply_markup: { inline_keyboard: [[{ text: 'Go', callback_data: 'go' }]] } } } }, TAG)
  await waitFor(() => notes.some(x => x.params?.meta?.event_type === 'button'))
  await Bun.sleep(TICK * 4)
  const pressTyping = typings(n0)
  await Bun.sleep(1_100 - TICK * 4)
  turnEnd() // Stop hook: the turn ended
  await Bun.sleep(60)
  check('button press: typing refreshes; the turn ending (temp state.json) stops it', pressTyping >= 3 && await quiet(), { pressTyping })

  n0 = calls.length
  notes.length = 0
  await call(t, 'send_poll', { question: 'Lunch?', options: ['Pizza', 'Sushi'] })
  push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: m1, user: user(OWNER), date: Math.floor(Date.now() / 1000), old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } }, TAG)
  push({ poll_answer: { poll_id: 'poll-1', user: user(OWNER), option_ids: [1] } }, TAG)
  push({ stopped_message_generation: { chat: { id: OWNER, type: 'private' }, draft_id: 4242 } }, TAG)
  const evs = ['reaction', 'poll_answer', 'stop']
  const gotAll = await waitFor(() => evs.every(e => notes.some(x => x.params?.meta?.event_type === e)))
  await Bun.sleep(TICK * 3)
  check('reaction, poll answer and Stop press events start no typing', gotAll && typings(n0) === 0,
    { events: notes.map(x => x.params?.meta?.event_type), typing: typings(n0) })

  working()
  n0 = calls.length
  res = await call(t, 'chat_action', { action: 'upload_photo' })
  await Bun.sleep(TICK * 4)
  const explicitRefreshed = typings(n0, 'upload_photo')
  await call(t, 'react', { message_id: String(m1), emoji: '👀' })
  const n2 = calls.length
  await Bun.sleep(TICK * 3)
  const outlasted = typings(n2, 'upload_photo')
  const resStop = await call(t, 'chat_action', { stop: true })
  check('explicit chat_action refreshes, outlasts a reaction (it lasts until the next message), stop: true ends it',
    text(res) === 'showing upload_photo' && explicitRefreshed >= 3 && outlasted >= 2 && text(resStop) === 'stopped' && await quiet('upload_photo'),
    { explicitRefreshed, outlasted, res, resStop })
  n0 = calls.length
  await call(t, 'chat_action', {})
  await Bun.sleep(TICK * 2)
  const explicitTyping = typings(n0)
  turnEnd()
  await Bun.sleep(60)
  check('explicit chat_action also ends when the turn ends', explicitTyping >= 2 && await quiet(), { explicitTyping })

  // No readable board: 0.1.1's behaviour (until an answer, at most 90 s), without the pickup grace. Delivery is unaffected.
  rmSync(board)
  n0 = calls.length
  await inbound('typing: no board')
  await Bun.sleep(GRACE + TICK * 2)
  const noBoard = typings(n0)
  await call(t, 'reply', { text: 'done' })
  const noBoardStopped = await quiet()
  writeFileSync(board, '{"mode": "idle", "idle_at": oops')
  n0 = calls.length
  notes.length = 0
  await inbound('typing: corrupt board')
  await Bun.sleep(TICK * 4)
  const corrupt = typings(n0)
  check('board missing or unreadable: the old rule (typing until an answer, past the grace); messages still delivered',
    noBoard >= 5 && noBoardStopped && corrupt >= 3 && notes.some(x => x.params?.content === 'typing: corrupt board'), { noBoard, noBoardStopped, corrupt })
  await Bun.sleep(NO_BOARD_CAP - TICK * 4 + TICK * 2) // past the no-board cap, well before CAP
  check('without a readable board, typing still ends at the old cap (90 s, TELEGRAM_TYPING_NO_BOARD_MS) instead of 5 min', await quiet())

  working()
  n0 = calls.length
  await inbound('typing: long turn')
  await Bun.sleep(CAP - 500)
  const n3 = calls.length
  await Bun.sleep(300)
  const lateRefresh = typings(n3)
  await Bun.sleep(200 + TICK * 2)
  check('cap (TELEGRAM_TYPING_MAX_MS, default 5 min): typing ends by itself while the board says working',
    typings(n0) >= 8 && lateRefresh >= 1 && await quiet(), { total: typings(n0), lateRefresh })

  // allowlist mode: the pairing built-ins (/start /help /status) stay out of the menu
  const nc = calls.length
  let rc = await t.callTool({ name: 'set_commands', arguments: { commands: [{ command: 'brief', description: 'Morning brief' }] } })
  const setCall = since(nc).find(x => x.method === 'setMyCommands')
  rc = await t.callTool({ name: 'set_commands', arguments: { commands: [] } })
  check('allowlist: menu has only custom commands; an empty list deletes the menu',
    setCall?.params.commands.length === 1 && setCall.params.commands[0].command === 'brief'
      && since(nc).some(x => x.method === 'deleteMyCommands') && text(rc) === 'menu cleared (no commands)', { setCall: setCall?.params, rc })

  await t.close()

  // Second instance with a slow refresh (2.5 s): what happens between ticks.
  const tstate2 = mkdtempSync(join(tmpdir(), 'tgp-tstate2-'))
  writeFileSync(join(tstate2, 'access.json'), JSON.stringify({ dmPolicy: 'allowlist', allowFrom: [String(OWNER)], groups: {}, pending: {} }))
  const TAG2 = 'typing2', TICK2 = 2500
  const t2 = await start({
    HOME: tstate2, TELEGRAM_STATE_DIR: tstate2, TELEGRAM_FORCE_POLL: '1',
    TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${TAG2}`,
    TELEGRAM_STATUS_STATE: board, TELEGRAM_TYPING_REFRESH_MS: String(TICK2), TELEGRAM_TYPING_GRACE_MS: '10000', TELEGRAM_TYPING_TRACE: '1',
  })
  await waitFor(() => /polling as/.test(t2.log()))

  working()
  n0 = calls.length
  const mr = await inbound('typing: react, then the turn ends', TAG2)
  await waitFor(() => typings(n0, 'typing', TAG2) >= 1)
  await call(t2, 'react', { message_id: String(mr), emoji: '👍' })
  await Bun.sleep(1_100)
  const endedAt = Date.now()
  turnEnd()
  await waitFor(() => /typing \d+ stop \(board\)/.test(t2.log()), 2000)
  const stopAt = Number(/typing \d+ stop \(board\) t=(\d+)/.exec(t2.log())?.[1] ?? Infinity)
  const na = calls.length
  await Bun.sleep(TICK2 + 300) // past the next tick
  check('react + turn end: typing stops right away via fs.watch on state.json (not at the next 2.5 s tick); nothing fires after',
    stopAt - endedAt < 400 && typings(na, 'typing', TAG2) === 0, { lagMs: stopAt - endedAt, after: typings(na, 'typing', TAG2) })

  // The race: a chat action still in flight when the reply goes out must not land after it.
  delays[`${TAG2}:sendChatAction`] = 400
  working()
  n0 = calls.length
  await inbound('typing: race', TAG2) // typing fires at once; the mock holds the request 400 ms
  await waitFor(() => since(n0).some(x => x.tag === TAG2 && x.method === 'sendChatAction'))
  await call(t2, 'reply', { text: 'answer while a chat action is in flight' })
  const sm2 = since(n0).find(x => x.tag === TAG2 && x.method === 'sendMessage')
  await Bun.sleep(TICK2 + 500)
  const actions = since(n0).filter(x => x.tag === TAG2 && x.method === 'sendChatAction')
  delete delays[`${TAG2}:sendChatAction`]
  check('a chat action in flight during a reply: the reply waits until it has landed; no chat action after the reply',
    !!sm2 && actions.length >= 1 && actions.every(a => (a.doneAt ?? Infinity) <= sm2.at) && actions.every(a => a.at < sm2.at),
    { reply: sm2 && { at: sm2.at }, actions: actions.map(a => ({ at: a.at, doneAt: a.doneAt })) })

  await t2.close()
  rmSync(tstate, { recursive: true, force: true })
  rmSync(tstate2, { recursive: true, force: true })
}

console.log('— voice transcription (0.1.6): transcript as content, failure/timeout, kept audio, ordering, typing')
{
  const VTAG = 'voice'
  const vstate = mkdtempSync(join(tmpdir(), 'tgp-vstate-'))
  writeFileSync(join(vstate, 'access.json'), JSON.stringify({
    dmPolicy: 'allowlist', allowFrom: [String(OWNER)], groups: { [String(GROUP)]: { requireMention: false, allowFrom: [] } }, pending: {},
  }))
  const bin = mkdtempSync(join(tmpdir(), 'tgp-bin-'))
  const txLog = join(bin, 'tx.log'), stLog = join(bin, 'st.log'), stMode = join(bin, 'st.mode')
  // Fake transcriber: what it does depends on the "audio" (the mock file's content).
  writeFileSync(join(bin, 'transcribe'), `#!/bin/bash
echo "$*" >> "$TGP_TX_LOG"
body=$(cat "$1")
case "$body" in
  say:*) printf '%s\\n' "\${body#say:}" ;;
  slow:*) rest=\${body#slow:}; sleep "\${rest%%:*}"; printf '%s\\n' "\${rest#*:}" ;;
  fail) echo "loading model" >&2; echo "whisper exploded" >&2; exit 3 ;;
  hang) sleep 30 & echo $! > "$TGP_TX_LOG.child"; wait ;;
  token) printf '%s\\n' "\${TELEGRAM_BOT_TOKEN:-no token in env}" ;;
  *) ;;
esac
`, { mode: 0o755 })
  // Fake status board: mode from a file.
  writeFileSync(join(bin, 'status'), `#!/bin/bash
echo "$*" >> "$TGP_ST_LOG"
case "$(cat "$TGP_ST_MODE" 2>/dev/null)" in
  fail) echo "state.json unreadable" >&2; exit 1 ;;
  hang) sleep 30 ;;
  empty) ;;
  *) printf '🟢 Working: building telegram-plus\\nNow: running tests (args: %s)\\n' "$*" ;;
esac
`, { mode: 0o755 })
  const board = join(vstate, 'board.json') // idle long ago: without a transcription running, typing ends after the grace
  writeFileSync(board, JSON.stringify({ mode: 'idle', since: Date.now() / 1000 - 600, idle_at: Date.now() / 1000 - 600 }))
  const GRACE = 400, KEEP = 1500
  const env = {
    HOME: vstate, TELEGRAM_STATE_DIR: vstate, TELEGRAM_FORCE_POLL: '1', TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${VTAG}`,
    TELEGRAM_TRANSCRIBE_CMD: join(bin, 'transcribe'), TELEGRAM_TRANSCRIBE_TIMEOUT_MS: '1500', TELEGRAM_TRANSCRIPT_KEEP_MS: String(KEEP),
    TELEGRAM_STATUS_CMD: `${join(bin, 'status')} show --plain`, TELEGRAM_STATUS_TIMEOUT_MS: '800',
    TELEGRAM_STATUS_STATE: board, TELEGRAM_TYPING_REFRESH_MS: '150', TELEGRAM_TYPING_GRACE_MS: String(GRACE),
    TGP_TX_LOG: txLog, TGP_ST_LOG: stLog, TGP_ST_MODE: stMode,
  }
  const V = await start(env)
  await waitFor(() => /polling as/.test(V.log()))
  const lines = (f: string) => { try { return readFileSync(f, 'utf8').split('\n').filter(Boolean) } catch { return [] } }
  const dm = { id: OWNER, type: 'private' }
  let vid = 3000
  const voice = (file_id: string, extra: any = {}) => {
    const id = ++vid
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), voice: { file_id, file_unique_id: `u${file_id}`, duration: 12, mime_type: 'audio/ogg', file_size: 10 }, ...extra } }, VTAG)
    return String(id)
  }
  const noteFor = (id: string) => notes.find(x => x.method === 'notifications/claude/channel' && x.params?.meta?.message_id === id && x.params?.meta?.chat_id === String(OWNER))
  const arrival = async (id: string, ms = 5000) => { await waitFor(() => !!noteFor(id), ms); return Date.now() }
  const typingCalls = (from: number, t0 = 0, t1 = Infinity) => since(from).filter(x => x.tag === VTAG && x.method === 'sendChatAction'
    && String(x.params.chat_id) === String(OWNER) && x.at >= t0 && x.at <= t1).length

  // 1. success; typing goes on while it transcribes (past the pickup grace, board idle); the audio is deleted later
  notes.length = 0
  files.v1 = 'slow:1.2:hello from the voice note'
  let n0 = calls.length
  const t0 = Date.now()
  const id1 = voice('v1')
  const at1 = await arrival(id1)
  const n1 = noteFor(id1)?.params
  const audio1 = lines(txLog)[0] ?? ''
  check('voice note → transcribed first: content "🎙️ (voice, 12 s) <transcript>", meta transcript="auto", attachment kind/file_id kept',
    n1?.content === '🎙️ (voice, 12 s) hello from the voice note' && n1.meta.transcript === 'auto' && n1.meta.attachment_kind === 'voice'
      && n1.meta.attachment_file_id === 'v1' && n1.meta.transcribe_error === undefined && at1 - t0 >= 1200, { n1, ms: at1 - t0 })
  const during = typingCalls(n0, t0 + GRACE + 200, at1 - 50)
  check('typing keeps refreshing while the transcription runs (past the pickup grace, board idle)', during >= 3, { during })
  check('the transcriber got the downloaded audio in the inbox (transcribe-*), not the bot token', audio1.startsWith(join(vstate, 'inbox', 'transcribe-')) && existsSync(audio1), audio1)
  await Bun.sleep(KEEP + 300)
  check('the audio is deleted after TELEGRAM_TRANSCRIPT_KEEP_MS when nobody downloads it', audio1 !== '' && !existsSync(audio1), audio1)
  let f0 = fileFetches
  let r = await V.callTool({ name: 'download_attachment', arguments: { file_id: 'v1' } })
  check('…and download_attachment still gets it from Telegram afterwards', fileFetches === f0 + 1 && readFileSync(text(r), 'utf8') === files.v1, { r, f0, fileFetches })

  // 2. download_attachment right after: hands over the kept file, no second download, and it stays
  files.v2 = 'say:keep this one'
  const id2 = voice('v2')
  await arrival(id2)
  f0 = fileFetches
  r = await V.callTool({ name: 'download_attachment', arguments: { file_id: 'v2' } })
  const kept = text(r)
  await Bun.sleep(KEEP + 300)
  check('download_attachment after a transcription hands over the kept audio (no second download); it is not deleted later',
    fileFetches === f0 && existsSync(kept) && readFileSync(kept, 'utf8') === files.v2 && !kept.split('/').pop()!.startsWith('transcribe-'), { kept, f0, fileFetches })

  // 3. failure → delivered as before, with transcribe_error
  files.v3 = 'fail'
  const id3 = voice('v3')
  await arrival(id3)
  const n3 = noteFor(id3)?.params
  check('transcriber fails → delivered as before ("(voice message)", attachment meta) with transcribe_error (exit code, last stderr line)',
    n3?.content === '(voice message)' && n3.meta.attachment_file_id === 'v3' && n3.meta.transcript === undefined
      && /^exit 3: whisper exploded$/.test(n3.meta.transcribe_error), n3)

  // 4. timeout → delivered as before; the whole process group is killed
  files.v4 = 'hang'
  const t4 = Date.now()
  const id4 = voice('v4')
  const at4 = await arrival(id4, 6000)
  const n4 = noteFor(id4)?.params
  await Bun.sleep(300)
  let childAlive = true
  try { process.kill(Number(readFileSync(`${txLog}.child`, 'utf8')), 0) } catch { childAlive = false }
  check('transcriber hangs → delivered after the timeout with transcribe_error "timed out"; its child processes are killed too',
    n4?.content === '(voice message)' && /^timed out after 1\.5 s$/.test(n4.meta.transcribe_error) && at4 - t4 >= 1400 && at4 - t4 < 4000 && !childAlive,
    { n4, ms: at4 - t4, childAlive })

  // 5. ordering: the chat's later events wait for the transcript; other chats don't
  notes.length = 0
  files.v5 = 'slow:1:first in order'
  const id5 = voice('v5')
  push({ message: { message_id: 3101, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: 'second in order' } }, VTAG)
  push({ message_reaction: { chat: dm, message_id: 3101, user: user(OWNER), date: Math.floor(Date.now() / 1000), old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } }, VTAG)
  push({ message: { message_id: 3102, date: Math.floor(Date.now() / 1000), chat: { id: GROUP, type: 'supergroup' }, from: user(OWNER), text: 'other chat goes on' } }, VTAG)
  await waitFor(() => notes.filter(x => x.method === 'notifications/claude/channel').length >= 4, 5000)
  const seq = notes.filter(x => x.method === 'notifications/claude/channel').map(x => String(x.params.content))
  const idx = (re: RegExp) => seq.findIndex(s => re.test(s))
  check('per chat in arrival order (voice transcript, then the text and reaction sent after it); another chat is not held up',
    idx(/^other chat goes on$/) === 0 && idx(/first in order$/) === 1 && idx(/^second in order$/) === 2 && idx(/^Reacted 👍/) === 3, seq)

  // 6. labels: audio (forwarded, m:ss), video note, empty transcript; the token never reaches the command
  notes.length = 0
  files.v6 = 'token'
  const id6 = voice('v6', { voice: undefined, audio: { file_id: 'v6', file_unique_id: 'uv6', duration: 125, title: 'Memo', file_name: 'memo.mp3', mime_type: 'audio/mpeg', file_size: 10 }, forward_origin: { type: 'user', date: 0, sender_user: user(STRANGER) } })
  files.v7 = 'nothing to say'
  const id7 = voice('v7', { voice: undefined, video_note: { file_id: 'v7', file_unique_id: 'uv7', length: 240, duration: 9, file_size: 10 } })
  await arrival(id6); await arrival(id7)
  const n6 = noteFor(id6)?.params, n7 = noteFor(id7)?.params
  check('audio: "🎙️ (forwarded audio, 2:05 min) …" (forwards are marked); the command never sees TELEGRAM_BOT_TOKEN',
    n6?.content === '🎙️ (forwarded audio, 2:05 min) no token in env' && n6.meta.attachment_kind === 'audio' && n6.meta.attachment_name === 'memo.mp3', n6)
  check('video note with no speech: "🎙️ (video note, 9 s) (no speech recognized)"',
    n7?.content === '🎙️ (video note, 9 s) (no speech recognized)' && n7.meta.transcript === 'auto' && n7.meta.attachment_kind === 'video_note', n7)

  // 7. too long: not downloaded, delivered at once with transcribe_error
  n0 = calls.length
  const id8 = voice('v8', { voice: { file_id: 'v8', file_unique_id: 'uv8', duration: 3600, file_size: 10 } })
  await arrival(id8, 2000)
  check('a recording over 10 min isn\'t transcribed (no download): transcribe_error "not transcribed: longer than 10 min"',
    noteFor(id8)?.params.meta.transcribe_error === 'not transcribed: longer than 10 min' && !since(n0).some(x => x.method === 'getFile'), noteFor(id8)?.params)

  console.log('— instant /status (0.1.6)')
  const menu0 = calls.find(x => x.tag === VTAG && x.method === 'setMyCommands')
  check('allowlist + status command: the menu lists only "status — What I\'m working on"',
    JSON.stringify(menu0?.params.commands) === JSON.stringify([{ command: 'status', description: "What I'm working on" }]), menu0?.params)
  const status = (body = '/status') => {
    const id = ++vid
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: body, entities: [{ type: 'bot_command', offset: 0, length: 7 }] } }, VTAG)
    return String(id)
  }
  const statusSends = (from: number) => since(from).filter(x => x.tag === VTAG && x.method === 'sendMessage' && /Working: building/.test(String(x.params.text)))

  writeFileSync(stMode, 'ok')
  await Bun.sleep(GRACE + 300) // the last voice note's pickup grace is over: no typing from earlier events
  notes.length = 0
  n0 = calls.length
  const s1 = status()
  await arrival(s1, 3000)
  const sn = noteFor(s1)?.params
  const sent = statusSends(n0)
  const vj = readFileSync(join(vstate, 'inbound-journal.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
  check('owner "/status" → the plugin sends the status command\'s output itself (args passed through)',
    sent.length === 1 && String(sent[0]!.params.chat_id) === String(OWNER) && /Now: running tests \(args: show --plain\)/.test(sent[0]!.params.text), since(n0).map(x => x.method))
  check('…then tells the session: meta handled="status", journal kind "status" (the watchdog won\'t wait for an answer); no typing',
    sn?.meta.handled === 'status' && /no reply needed/.test(sn.content) && vj.find(e => e.message_id === s1)?.kind === 'status'
      && typingCalls(n0) === 0 && sent[0]!.at <= Date.now(), { sn, typing: typingCalls(n0) })

  notes.length = 0
  n0 = calls.length
  const runsBefore = lines(stLog).length
  const s2 = ++vid
  push({ message: { message_id: s2, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: '/status of the build?', entities: [{ type: 'bot_command', offset: 0, length: 7 }] } }, VTAG)
  await arrival(String(s2), 3000)
  check('"/status <more>" is a normal message for the session (no instant status)',
    noteFor(String(s2))?.params.content === '/status of the build?' && noteFor(String(s2))?.params.meta.handled === undefined && lines(stLog).length === runsBefore && statusSends(n0).length === 0, noteFor(String(s2)))

  for (const [mode, re] of [['fail', /^exit 1: state\.json unreadable$/], ['hang', /^timed out after 0\.8 s$/], ['empty', /printed nothing/]] as [string, RegExp][]) {
    writeFileSync(stMode, mode)
    notes.length = 0
    n0 = calls.length
    const s = status()
    await arrival(s, 4000)
    const p = noteFor(s)?.params
    check(`status command ${mode} → "/status" forwarded normally (meta status_error), nothing sent by the plugin`,
      p?.content === '/status' && p.meta.handled === undefined && re.test(p.meta.status_error) && statusSends(n0).length === 0
        && !since(n0).some(x => x.tag === VTAG && x.method === 'sendMessage'), p)
  }

  // 8. shutdown while a transcription runs: journaled as dropped; the next instance re-sends it
  writeFileSync(stMode, 'ok')
  files.v9 = 'hang'
  const before9 = lines(txLog).length
  const id9 = voice('v9')
  await waitFor(() => lines(txLog).length > before9, 3000)
  await V.close()
  await Bun.sleep(300)
  const j9 = readFileSync(join(vstate, 'inbound-journal.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)).find(e => e.message_id === id9)
  check('shutdown during a transcription: the voice note is journaled "dropped" (untranscribed, transcribe_error) for the next instance',
    j9?.status === 'dropped' && j9.content === '(voice message)' && /interrupted/.test(j9.meta?.transcribe_error) && j9.meta?.attachment_file_id === 'v9', j9)
  notes.length = 0
  const V2 = await start({ ...env, TELEGRAM_TRANSCRIBE_CMD: '' })
  await waitFor(() => !!noteFor(id9), 5000)
  const re9 = noteFor(id9)?.params
  check('…and the next instance re-sends it (flagged redelivered)', re9?.meta.redelivered === 'true' && /\(voice message\)$/.test(re9.content), re9)
  await V2.close()

  // 9. "" turns both off, even with ~/bin/transcribe and ~/bin/tg-status present; unset → those defaults
  const home = mkdtempSync(join(tmpdir(), 'tgp-home-'))
  mkdirSync(join(home, 'bin'))
  writeFileSync(join(home, 'bin', 'transcribe'), readFileSync(join(bin, 'transcribe')), { mode: 0o755 })
  writeFileSync(join(home, 'bin', 'tg-status'), readFileSync(join(bin, 'status')), { mode: 0o755 })
  const offTag = 'voiceoff'
  const off = await start({ ...env, HOME: home, TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${offTag}`, TELEGRAM_TRANSCRIBE_CMD: '', TELEGRAM_STATUS_CMD: '' })
  await waitFor(() => /polling as/.test(off.log()))
  await waitFor(() => calls.some(x => x.tag === offTag && /MyCommands$/.test(x.method)))
  notes.length = 0
  n0 = calls.length
  files.v10 = 'say:should not run'
  const id10 = String(++vid)
  push({ message: { message_id: Number(id10), date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), voice: { file_id: 'v10', file_unique_id: 'uv10', duration: 3, file_size: 10 } } }, offTag)
  push({ message: { message_id: ++vid, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: '/status', entities: [{ type: 'bot_command', offset: 0, length: 7 }] } }, offTag)
  await arrival(id10, 3000)
  await waitFor(() => since(n0).some(x => x.tag === offTag && x.method === 'sendMessage'), 3000)
  const n10 = noteFor(id10)?.params
  check('TELEGRAM_TRANSCRIBE_CMD="" → voice delivered at once as before (no download, no transcript meta)',
    n10?.content === '(voice message)' && n10.meta.transcript === undefined && n10.meta.transcribe_error === undefined && !since(n0).some(x => x.tag === offTag && x.method === 'getFile'), n10)
  check('TELEGRAM_STATUS_CMD="" → /status answers the pairing state as before; menu without status (deleted when empty)',
    since(n0).find(x => x.tag === offTag && x.method === 'sendMessage')?.params.text === 'Paired as @owner.'
      && calls.some(x => x.tag === offTag && x.method === 'deleteMyCommands') && !calls.some(x => x.tag === offTag && x.method === 'setMyCommands'),
    calls.filter(x => x.tag === offTag).map(x => x.method))
  await off.close()

  const defTag = 'voicedef'
  const { TELEGRAM_TRANSCRIBE_CMD: _a, TELEGRAM_STATUS_CMD: _b, ...envNoCmd } = env
  const def = await start({ ...envNoCmd, HOME: home, TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${defTag}` })
  await waitFor(() => /polling as/.test(def.log()))
  await waitFor(() => calls.some(x => x.tag === defTag && /MyCommands$/.test(x.method)))
  notes.length = 0
  n0 = calls.length
  files.v11 = 'say:default transcriber'
  const id11 = String(++vid)
  push({ message: { message_id: Number(id11), date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), voice: { file_id: 'v11', file_unique_id: 'uv11', duration: 3, file_size: 10 } } }, defTag)
  push({ message: { message_id: ++vid, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: '/status', entities: [{ type: 'bot_command', offset: 0, length: 7 }] } }, defTag)
  await arrival(id11, 3000)
  await waitFor(() => since(n0).some(x => x.tag === defTag && x.method === 'sendMessage'), 3000)
  check('unset → defaults ~/bin/transcribe and "~/bin/tg-status show --plain" when executable; menu lists status',
    noteFor(id11)?.params.content === '🎙️ (voice, 3 s) default transcriber'
      && /args: show --plain/.test(since(n0).find(x => x.tag === defTag && x.method === 'sendMessage')?.params.text)
      && calls.find(x => x.tag === defTag && x.method === 'setMyCommands')?.params.commands?.[0]?.command === 'status',
    { note: noteFor(id11)?.params, sends: since(n0).filter(x => x.tag === defTag).map(x => [x.method, x.params.text]) })
  await def.close()

  for (const d of [vstate, bin, home]) rmSync(d, { recursive: true, force: true })
}

console.log('— instant commands (0.1.8): instant.json table → script, owner only, fallbacks, menu')
{
  const ITAG = 'instant'
  const istate = mkdtempSync(join(tmpdir(), 'tgp-istate-'))
  const access = (dmPolicy: string) => writeFileSync(join(istate, 'access.json'), JSON.stringify({
    dmPolicy, allowFrom: [String(OWNER)], groups: { [String(GROUP)]: { requireMention: false, allowFrom: [] } }, pending: {},
  }))
  access('allowlist')
  const ibin = mkdtempSync(join(tmpdir(), 'tgp-ibin-'))
  const runLog = join(ibin, 'run.log')
  // Fake command script: behaviour by its first argument.
  writeFileSync(join(ibin, 'cmd'), `#!/bin/bash
echo "$*" >> "$TGP_RUN_LOG"
case "$1" in
  ok) printf '💳 answer for %s\\ntoken: %s\\n' "$*" "\${TELEGRAM_BOT_TOKEN:-none}" ;;
  soft) echo "⚠️ /soft failed: Sonarr: no answer within 12 s."; exit 3 ;;
  crash) echo "loading" >&2; echo "Traceback: boom" >&2; exit 1 ;;
  hang) sleep 30 & echo $! > "$TGP_RUN_LOG.child"; wait ;;
  empty) ;;
  args) shift 2; printf 'n=%s' "$#"; for a in "$@"; do printf ' [%s]' "$a"; done; echo ;;
esac
`, { mode: 0o755 })
  writeFileSync(join(ibin, 'status'), '#!/bin/bash\necho "🟢 Working: instant tests"\n', { mode: 0o755 })
  const cmd = join(ibin, 'cmd')
  const table: any[] = [
    { command: 'bills', description: 'Open bills', run: `${cmd} ok bills` },
    { command: '/Queue', description: 'Downloads', run: [cmd, 'ok', 'queue'] },
    { command: 'soft', description: 'Script prints its own fallback', run: `${cmd} soft` },
    { command: 'crash', description: 'Crashes', run: `${cmd} crash` },
    { command: 'hang', description: 'Hangs', run: `${cmd} hang`, timeout_ms: 800 },
    { command: 'empty', description: 'Prints nothing', run: `${cmd} empty` },
    { command: 'fwd', description: 'Forward on error', run: `${cmd} crash`, on_error: 'forward' },
    { command: 'quiet', description: 'No note to the session', run: `${cmd} ok quiet`, note: false },
    { command: 'status', description: 'reserved', run: `${cmd} ok` },
    { command: 'bad-name', description: 'x', run: `${cmd} ok` },
    { command: 'bills', description: 'dup', run: `${cmd} ok dup` },
    { command: 'norun', description: 'no run' },
    { command: 'nodesc', run: `${cmd} ok` },
  ]
  const ifile = join(istate, 'instant.json')
  writeFileSync(ifile, JSON.stringify(table))
  const board = join(istate, 'board.json') // idle long ago: typing from normal messages ends after the grace
  writeFileSync(board, JSON.stringify({ mode: 'idle', since: Date.now() / 1000 - 600, idle_at: Date.now() / 1000 - 600 }))
  const GRACE = 400
  const env = {
    HOME: istate, TELEGRAM_STATE_DIR: istate, TELEGRAM_FORCE_POLL: '1', TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${ITAG}`,
    TELEGRAM_STATUS_CMD: join(ibin, 'status'), TELEGRAM_TRANSCRIBE_CMD: '', TGP_RUN_LOG: runLog,
    TELEGRAM_STATUS_STATE: board, TELEGRAM_TYPING_REFRESH_MS: '150', TELEGRAM_TYPING_GRACE_MS: String(GRACE),
  }
  const I = await start(env)
  await waitFor(() => /polling as/.test(I.log()))
  await waitFor(() => calls.some(x => x.tag === ITAG && x.method === 'setMyCommands'))
  const lines = (f: string) => { try { return readFileSync(f, 'utf8').split('\n').filter(Boolean) } catch { return [] } }
  const journal = () => readFileSync(join(istate, 'inbound-journal.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
  const dm = { id: OWNER, type: 'private' }
  let iid = 7000
  const send = (body: string, from = OWNER, chat: any = dm) => {
    const id = ++iid
    const len = body.split(/\s/)[0]!.length
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat, from: user(from), text: body,
      entities: body.startsWith('/') ? [{ type: 'bot_command', offset: 0, length: len }] : [] } }, ITAG)
    return String(id)
  }
  const noteFor = (id: string) => notes.find(x => x.method === 'notifications/claude/channel' && x.params?.meta?.message_id === id)?.params
  const arrival = (id: string, ms = 3000) => waitFor(() => !!noteFor(id), ms)
  const sends = (from: number) => since(from).filter(x => x.tag === ITAG && x.method === 'sendMessage')
  const typingsTo = (from: number) => since(from).filter(x => x.tag === ITAG && x.method === 'sendChatAction' && String(x.params.chat_id) === String(OWNER)).length

  const menu = calls.find(x => x.tag === ITAG && x.method === 'setMyCommands')?.params.commands
  check('menu at startup: /status, then instant.json\'s entries in file order (names normalized); bad, built-in and duplicate entries skipped and logged',
    JSON.stringify(menu?.map((c: any) => c.command)) === JSON.stringify(['status', 'bills', 'queue', 'soft', 'crash', 'hang', 'empty', 'fwd', 'quiet'])
      && menu[1].description === 'Open bills'
      && /instant\.json: skipped #9 \(\/status is built in\), #10 \(invalid command name "bad-name"\), #11 \(\/bills twice\), #12 \(\/norun: no "run"\), #13 \(\/nodesc: description must be 1-256 chars\)/.test(I.log()),
    { menu, log: I.log().split('\n').filter(l => /instant/.test(l)) })

  // 1. success
  notes.length = 0
  let n0 = calls.length
  const b1 = send('/bills')
  await arrival(b1)
  let s = sends(n0)
  const nb = noteFor(b1)
  check('owner "/bills" → the plugin runs its script and sends the stdout itself as plain text; the bot token never reaches the script',
    s.length === 1 && String(s[0]!.params.chat_id) === String(OWNER) && s[0]!.params.text === '💳 answer for ok bills\ntoken: none' && s[0]!.params.parse_mode === undefined,
    s.map(x => x.params))
  check('…then the session gets a note: meta handled="bills" with the answer repeated, journal kind "instant" (the watchdog skips it); no typing',
    nb?.meta.handled === 'bills' && /^\/bills \(answered instantly by the plugin; no reply needed\)\. It sent:\n💳 answer for ok bills/.test(nb.content)
      && journal().find(e => e.message_id === b1)?.kind === 'instant' && typingsTo(n0) === 0, { nb, typing: typingsTo(n0) })

  notes.length = 0
  n0 = calls.length
  const q1 = send('/queue@testbot'), q2 = send('/QUEUE')
  await arrival(q1); await arrival(q2)
  check('"/queue@<this bot>" and "/QUEUE" are instant too ("run" as an argv array, names case-insensitive)',
    sends(n0).filter(x => x.params.text.startsWith('💳 answer for ok queue')).length === 2 && noteFor(q1)?.meta.handled === 'queue' && noteFor(q2)?.meta.handled === 'queue',
    sends(n0).map(x => x.params.text))

  // 2. what stays a normal message: arguments, unknown commands, other bots, groups, strangers
  notes.length = 0
  n0 = calls.length
  let runsBefore = lines(runLog).length
  const a1 = send('/bills paid swisscom'), a2 = send('/weather'), a3 = send('/bills@otherbot')
  const g1 = send('/bills', OWNER, { id: GROUP, type: 'supergroup' })
  await arrival(a1); await arrival(a2); await arrival(a3); await arrival(g1)
  check('"/bills <args>", an unknown "/weather", "/bills@otherbot" and "/bills" in a group go to the session as normal messages; no script runs',
    noteFor(a1)?.content === '/bills paid swisscom' && noteFor(a2)?.content === '/weather' && noteFor(a3)?.content === '/bills@otherbot' && noteFor(g1)?.content === '/bills'
      && [a1, a2, a3, g1].every(id => noteFor(id)?.meta.handled === undefined) && lines(runLog).length === runsBefore && sends(n0).length === 0,
    { notes: [a1, a2, a3, g1].map(noteFor), runs: lines(runLog).length - runsBefore })

  notes.length = 0
  n0 = calls.length
  const x1 = send('/bills', STRANGER, { id: STRANGER, type: 'private' })
  await Bun.sleep(800)
  access('pairing')
  const x2 = send('/bills', STRANGER, { id: STRANGER, type: 'private' })
  await waitFor(() => sends(n0).length > 0, 3000)
  await Bun.sleep(200)
  access('allowlist')
  check('a stranger\'s "/bills" never runs the script: dropped with an allowlist, a pairing code in pairing mode',
    !noteFor(x1) && !noteFor(x2) && lines(runLog).length === runsBefore && sends(n0).length === 1 && /^Pairing required/.test(sends(n0)[0]!.params.text),
    { sends: sends(n0).map(x => x.params.text), runs: lines(runLog).length - runsBefore })

  // 3. failures with on_error "reply" (default): the owner always gets an answer
  notes.length = 0
  n0 = calls.length
  const f1 = send('/soft')
  await arrival(f1)
  check('script fails but prints a fallback (exit 3) → the owner gets that text; the session note has handled + instant_error',
    sends(n0).length === 1 && sends(n0)[0]!.params.text === '⚠️ /soft failed: Sonarr: no answer within 12 s.'
      && noteFor(f1)?.meta.handled === 'soft' && /^exit 3/.test(noteFor(f1)?.meta.instant_error) && /instant answer failed.*fallback text; no reply needed/.test(noteFor(f1)?.content),
    { sends: sends(n0).map(x => x.params.text), note: noteFor(f1) })

  notes.length = 0
  n0 = calls.length
  const f2 = send('/crash'), f3 = send('/empty')
  await arrival(f2); await arrival(f3)
  const ftexts = sends(n0).map(x => x.params.text)
  check('script crashes silently / prints nothing → a short "⚠️ /name didn\'t work (…)" reply (last stderr line); session told (instant_error)',
    ftexts[0] === "⚠️ /crash didn't work (exit 1: Traceback: boom). Ask me in a normal message if you need it." && noteFor(f2)?.meta.instant_error === 'exit 1: Traceback: boom'
      && ftexts[1] === "⚠️ /empty didn't work (the command printed nothing). Ask me in a normal message if you need it." && noteFor(f3)?.meta.instant_error === 'the command printed nothing',
    { ftexts, n2: noteFor(f2), n3: noteFor(f3) })

  notes.length = 0
  n0 = calls.length
  const t0 = Date.now()
  const h1 = send('/hang')
  const h2 = send('sent right after /hang')
  await arrival(h1, 5000); await arrival(h2, 5000)
  const hangMs = Date.now() - t0
  await Bun.sleep(300)
  let childAlive = true
  try { process.kill(Number(readFileSync(`${runLog}.child`, 'utf8')), 0) } catch { childAlive = false }
  const order = notes.filter(x => x.method === 'notifications/claude/channel').map(x => x.params.meta.message_id)
  check('script hangs → after its timeout_ms (0.8 s) the owner gets "⚠️ /hang didn\'t work (timed out after 0.8 s)"; its child processes are killed',
    sends(n0)[0]?.params.text === "⚠️ /hang didn't work (timed out after 0.8 s). Ask me in a normal message if you need it." && noteFor(h1)?.meta.instant_error === 'timed out after 0.8 s'
      && hangMs >= 700 && hangMs < 4000 && !childAlive, { sends: sends(n0).map(x => x.params.text), hangMs, childAlive })
  check('…and the chat\'s next message waits for it (arrival order kept)', order.indexOf(h1) === 0 && order.indexOf(h2) === 1, order)

  // 4. on_error "forward", note false
  notes.length = 0
  await Bun.sleep(GRACE + 300)
  n0 = calls.length
  const w1 = send('/fwd')
  await arrival(w1)
  check('on_error "forward": a failure sends "/fwd" to the session as a normal message (meta instant_error), nothing from the plugin',
    noteFor(w1)?.content === '/fwd' && noteFor(w1)?.meta.handled === undefined && noteFor(w1)?.meta.instant_error === 'exit 1: Traceback: boom'
      && journal().find(e => e.message_id === w1)?.kind === 'message' && sends(n0).length === 0, noteFor(w1))

  notes.length = 0
  n0 = calls.length
  const quiet = send('/quiet')
  await waitFor(() => sends(n0).length > 0, 3000)
  await Bun.sleep(800)
  check('note false: the answer is sent, the session is not told at all (no notification, nothing journaled)',
    sends(n0)[0]?.params.text.startsWith('💳 answer for ok quiet') && !noteFor(quiet) && !journal().some(e => e.message_id === quiet),
    { sends: sends(n0).map(x => x.params.text), note: noteFor(quiet) })

  // 5. menu with custom commands; instant.json edits work without a restart
  n0 = calls.length
  const rc = await I.callTool({ name: 'set_commands', arguments: { commands: [{ command: 'brief', description: 'Morning brief' }, { command: 'bills', description: 'custom bills' }] } })
  const set2 = since(n0).find(x => x.tag === ITAG && x.method === 'setMyCommands')?.params.commands
  check('set_commands: instant commands first, then custom ones; a custom entry named like an instant command is left out',
    JSON.stringify(set2?.map((c: any) => c.command)) === JSON.stringify(['status', 'bills', 'queue', 'soft', 'crash', 'hang', 'empty', 'fwd', 'quiet', 'brief'])
      && set2[1].description === 'Open bills' && /^commands set: \/status \/bills .* \/quiet \/brief$/.test(text(rc)), { set2, rc: text(rc) })

  writeFileSync(ifile, JSON.stringify([{ command: 'later', description: 'Added later', run: `${cmd} ok later` }]))
  notes.length = 0
  n0 = calls.length
  const l1 = send('/later'), l2 = send('/bills')
  await arrival(l1); await arrival(l2)
  check('instant.json is re-read when it changes: a new "/later" is instant at once, a removed "/bills" goes to the session',
    noteFor(l1)?.meta.handled === 'later' && sends(n0)[0]?.params.text.startsWith('💳 answer for ok later')
      && noteFor(l2)?.content === '/bills' && noteFor(l2)?.meta.handled === undefined, { l1: noteFor(l1), l2: noteFor(l2) })

  // 5b. args true (0.1.10): "/name <text>" is instant, the text is ONE argv element (no shell)
  writeFileSync(ifile, JSON.stringify([
    { command: 'later', description: 'Added later', run: `${cmd} ok later` },
    { command: 'find', description: 'Takes a query', run: `${cmd} args find`, args: true },
    { command: 'badargs', description: 'args not boolean', run: `${cmd} args x`, args: 'yes' },
  ]))
  notes.length = 0
  n0 = calls.length
  runsBefore = lines(runLog).length
  const r1 = send('/find  what did we decide about $(rm -rf ~); `id` "x"  ')
  await arrival(r1)
  check('args true: "/find <text>" runs the script with the trimmed text as ONE extra argument, nothing shell-interpreted; owner gets the answer',
    sends(n0)[0]?.params.text === 'n=1 [what did we decide about $(rm -rf ~); `id` "x"]' && noteFor(r1)?.meta.handled === 'find',
    { sends: sends(n0).map(x => x.params.text), note: noteFor(r1) })
  check('…the session note starts with the whole command (query included), journal kind "instant"',
    /^\/find what did we decide about \$\(rm -rf ~\); `id` "x" \(answered instantly by the plugin; no reply needed\)\. It sent:\nn=1 \[/.test(noteFor(r1)?.content ?? '')
      && journal().find(e => e.message_id === r1)?.kind === 'instant', noteFor(r1)?.content)

  notes.length = 0
  n0 = calls.length
  const r2 = send('/find'), r3 = send('/find@testbot multi\nline   query')
  await arrival(r2); await arrival(r3)
  const rtexts = sends(n0).map(x => x.params.text)
  check('args true: a bare "/find" runs without an extra argument (the script prints its usage); "/find@<this bot> …" works, newlines kept inside the one argument',
    rtexts.includes('n=0') && rtexts.includes('n=1 [multi\nline   query]') && noteFor(r2)?.meta.handled === 'find' && noteFor(r3)?.meta.handled === 'find', rtexts)

  notes.length = 0
  n0 = calls.length
  runsBefore = lines(runLog).length
  const long = 'x'.repeat(513)
  const r4 = send('/later with args'), r5 = send(`/find ${long}`), r6 = send('/find@otherbot q'), r7 = send('/badargs q')
  await arrival(r4); await arrival(r5); await arrival(r6); await arrival(r7)
  check('stays a normal message: args on an entry without args true, a >512-char argument, "/find@otherbot", an entry with a non-boolean args (skipped, logged); no script runs',
    noteFor(r4)?.content === '/later with args' && noteFor(r5)?.content === `/find ${long}` && noteFor(r6)?.content === '/find@otherbot q' && noteFor(r7)?.content === '/badargs q'
      && [r4, r5, r6, r7].every(id => noteFor(id)?.meta.handled === undefined) && lines(runLog).length === runsBefore && sends(n0).length === 0
      && /instant\.json: skipped #3 \(\/badargs: args must be true or false\)/.test(I.log()),
    { notes: [r4, r5, r6, r7].map(id => noteFor(id)?.content?.slice(0, 40)), runs: lines(runLog).length - runsBefore, log: I.log().split('\n').filter(l => /badargs/.test(l)) })
  writeFileSync(ifile, JSON.stringify([{ command: 'later', description: 'Added later', run: `${cmd} ok later` }]))

  const st1 = send('/status')
  await arrival(st1)
  check('/status still goes through the same table (TELEGRAM_STATUS_CMD, note handled="status", journal kind "status")',
    noteFor(st1)?.meta.handled === 'status' && journal().find(e => e.message_id === st1)?.kind === 'status'
      && since(n0).some(x => x.tag === ITAG && x.method === 'sendMessage' && x.params.text === '🟢 Working: instant tests'), noteFor(st1))
  await I.close()

  // 6. TELEGRAM_INSTANT_FILE="" turns the table off
  writeFileSync(ifile, JSON.stringify(table))
  const OTAG = 'instantoff'
  const off = await start({ ...env, TELEGRAM_INSTANT_FILE: '', TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${OTAG}` })
  await waitFor(() => /polling as/.test(off.log()))
  await waitFor(() => calls.some(x => x.tag === OTAG && x.method === 'setMyCommands'))
  notes.length = 0
  runsBefore = lines(runLog).length
  const id = ++iid
  push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER), text: '/bills', entities: [{ type: 'bot_command', offset: 0, length: 6 }] } }, OTAG)
  await arrival(String(id))
  check('TELEGRAM_INSTANT_FILE="" → no instant.json commands: menu /status + the persisted custom ones (their "bills" is back); "/bills" goes to the session',
    JSON.stringify(calls.find(x => x.tag === OTAG && x.method === 'setMyCommands')?.params.commands.map((c: any) => c.command)) === '["status","brief","bills"]'
      && noteFor(String(id))?.content === '/bills' && lines(runLog).length === runsBefore, noteFor(String(id)))
  await off.close()

  for (const d of [istate, ibin]) rmSync(d, { recursive: true, force: true })
}

console.log('— private scans (0.1.11): "/scan private" / "/sp" → paperless via PATH, nothing to the session; "/scan" passes through')
{
  const STAG = 'scan'
  const sstate = mkdtempSync(join(tmpdir(), 'tgp-sstate-'))
  writeFileSync(join(sstate, 'access.json'), JSON.stringify({ dmPolicy: 'allowlist', allowFrom: [String(OWNER)], groups: {}, pending: {} }))
  const sbin = mkdtempSync(join(tmpdir(), 'tgp-sbin-'))
  const stmp = mkdtempSync(join(tmpdir(), 'tgp-stmp-')) // the server's TMPDIR: its temp dirs land here
  const slog = join(sbin, 'scan.log')
  // Fake paperless, found on PATH: logs argv, the temp dir/file modes and the file's content; behaviour by title.
  writeFileSync(join(sbin, 'paperless'), `#!/bin/bash
f="$2"
{
  printf 'ARGV'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'
  printf 'MODES\\t%s\\t%s\\n' "$(stat -c %a "$(dirname "$f")")" "$(stat -c %a "$f")"
  printf 'CONTENT\\t%s\\n' "$(cat "$f")"
  printf 'TOKEN\\t%s\\n' "\${TELEGRAM_BOT_TOKEN:-none}"
} >> "$TGP_SCAN_LOG"
case "$*" in
  *--title=FAIL*) echo "uploaded, task t1" >&2; echo "paperless: consumption failed: boom" >&2; exit 1 ;;
  *--title=SLOW*) sleep 30 & wait ;;
  *) n=$(( 4240 + $(grep -c '^ARGV' "$TGP_SCAN_LOG") )); echo "document $n: http://paperless.test/documents/$n/details" ;;
esac
`, { mode: 0o755 })
  const S = await start({
    HOME: sstate, TELEGRAM_STATE_DIR: sstate, TELEGRAM_FORCE_POLL: '1', TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${STAG}`,
    TELEGRAM_TRANSCRIBE_CMD: '', TELEGRAM_STATUS_CMD: '', TELEGRAM_INSTANT_FILE: '', TGP_SCAN_LOG: slog,
    PATH: `${sbin}:${guardBin}:${process.env.PATH!}`, TMPDIR: stmp, TELEGRAM_SCAN_PRIVATE_TIMEOUT_MS: '1500',
  })
  await waitFor(() => /polling as/.test(S.log()))
  const dm = { id: OWNER, type: 'private' }
  let sid = 8000
  const cap = (caption?: string) => caption == null ? {} : { caption, caption_entities: caption.startsWith('/') ? [{ type: 'bot_command', offset: 0, length: caption.split(/\s/)[0]!.length }] : [] }
  const photo = (file: string, caption?: string, extra: any = {}, from = OWNER) => {
    const id = ++sid
    files[`${file}s`] = 'small preview'; files[file] ??= `JPEG ${file} full size`
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: from === OWNER ? dm : { id: from, type: 'private' }, from: user(from),
      photo: [{ file_id: `${file}s`, file_unique_id: `u${file}s`, width: 90, height: 90, file_size: 13 }, { file_id: file, file_unique_id: `u${file}`, width: 1280, height: 960, file_size: 30 }],
      ...cap(caption), ...extra } }, STAG)
    return String(id)
  }
  const doc = (file: string, name: string, caption?: string, size = 30) => {
    const id = ++sid
    files[file] ??= `%PDF ${file}`
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER),
      document: { file_id: file, file_unique_id: `u${file}`, file_name: name, mime_type: 'application/pdf', file_size: size }, ...cap(caption) } }, STAG)
    return String(id)
  }
  const noteFor = (id: string) => notes.find(x => x.method === 'notifications/claude/channel' && x.params?.meta?.message_id === id)?.params
  const arrival = (id: string, ms = 4000) => waitFor(() => !!noteFor(id), ms)
  const sends = (from: number) => since(from).filter(x => x.tag === STAG && x.method === 'sendMessage')
  const replyTo = (from: number, id: string) => sends(from).find(x => x.params.reply_parameters?.message_id === Number(id))?.params
  const runs = () => { try { return readFileSync(slog, 'utf8').split('\n').filter(l => l.startsWith('ARGV')).length } catch { return 0 } }
  const lastRun = () => {
    const ls = readFileSync(slog, 'utf8').trim().split('\n')
    const i = ls.map(l => l.startsWith('ARGV')).lastIndexOf(true)
    const argv = ls[i]!.split('\t').slice(1)
    const [, dirMode, fileMode] = ls[i + 1]!.split('\t')
    return { argv, dirMode, fileMode, content: ls[i + 2]!.split('\t').slice(1).join('\t'), token: ls[i + 3]!.split('\t')[1] }
  }
  const scanDirsLeft = () => readdirSync(stmp).filter(n => n.startsWith('tgp-scan-'))
  const journalText = () => readFileSync(join(sstate, 'inbound-journal.jsonl'), 'utf8')
  const inboxFiles = () => { try { return readdirSync(join(sstate, 'inbox')) } catch { return [] } }
  const reactionsOn = (from: number, id: string) => since(from).filter(x => x.tag === STAG && x.method === 'setMessageReaction' && x.params.message_id === Number(id)).map(x => (x.params.reaction ?? []).map((r: any) => r.emoji).join('') || '-')

  // 1. owner photo "/scan private <title>"
  notes.length = 0
  let n0 = calls.length
  const s1 = photo('phA', '/scan private Arztrechnung   Oktober')
  await arrival(s1)
  let run = lastRun()
  const p1 = run.argv[1] ?? ''
  check('owner photo "/scan private <title>" → `paperless` from PATH: upload FILE --title=<title> --tag Inbox --tag private --create (largest photo size; no bot token)',
    JSON.stringify(run.argv) === JSON.stringify(['upload', p1, '--title=Arztrechnung Oktober', '--tag', 'Inbox', '--tag', 'private', '--create'])
      && run.content === 'JPEG phA full size' && run.token === 'none', run)
  check('…the file sat in a fresh 0700 temp dir under TMPDIR (file 0600), named scan-<stamp>.jpg; both are gone afterwards',
    dirname(dirname(p1)) === stmp && /^tgp-scan-\d+-/.test(basename(dirname(p1))) && /^scan-\d{8}-\d{4}\.jpg$/.test(basename(p1))
      && run.dirMode === '700' && run.fileMode === '600' && !existsSync(p1) && !existsSync(dirname(p1)) && scanDirsLeft().length === 0,
    { p1, run, left: scanDirsLeft() })
  const r1 = replyTo(n0, s1)
  check('…the owner gets "🔒 Filed privately → document <id>" as a reply to his photo; ✍ while it runs, cleared after',
    r1?.text === '🔒 Filed privately → document 4241' && sends(n0).length === 1 && JSON.stringify(reactionsOn(n0, s1)) === '["✍","-"]', { r1, re: reactionsOn(n0, s1) })
  const nt1 = noteFor(s1)
  check('…the session gets one line: handled="scan-private", doc_id; no image_path / file id / title anywhere (note, journal, inbox)',
    nt1?.content === '/scan private (filed privately by the plugin → paperless document 4241; the owner was told; no reply needed)'
      && nt1.meta.handled === 'scan-private' && nt1.meta.doc_id === '4241' && nt1.meta.image_path === undefined && nt1.meta.attachment_file_id === undefined
      && !JSON.stringify(notes).includes('Arztrechnung') && !journalText().includes('Arztrechnung') && inboxFiles().length === 0
      && notes.filter(x => x.method === 'notifications/claude/channel').length === 1,
    { nt1, inbox: inboxFiles() })
  check('…journal kind "instant" (the watchdog expects no answer)',
    journalText().split('\n').filter(Boolean).map(l => JSON.parse(l)).find(e => e.message_id === s1)?.kind === 'instant')

  // 2. "/sp" on a PDF, no title
  notes.length = 0
  n0 = calls.length
  const s2 = doc('docB', 'Befund Mai.pdf', '/sp')
  await arrival(s2)
  run = lastRun()
  check('"/sp" on a PDF without a title → title "Scan YYYY-MM-DD HH:MM", the document keeps its name; reply + one-line note',
    /^--title=Scan \d{4}-\d\d-\d\d \d\d:\d\d$/.test(run.argv[2] ?? '') && basename(run.argv[1] ?? '') === 'Befund Mai.pdf' && run.content === '%PDF docB'
      && replyTo(n0, s2)?.text === '🔒 Filed privately → document 4242'
      && noteFor(s2)?.content === '/sp (filed privately by the plugin → paperless document 4242; the owner was told; no reply needed)'
      && noteFor(s2)?.meta.attachment_file_id === undefined && !existsSync(run.argv[1]!) && scanDirsLeft().length === 0,
    { run, note: noteFor(s2) })

  // 3. album: the caption is on the first item; the rest of the media group follows it
  notes.length = 0
  n0 = calls.length
  const before = runs()
  const g1 = photo('phG1', '/sp Befund', { media_group_id: 'alb1' }), g2 = photo('phG2', undefined, { media_group_id: 'alb1' })
  const g3 = photo('phG3', undefined, { media_group_id: 'other' }) // another album without the caption: normal
  await arrival(g1); await arrival(g2); await arrival(g3)
  const ls = readFileSync(slog, 'utf8').trim().split('\n').filter(l => l.startsWith('ARGV')).slice(before).map(l => l.split('\t')[3])
  check('album "/sp <title>": every photo of that media group is filed separately ("<title>", "<title> (2)"), in order; none reaches the session',
    JSON.stringify(ls) === JSON.stringify(['--title=Befund', '--title=Befund (2)']) && noteFor(g1)?.meta.doc_id === '4243' && noteFor(g2)?.meta.doc_id === '4244'
      && replyTo(n0, g2)?.text === '🔒 Filed privately → document 4244' && noteFor(g1)?.meta.image_path === undefined && noteFor(g2)?.meta.image_path === undefined,
    { ls, g1: noteFor(g1), g2: noteFor(g2) })
  check('…a photo of another media group without the caption is a normal message (image_path)',
    noteFor(g3)?.meta.handled === undefined && !!noteFor(g3)?.meta.image_path && noteFor(g3)?.content === '(photo)', noteFor(g3))
  for (const f of inboxFiles()) rmSync(join(sstate, 'inbox', f), { force: true })

  // 4. failures: paperless error, our timeout, too big, not a photo/document
  notes.length = 0
  n0 = calls.length
  const s4 = photo('phF', '/sp FAIL')
  await arrival(s4)
  check('paperless fails → "⚠️ Private scan failed: <its last error line>" to the owner; the session gets only a code (scan_error), no title; nothing kept',
    replyTo(n0, s4)?.text === '⚠️ Private scan failed: consumption failed: boom'
      && noteFor(s4)?.content === "/sp (the plugin's private scan failed: upload; nothing was kept; the owner was told; no reply needed)"
      && noteFor(s4)?.meta.scan_error === 'upload' && noteFor(s4)?.meta.doc_id === undefined && !JSON.stringify(noteFor(s4)).includes('FAIL') && scanDirsLeft().length === 0,
    { r: replyTo(n0, s4), note: noteFor(s4), left: scanDirsLeft() })
  n0 = calls.length
  const s5 = photo('phS', '/sp SLOW')
  await arrival(s5, 8000)
  check('upload hangs → killed after TELEGRAM_SCAN_PRIVATE_TIMEOUT_MS: "⚠️ Private scan failed: paperless upload timed out after 1.5 s"; scan_error timeout; temp dir gone',
    replyTo(n0, s5)?.text === '⚠️ Private scan failed: paperless upload timed out after 1.5 s' && noteFor(s5)?.meta.scan_error === 'timeout' && scanDirsLeft().length === 0,
    { r: replyTo(n0, s5), note: noteFor(s5), left: scanDirsLeft() })
  n0 = calls.length
  let runs0 = runs()
  const s6 = doc('docBig', 'big.pdf', '/sp', 30 * 1024 * 1024)
  await arrival(s6)
  check('a document over 20 MB → refused before any download; paperless not run',
    replyTo(n0, s6)?.text === '⚠️ Private scan failed: the file is over 20 MB (the Bot API limit)' && noteFor(s6)?.meta.scan_error === 'too_big'
      && !since(n0).some(x => x.tag === STAG && x.method === 'getFile') && runs() === runs0, { r: replyTo(n0, s6), note: noteFor(s6) })
  n0 = calls.length
  const s7 = String(++sid)
  push({ message: { message_id: Number(s7), date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER),
    voice: { file_id: 'vSP', file_unique_id: 'uvSP', duration: 3, mime_type: 'audio/ogg', file_size: 10 }, ...cap('/sp secret') } }, STAG)
  await arrival(s7)
  check('a voice note with "/sp" → refused ("only photos and documents"), not forwarded',
    replyTo(n0, s7)?.text === '⚠️ Private scan failed: only photos and documents (PDF) can be filed' && noteFor(s7)?.meta.scan_error === 'unsupported'
      && noteFor(s7)?.meta.attachment_file_id === undefined && !JSON.stringify(noteFor(s7)).includes('secret') && runs() === runs0, noteFor(s7))

  // 5. pass-through: plain "/scan", look-alikes, strangers
  notes.length = 0
  n0 = calls.length
  runs0 = runs()
  const t1 = photo('phC', '/scan'), t2 = photo('phD', '/scan privately'), t3 = doc('docE', 'Rechnung.pdf', '/scan Rechnung Swisscom')
  await arrival(t1); await arrival(t2); await arrival(t3)
  check('plain "/scan" (and "/scan privately") on a photo → the session as today: content = caption, image_path; no paperless run, no reply',
    noteFor(t1)?.content === '/scan' && !!noteFor(t1)?.meta.image_path && existsSync(noteFor(t1)!.meta.image_path) && noteFor(t1)?.meta.handled === undefined
      && noteFor(t2)?.content === '/scan privately' && !!noteFor(t2)?.meta.image_path && runs() === runs0 && sends(n0).length === 0,
    { t1: noteFor(t1), t2: noteFor(t2) })
  check('"/scan <title>" on a document → the session with attachment_file_id, as today',
    noteFor(t3)?.content === '/scan Rechnung Swisscom' && noteFor(t3)?.meta.attachment_file_id === 'docE' && runs() === runs0, noteFor(t3))
  n0 = calls.length
  photo('phX', '/sp mine now', {}, STRANGER)
  await Bun.sleep(700)
  check('a stranger\'s "/sp" photo: nothing (no paperless run, no reply, no note, no download) under dmPolicy allowlist',
    runs() === runs0 && !since(n0).some(x => x.tag === STAG && ['sendMessage', 'getFile', 'setMessageReaction'].includes(x.method))
      && !notes.some(x => x.params?.meta?.user_id === String(STRANGER)), since(n0).filter(x => x.tag === STAG).map(x => x.method))
  await S.close()

  // 6. TELEGRAM_SCAN_PRIVATE_CMD="" (or no paperless at all): refused, still never forwarded
  const OTAG = 'scanoff'
  const O = await start({
    HOME: sstate, TELEGRAM_STATE_DIR: sstate, TELEGRAM_FORCE_POLL: '1', TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${OTAG}`,
    TELEGRAM_TRANSCRIBE_CMD: '', TELEGRAM_STATUS_CMD: '', TELEGRAM_INSTANT_FILE: '', TGP_SCAN_LOG: slog,
    PATH: `${sbin}:${guardBin}:${process.env.PATH!}`, TMPDIR: stmp, TELEGRAM_SCAN_PRIVATE_CMD: '',
  })
  await waitFor(() => /polling as/.test(O.log()))
  notes.length = 0
  n0 = calls.length
  const id = String(++sid)
  files.phO = 'JPEG off'
  push({ message: { message_id: Number(id), date: Math.floor(Date.now() / 1000), chat: dm, from: user(OWNER),
    photo: [{ file_id: 'phO', file_unique_id: 'uphO', width: 10, height: 10, file_size: 8 }], ...cap('/sp x') } }, OTAG)
  await arrival(id)
  check('TELEGRAM_SCAN_PRIVATE_CMD="" → "⚠️ Private scan failed: the paperless command is not available here"; no download, not forwarded',
    since(n0).find(x => x.tag === OTAG && x.method === 'sendMessage')?.params.text === '⚠️ Private scan failed: the paperless command is not available here'
      && noteFor(id)?.meta.scan_error === 'no_command' && noteFor(id)?.meta.image_path === undefined && runs() === runs0
      && !since(n0).some(x => x.tag === OTAG && x.method === 'getFile'), noteFor(id))
  await O.close()
  check('no temp dirs left behind by either instance', scanDirsLeft().length === 0, scanDirsLeft())
  for (const d of [sstate, sbin, stmp]) rmSync(d, { recursive: true, force: true })
}

console.log('— delivery guard: verdict from the MCP log, no consumption while blocked, drops, journal, health, replay')
{
  const OWNER_S = String(OWNER)
  const polls = (tag: string) => pollsBy[tag] ?? 0
  const mkState = () => {
    const d = mkdtempSync(join(tmpdir(), 'tgp-guard-'))
    writeFileSync(join(d, 'access.json'), JSON.stringify({ dmPolicy: 'allowlist', allowFrom: [OWNER_S], groups: {}, pending: {} }))
    return d
  }
  const logRoot = mkdtempSync(join(tmpdir(), 'tgp-mcplog-'))
  const dirs: string[] = [logRoot]

  /** Acts like Claude Code for one server connection: writes its MCP debug log
   *  (batched like Claude Code's writer), answers roots/list with a log line,
   *  and accepts (logs) or silently drops channel notifications (sim.wn). */
  async function claudeLike(tag: string, gstate: string, gate: 'registered' | string | null, env: Record<string, string> = {}) {
    const dir = join(logRoot, tag)
    mkdirSync(dir, { recursive: true })
    const file = join(dir, `${new Date().toISOString().replace(/[:.]/g, '-')}.jsonl`)
    let buf: string[] = []
    const sim = {
      wn: gate === 'registered',
      file,
      log: (m: string) => { buf.push(JSON.stringify({ debug: m, timestamp: new Date().toISOString(), sessionId: 'sess-test', cwd: '/home/node/workspace' })) },
      flush: () => { if (buf.length) { appendFileSync(file, buf.join('\n') + '\n'); buf = [] } },
      stop: () => { clearInterval(timer); sim.flush() },
    }
    const timer = setInterval(sim.flush, 300)
    sim.log('Starting connection with timeout of 30000ms')
    const c = await start({
      HOME: gstate, TELEGRAM_STATE_DIR: gstate, TELEGRAM_FORCE_POLL: '1', TELEGRAM_MCP_LOG_DIR: dir,
      TELEGRAM_API_ROOT: `http://127.0.0.1:${server.port}/${tag}`, TELEGRAM_POLL_CHECK_MS: '300',
      TELEGRAM_GATE_RECHECK_MS: '300', TELEGRAM_REDELIVER_MS: '700', TELEGRAM_REPLAY_CHECK_MS: '200',
      TELEGRAM_HEALTH_POLL_MS: '200', TELEGRAM_QUIET_HOURS: '',
      ...env,
    }, undefined, {
      roots: () => sim.log('Received ListRoots request from server'),
      channel: n => {
        if (!sim.wn) return false
        sim.log(`notifications/claude/channel: ${String(n.params.content).slice(0, 80)}`)
        return true
      },
    })
    sim.log('Successfully connected (transport: stdio) in 100ms')
    sim.log('Connection established with capabilities: {"hasTools":true,"protocolEra":"legacy"}')
    if (gate === 'registered') sim.log('Channel notifications registered')
    else if (gate) sim.log(`Channel notifications skipped: ${gate}`)
    return Object.assign(c, { sim })
  }

  const health = (d: string) => { try { return JSON.parse(readFileSync(join(d, 'health.json'), 'utf8')) } catch { return null } }
  const journal = (d: string): any[] => {
    try { return readFileSync(join(d, 'inbound-journal.jsonl'), 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) } catch { return [] }
  }
  const mode = (f: string) => statSync(f).mode & 0o777
  const ownerMsg = (tag: string, body: string, id: number) =>
    push({ message: { message_id: id, date: Math.floor(Date.now() / 1000), chat: { id: OWNER, type: 'private' }, from: user(OWNER), text: body } }, tag)
  const sentTo = (tag: string, from: number, re: RegExp) => since(from).filter(x =>
    x.tag === tag && x.method === 'sendMessage' && String(x.params.chat_id) === OWNER_S && re.test(String(x.params.text)))
  const got = (body: string) => notes.filter(x => x.method === 'notifications/claude/channel' && String(x.params?.content).endsWith(body))

  // 1. Claude Code registered us: poll, journal, ack, health.
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g1', d, 'registered')
    await waitFor(() => polls('g1') > 0 && health(d)?.delivery?.state === 'ok', 6000)
    const h0 = health(d)
    check('verdict "registered" read from the MCP log → polls; health.json (0600): version, pid, role, verdict, log file',
      polls('g1') > 0 && h0?.version === PKG_VERSION && h0.pid > 0 && h0.poll_role === 'poll' && h0.channels_accepted === true
        && h0.delivery?.state === 'ok' && h0.mcp_log === c.sim.file && mode(join(d, 'health.json')) === 0o600, h0)
    notes.length = 0
    ownerMsg('g1', 'guard: hello', 501)
    await waitFor(() => got('guard: hello').length > 0)
    await waitFor(() => journal(d).some(e => e.message_id === '501' && e.status === 'acked'), 6000)
    await c.callTool({ name: 'react', arguments: { chat_id: OWNER_S, message_id: '501', emoji: '👍' } })
    await waitFor(() => health(d)?.last_tool_call?.name === 'react' && health(d)?.last_inbound?.message_id === '501', 5000)
    const e = journal(d).find(x => x.message_id === '501')
    const h1 = health(d)
    check('owner message: journaled (0600), acked by its Claude Code log line; health has last_inbound, last_tool_call, last_poll_ok',
      e?.status === 'acked' && e.kind === 'message' && e.owner === true && e.content === 'guard: hello' && mode(join(d, 'inbound-journal.jsonl')) === 0o600
        && h1?.last_inbound?.kind === 'message' && h1.last_inbound.journal_id === e.id && h1.last_tool_call?.name === 'react'
        && typeof h1.last_poll_ok === 'string' && Date.now() - Date.parse(h1.last_poll_ok) < 5000, { e, h1 })
    c.sim.stop(); await c.close()
  }

  // 2. Claude Code skipped us (today's incident): don't consume, alert once, resume when it accepts.
  {
    const d = mkState(); dirs.push(d)
    let n = calls.length
    const c = await claudeLike('g2', d, 'channels feature is not currently available')
    await waitFor(() => health(d)?.poll_role === 'blocked', 6000)
    ownerMsg('g2', 'guard: while blocked', 601)
    await Bun.sleep(1200)
    const h = health(d)
    const alerts = sentTo('g2', n, /^⚠️ \(auto\)/)
    check('verdict "skipped" → never calls getUpdates (Telegram keeps the update), holds bot.pid, health: blocked/disabled',
      polls('g2') === 0 && q('g2').length === 1 && h?.delivery?.state === 'blocked' && h.delivery.kind === 'disabled'
        && h.channels_accepted === false && /not currently available/.test(h.channels_reason)
        && parseInt(readFileSync(join(d, 'bot.pid'), 'utf8'), 10) === h.pid, { h, polls: polls('g2'), queued: q('g2').length })
    check('the owner gets one "⚠️ (auto)" alert (queued, Remote Control meanwhile); delivery-alert.json marks it sent',
      alerts.length === 1 && /Remote Control/.test(alerts[0]!.params.text) && /holds yours/.test(alerts[0]!.params.text)
        && JSON.parse(readFileSync(join(d, 'delivery-alert.json'), 'utf8')).sent === true, alerts.map(a => a.params.text))
    c.sim.stop(); await c.close()

    n = calls.length
    const c2 = await claudeLike('g2', d, 'channels feature is not currently available') // restarted, same problem
    await waitFor(() => health(d)?.poll_role === 'blocked' && health(d)?.pid !== h.pid, 6000)
    await Bun.sleep(600)
    check('same incident after a restart: no second alert, still not polling',
      sentTo('g2', n, /^⚠️/).length === 0 && polls('g2') === 0 && q('g2').length === 1, since(n).filter(x => x.tag === 'g2').map(x => x.method))

    notes.length = 0
    n = calls.length
    c2.sim.wn = true // Claude Code's gate accepts us after all (a reconnect, a plugin reload)
    c2.sim.log('Channel notifications registered')
    await waitFor(() => got('guard: while blocked').length > 0, 6000)
    await waitFor(() => sentTo('g2', n, /Back to normal/).length > 0, 4000)
    check('verdict turns "registered" → polling starts, the held update arrives once, one "✅ (auto) Back to normal", alert cleared',
      got('guard: while blocked').length === 1 && polls('g2') > 0 && sentTo('g2', n, /^✅ \(auto\) Back to normal/).length === 1
        && !existsSync(join(d, 'delivery-alert.json')) && health(d)?.delivery?.state === 'ok', notes.map(x => x.params?.content))
    c2.sim.stop(); await c2.close()
  }

  // 3. Claude Code drops notifications mid-session (channels off at delivery time).
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g3', d, 'registered')
    await waitFor(() => polls('g3') > 0, 6000)
    notes.length = 0
    ownerMsg('g3', 'guard: first (accepted)', 701)
    await waitFor(() => journal(d).some(e => e.message_id === '701' && e.status === 'acked'), 6000)
    let n = calls.length
    c.sim.wn = false
    ownerMsg('g3', 'guard: dropped one', 702)
    await waitFor(() => journal(d).some(e => e.message_id === '702' && e.status === 'dropped'), 8000)
    await waitFor(() => health(d)?.delivery?.kind === 'dropped' && health(d)?.poll_role === 'blocked', 3000)
    const pollsPaused = polls('g3')
    ownerMsg('g3', 'guard: sent while paused', 703)
    await Bun.sleep(1500)
    check('a notification with no Claude Code log line before a later marker → journal "dropped", polling paused, new updates stay at Telegram',
      health(d)?.poll_role === 'blocked' && polls('g3') <= pollsPaused + 1 && q('g3').length === 1 && got('guard: dropped one').length === 0,
      { h: health(d), queued: q('g3').length })
    check('the owner gets one alert about the drops', sentTo('g3', n, /^⚠️ \(auto\) Claude Code started dropping/).length === 1, since(n).filter(x => x.method === 'sendMessage').map(x => x.params.text))
    n = calls.length
    c.sim.wn = true // channels work again
    await waitFor(() => got('guard: sent while paused').length > 0, 8000)
    await waitFor(() => sentTo('g3', n, /Back to normal/).length > 0, 4000)
    const re = got('guard: dropped one')
    const order = notes.findIndex(x => String(x.params?.content).endsWith('guard: dropped one')) < notes.findIndex(x => String(x.params?.content).endsWith('guard: sent while paused'))
    check('recovered: the dropped message arrives once, flagged (redelivered="true", noted in the text), before the held update; "back to normal"',
      re.length === 1 && re[0].params.meta.redelivered === 'true' && /^\(re-sent after a connection problem/.test(re[0].params.content) && order
        && got('guard: sent while paused').length === 1 && sentTo('g3', n, /Back to normal/).length === 1
        && journal(d).find(e => e.message_id === '702')?.status === 'acked' && health(d)?.poll_role === 'poll',
      notes.map(x => String(x.params?.content).slice(0, 50)))
    c.sim.stop(); await c.close()
  }

  // 4. Dropped, then restarted (what the watchdog does): the new instance re-sends it.
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g4', d, 'registered')
    await waitFor(() => polls('g4') > 0, 6000)
    ownerMsg('g4', 'guard: confirms the log format', 801)
    await waitFor(() => journal(d).some(e => e.message_id === '801' && e.status === 'acked'), 6000)
    c.sim.wn = false
    ownerMsg('g4', 'guard: dropped before a restart', 802)
    await waitFor(() => journal(d).some(e => e.message_id === '802' && e.status === 'dropped'), 8000)
    c.sim.stop(); await c.close()
    notes.length = 0
    const n = calls.length
    const c2 = await claudeLike('g4', d, 'registered')
    await waitFor(() => got('guard: dropped before a restart').length > 0, 8000)
    await waitFor(() => sentTo('g4', n, /Back to normal/).length > 0, 4000)
    const re = got('guard: dropped before a restart')
    check('after a restart the new instance re-sends what the old one saw dropped (flagged, once), then "back to normal"',
      re.length === 1 && re[0].params.meta.redelivered === 'true' && journal(d).find(e => e.message_id === '802')?.status === 'acked'
        && sentTo('g4', n, /Back to normal/).length === 1 && got('guard: confirms the log format').length === 0, notes.map(x => x.params?.content))
    c2.sim.stop(); await c2.close()
  }

  // 5. No ack line ever seen (e.g. Claude Code changed its log format): never act on "drops".
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g5', d, 'registered')
    c.sim.wn = false
    await waitFor(() => polls('g5') > 0, 6000)
    const n = calls.length
    ownerMsg('g5', 'guard: never logged', 901)
    await waitFor(() => journal(d).some(e => e.message_id === '901' && e.status !== 'sent'), 8000)
    await Bun.sleep(1000)
    check('without a single ack line in this process a missing line is only "unconfirmed": no pause, no alert, no re-send',
      journal(d).find(e => e.message_id === '901')?.status === 'unconfirmed' && health(d)?.poll_role === 'poll'
        && sentTo('g5', n, /^⚠️/).length === 0 && journal(d).find(e => e.message_id === '901')?.tries === 1, { j: journal(d), h: health(d) })
    c.sim.stop(); await c.close()
  }

  // 6. Watchdog replay requests.
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g6', d, 'registered')
    await waitFor(() => polls('g6') > 0, 6000)
    notes.length = 0
    ownerMsg('g6', 'guard: the session never saw this', 1001)
    await waitFor(() => journal(d).some(e => e.message_id === '1001' && e.status === 'acked'), 6000)
    const id = journal(d).find(e => e.message_id === '1001').id
    const ask = () => writeFileSync(join(d, 'replay-request.json'), JSON.stringify({ ids: [id], reason: 'stalled' }))
    ask(); await waitFor(() => got('guard: the session never saw this').length === 2, 3000)
    ask(); await waitFor(() => got('guard: the session never saw this').length === 3, 3000)
    ask(); await Bun.sleep(1000)
    const all = got('guard: the session never saw this')
    check('replay-request.json (watchdog): each id re-sent flagged redelivered, at most twice per event; the request is consumed',
      all.length === 3 && all.slice(1).every(x => x.params.meta.redelivered === 'true') && journal(d).find(e => e.id === id)?.replays === 2
        && !existsSync(join(d, 'replay-request.json')), all.map(x => x.params.meta))
    c.sim.stop(); await c.close()
  }

  // 7. Journal bounds.
  {
    const d = mkState(); dirs.push(d)
    const c = await claudeLike('g7', d, 'registered')
    await waitFor(() => polls('g7') > 0, 6000)
    notes.length = 0
    for (let i = 0; i < 110; i++) push({ message_reaction: { chat: { id: OWNER, type: 'private' }, message_id: 5000 + i, user: user(OWNER), date: 1700000000, old_reaction: [], new_reaction: [{ type: 'emoji', emoji: '👍' }] } }, 'g7')
    await waitFor(() => notes.filter(x => x.params?.meta?.event_type === 'reaction').length >= 110, 10000)
    const count = journal(d).length
    for (let i = 0; i < 60; i++) ownerMsg('g7', `big ${i} ${'x'.repeat(4000)}`, 6000 + i)
    await waitFor(() => notes.filter(x => /^big \d+ /.test(String(x.params?.content))).length >= 60, 15000)
    await Bun.sleep(300)
    const size = statSync(join(d, 'inbound-journal.jsonl')).size
    const j = journal(d)
    check('journal stays bounded: last 100 events, ≤200 KB (newest kept), file 0600',
      count === 100 && size <= 200 * 1024 && j.length > 0 && j.at(-1)?.message_id === '6059' && mode(join(d, 'inbound-journal.jsonl')) === 0o600,
      { count, size, n: j.length, last: j.at(-1)?.message_id })
    c.sim.stop(); await c.close()
  }

  for (const d of dirs) rmSync(d, { recursive: true, force: true })
}

check('no test instance ever ran the real paperless (PATH guard)', !existsSync(guardMark), existsSync(guardMark) ? readFileSync(guardMark, 'utf8') : '')
server.stop(true)
for (const f of [f1, f2, state, guardBin]) rmSync(f, { recursive: true, force: true })
console.log(failures ? `\n${failures} FAILED` : '\nall passed')
process.exit(failures ? 1 : 0)
