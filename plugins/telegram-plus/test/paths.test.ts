// Offline tests for server.ts paths offline.ts doesn't reach (test audit 2026-10-15):
// inline button specs and on_press remove/keep, the rich → plain fallback with
// splitting, long-message splitting, file limits and markup placement, Stop on a
// draft, and the outbound allowlist gate on every chat tool.
//
// Same style as offline.ts: server.ts runs as a child over stdio (MCP client),
// against a mock Bot API on 127.0.0.1 with a fake token and a temp state dir/HOME.
// Never touches api.telegram.org, the real state dir or its token file: the
// token comes from a .env in the temp state dir, which the server must chmod
// (proof that its one ENV_FILE is the temp one). Polls the mock only.
// Run:  bun test   (or: bun test test/paths.test.ts)
//
// test.failing marks a known bug: it passes while the bug is there and fails
// once fixed (then drop ".failing"). Both pinned bugs of 2026-10-15 (fallback
// quoting, emoji splits) are fixed in 0.1.13, together with missing/numeric
// chat_id and the "callback_data" alias.
import { describe, expect, test, beforeAll, afterAll, setDefaultTimeout } from 'bun:test'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, statSync, truncateSync, chmodSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { png } from './images.ts'

setDefaultTimeout(30_000)

const OWNER = 111
const STRANGER = 999
const GROUP = -100123
const OTHER_GROUP = -100777
const TOKEN = '123456:PATHS-not-a-real-token'
const O = String(OWNER)

// ---------------------------------------------------------------- mock Bot API
type Call = { method: string; params: any }
const calls: Call[] = []
const updates: any[] = []
let updateId = 1
let msgId = 5000
let foreign = 0 // requests with another token or path: must stay 0

const ok = (result: unknown) => Response.json({ ok: true, result })
const fail = (code: number, description: string) => Response.json({ ok: false, error_code: code, description }, { status: code })
const msg = (chat_id: any, extra: any = {}) => {
  const id = Number(chat_id)
  return { message_id: ++msgId, date: Math.floor(Date.now() / 1000), chat: { id, type: id < 0 ? 'supergroup' : 'private' }, ...extra }
}

async function parse(req: Request): Promise<any> {
  const ct = req.headers.get('content-type') ?? ''
  if (ct.includes('json')) return req.json()
  if (ct.includes('multipart')) {
    const o: any = {}
    for (const [k, v] of (await req.formData()).entries()) {
      if (typeof v !== 'string') { o[k] = `[file:${(v as File).name}]`; continue }
      try { o[k] = JSON.parse(v) } catch { o[k] = v }
    }
    // grammY uploads a file as its own field and points at it with "attach://<field>"
    for (const k of Object.keys(o)) if (typeof o[k] === 'string' && o[k].startsWith('attach://')) o[k] = o[o[k].slice(9)] ?? o[k]
    return o
  }
  const t = await req.text()
  return t ? JSON.parse(t) : {}
}

const api = Bun.serve({
  port: 0,
  hostname: '127.0.0.1',
  async fetch(req) {
    const m = /^\/bot([^/]+)\/(\w+)$/.exec(new URL(req.url).pathname)
    if (!m || m[1] !== TOKEN) { foreign++; return new Response('nope', { status: 404 }) }
    const method = m[2]!
    const p = await parse(req)
    if (method === 'getUpdates') {
      const offset = Number(p.offset ?? 0)
      while (offset > 0 && updates.length && updates[0].update_id < offset) updates.shift()
      const t0 = Date.now()
      while (!updates.length && Date.now() - t0 < 300) await Bun.sleep(20)
      return ok(updates.slice(0, Number(p.limit) || 100))
    }
    calls.push({ method, params: p })
    const body = String(p.text ?? p.rich_message?.markdown ?? '')
    switch (method) {
      case 'getMe': return ok({ id: 1, is_bot: true, first_name: 'T', username: 'testbot' })
      case 'sendMessage':
        if (body.includes('FAILSEND')) return fail(500, 'Internal Server Error: test')
        if (p.parse_mode && body.includes('BADPARSE')) return fail(400, "Bad Request: can't parse entities")
        return ok(msg(p.chat_id, { text: p.text }))
      case 'sendRichMessage':
        if (body.includes('BADRICH')) return fail(400, 'Bad Request: rich message is invalid')
        if (body.includes('RICH403')) return fail(403, 'Forbidden: bot was blocked by the user')
        return ok(msg(p.chat_id, { rich_message: {} }))
      case 'editMessageText':
        if (body.includes('NOEDIT')) return fail(400, 'Bad Request: MESSAGE_TOO_LONG')
        return ok(true)
      case 'sendPhoto': case 'sendDocument': return ok(msg(p.chat_id))
      case 'sendMediaGroup': return ok(p.media.map(() => msg(p.chat_id)))
      default: return ok(true)
    }
  },
})

// ---------------------------------------------------------------- server under test
const STATE = mkdtempSync(join(tmpdir(), 'tgp-paths-state-'))
const FILES = mkdtempSync(join(tmpdir(), 'tgp-paths-files-'))
const GUARD = mkdtempSync(join(tmpdir(), 'tgp-paths-guard-'))
const ENV_IN_STATE = join(STATE, '.env')
const ACCESS = { dmPolicy: 'allowlist', allowFrom: [O], groups: { [String(GROUP)]: { requireMention: true, allowFrom: [] } }, pending: {} }
const writeAccess = (extra: Record<string, unknown> = {}) =>
  writeFileSync(join(STATE, 'access.json'), JSON.stringify({ ...ACCESS, ...extra }))

// The server's env: no token (it must come from the temp .env), temp HOME and
// state dir, no CLAUDE_CONFIG_DIR, the mock as API root, and no local commands.
const SERVER_ENV: Record<string, string> = {
  PATH: `${GUARD}:${process.env.PATH!}`,
  HOME: STATE,
  TELEGRAM_STATE_DIR: STATE,
  TELEGRAM_API_ROOT: `http://127.0.0.1:${api.port}`,
  TELEGRAM_FORCE_POLL: '1',
  TELEGRAM_TRANSCRIBE_CMD: '',
  TELEGRAM_STATUS_CMD: '',
  TELEGRAM_SCAN_PRIVATE_CMD: '',
  TELEGRAM_INSTANT_FILE: '',
}

const notes: any[] = []
let log = ''
let client: Client

beforeAll(async () => {
  // Hard preconditions: never the real state dir, never a real API, never a real token.
  for (const d of [STATE, SERVER_ENV.HOME!]) {
    if (!d.startsWith(tmpdir()) || d.includes('/.claude/')) throw new Error(`refusing to run: state/home not a temp dir: ${d}`)
  }
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(SERVER_ENV.TELEGRAM_API_ROOT!)) throw new Error('refusing to run: API root is not the local mock')
  if ('TELEGRAM_BOT_TOKEN' in SERVER_ENV || 'CLAUDE_CONFIG_DIR' in SERVER_ENV) throw new Error('refusing to run: token/config dir in the server env')
  // A "paperless" that refuses, first on PATH (as in offline.ts).
  writeFileSync(join(GUARD, 'paperless'), '#!/bin/sh\necho "paperless: blocked in tests" >&2\nexit 1\n', { mode: 0o755 })
  writeAccess()
  writeFileSync(ENV_IN_STATE, `TELEGRAM_BOT_TOKEN=${TOKEN}\n`)
  chmodSync(ENV_IN_STATE, 0o644) // the server must make it 0600 (umask-proof)

  const transport = new StdioClientTransport({
    command: process.execPath, args: ['server.ts'], cwd: join(import.meta.dir, '..'),
    env: SERVER_ENV, stderr: 'pipe',
  })
  transport.stderr?.on('data', (d: Buffer) => { log += d.toString() })
  client = new Client({ name: 'paths-test', version: '0' })
  client.fallbackNotificationHandler = async n => { notes.push(n) }
  await client.connect(transport)
  if (!await waitFor(() => /polling as @testbot/.test(log), 8000)) throw new Error(`server did not start polling the mock:\n${log}`)
})

afterAll(async () => {
  await client?.close().catch(() => {})
  api.stop(true)
  for (const d of [STATE, FILES, GUARD]) rmSync(d, { recursive: true, force: true })
})

// ---------------------------------------------------------------- helpers
async function waitFor(pred: () => boolean, ms = 3000): Promise<boolean> {
  const t = Date.now()
  while (!pred() && Date.now() - t < ms) await Bun.sleep(20)
  return pred()
}
const since = (n: number) => calls.slice(n)
const sent = (n: number, method: string) => since(n).filter(c => c.method === method)
const call = (name: string, args: Record<string, unknown>) => client.callTool({ name, arguments: args }) as Promise<any>
const text = (r: any) => r.content?.[0]?.text as string
const idOf = (r: any) => Number(/id: (\d+)/.exec(text(r))![1])
const push = (u: any) => updates.push({ update_id: updateId++, ...u })
const user = (id: number) => ({ id, is_bot: false, first_name: 'U', username: id === OWNER ? 'owner' : 'stranger' })
const events = (type: string) => notes.filter(n => n.method === 'notifications/claude/channel' && n.params?.meta?.event_type === type)
const EDITS = ['editMessageText', 'editMessageCaption', 'editMessageReplyMarkup']
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/

let cq = 0
/** The owner (or `from`) presses `data` on message `message` in his DM; resolves with the button event. */
async function press(message: Record<string, unknown>, data: string, from = OWNER): Promise<any> {
  const before = events('button').length
  push({ callback_query: { id: `pcq${++cq}`, from: user(from), chat_instance: 'x', data, message: { date: 0, chat: { id: OWNER, type: 'private' }, ...message } } })
  await waitFor(() => events('button').length > before)
  return events('button')[before]
}

// ---------------------------------------------------------------- 1. isolation
describe('isolation', () => {
  test('the token came from the temp state dir: the server chmod-ed that .env (its only ENV_FILE) to 0600', () => {
    expect(statSync(ENV_IN_STATE).mode & 0o777).toBe(0o600)
    expect(log).toMatch(/polling as @testbot/)
    expect(calls.some(c => c.method === 'getMe')).toBe(true)
    expect(foreign).toBe(0)
  })
})

// ---------------------------------------------------------------- 2. inline buttons
describe('inline buttons: data vs callback_data', () => {
  test('string → callback_data = label; {data} → data; {url} → no callback_data; a flat list is one row', async () => {
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: 'Pick', buttons: ['A', { text: 'Bee', data: 'b' }, { text: 'Web', url: 'https://example.com' }] })
    expect(r.isError).toBeFalsy()
    const kb = sent(n, 'sendMessage')[0]!.params.reply_markup.inline_keyboard
    expect(kb).toEqual([[{ text: 'A', callback_data: 'A' }, { text: 'Bee', callback_data: 'b' }, { text: 'Web', url: 'https://example.com' }]])
  })

  test('"callback_data" is an alias for "data" (no longer ignored); "data" wins when both are given', async () => {
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: 'Pick', buttons: [[{ text: 'Yes please', callback_data: 'yes' }, { text: 'Both', data: 'd', callback_data: 'cd' }]] })
    expect(r.isError).toBeFalsy()
    expect(sent(n, 'sendMessage')[0]!.params.reply_markup.inline_keyboard).toEqual([[
      { text: 'Yes please', callback_data: 'yes' },
      { text: 'Both', callback_data: 'd' },
    ]])
  })

  test('"callback_data" gets the same checks as "data" (64 bytes, "perm:") and works in edit_message too', async () => {
    const n = calls.length
    const r1 = await call('reply', { chat_id: O, text: 'x', buttons: [[{ text: 'ü', callback_data: 'ü'.repeat(33) }]] })
    const r2 = await call('reply', { chat_id: O, text: 'x', buttons: [[{ text: 'Allow', callback_data: 'perm:allow:abcde' }]] })
    expect(r1.isError && text(r1)).toMatch(/button data over 64 bytes/)
    expect(r2.isError && text(r2)).toMatch(/may not start with "perm:"/)
    expect(sent(n, 'sendMessage')).toHaveLength(0)
    const id = idOf(await call('reply', { chat_id: O, text: 'Edit me' }))
    const m = calls.length
    const r3 = await call('edit_message', { chat_id: O, message_id: String(id), text: 'Edited', buttons: [[{ text: 'Go', callback_data: 'go' }]] })
    expect(r3.isError).toBeFalsy()
    const e = since(m).find(c => c.method === 'editMessageText')
    expect(e?.params.reply_markup?.inline_keyboard).toEqual([[{ text: 'Go', callback_data: 'go' }]])
  })

  test('refused before anything is sent: data over 64 UTF-8 bytes, "perm:" data, a button without text', async () => {
    const n = calls.length
    const r1 = await call('reply', { chat_id: O, text: 'x', buttons: [[{ text: 'ü', data: 'ü'.repeat(33) }]] }) // 33 chars, 66 bytes
    const r2 = await call('reply', { chat_id: O, text: 'x', buttons: [[{ text: 'Allow', data: 'perm:allow:abcde' }]] })
    const r3 = await call('reply', { chat_id: O, text: 'x', buttons: [[{ data: 'x' }]] })
    expect(r1.isError && text(r1)).toMatch(/button data over 64 bytes/)
    expect(r2.isError && text(r2)).toMatch(/may not start with "perm:"/)
    expect(r3.isError && text(r3)).toMatch(/every button needs text/)
    expect(sent(n, 'sendMessage')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------- 3. on_press
describe('on_press after a button press', () => {
  test('"remove": buttons cleared (editMessageReplyMarkup), no ✓ line; the event still arrives', async () => {
    const r = await call('reply', { chat_id: O, text: 'Remove me', buttons: [['A', 'B']], on_press: 'remove' })
    const id = idOf(r)
    const n = calls.length
    const ev = await press({ message_id: id, text: 'Remove me', reply_markup: { inline_keyboard: [[{ text: 'A', callback_data: 'A' }, { text: 'B', callback_data: 'B' }]] } }, 'B')
    expect(ev.params.meta).toMatchObject({ callback_data: 'B', button_text: 'B', message_id: String(id), message_text: 'Remove me', chat_id: O })
    const edits = since(n).filter(c => EDITS.includes(c.method))
    expect(edits.map(c => c.method)).toEqual(['editMessageReplyMarkup'])
    expect(edits[0]!.params).toMatchObject({ message_id: id, reply_markup: { inline_keyboard: [] } })
    expect(sent(n, 'answerCallbackQuery')[0]?.params.text).toBe('✓ B')
  })

  test('"keep": no edits at all, every press is delivered', async () => {
    const r = await call('reply', { chat_id: O, text: 'Counter', buttons: [[{ text: '+1', data: 'inc' }]], on_press: 'keep' })
    const id = idOf(r)
    const n = calls.length
    const m = { message_id: id, text: 'Counter', reply_markup: { inline_keyboard: [[{ text: '+1', callback_data: 'inc' }]] } }
    const e1 = await press(m, 'inc')
    const e2 = await press(m, 'inc')
    await Bun.sleep(150)
    expect([e1?.params.meta.button_text, e2?.params.meta.button_text]).toEqual(['+1', '+1'])
    expect(since(n).filter(c => EDITS.includes(c.method))).toHaveLength(0)
    expect(sent(n, 'answerCallbackQuery')).toHaveLength(2)
  })

  test('"mark" (default): when the ✓ edit is refused, the buttons are still removed', async () => {
    const r = await call('reply', { chat_id: O, text: 'NOEDIT pick', buttons: [['Go']] })
    const id = idOf(r)
    const n = calls.length
    await press({ message_id: id, text: 'NOEDIT pick', reply_markup: { inline_keyboard: [[{ text: 'Go', callback_data: 'Go' }]] } }, 'Go')
    const edits = since(n).filter(c => EDITS.includes(c.method))
    expect(edits.map(c => c.method)).toEqual(['editMessageText', 'editMessageReplyMarkup'])
    expect(edits[0]!.params.text).toBe('NOEDIT pick\n\n✓ Go')
    expect(edits[1]!.params.reply_markup).toEqual({ inline_keyboard: [] })
  })
})

// ---------------------------------------------------------------- 4. rich → plain fallback
describe('rich → plain fallback', () => {
  const lines = Array.from({ length: 90 }, (_, i) => `BADRICH line ${i} ` + 'x'.repeat(85))
  const long = lines.join('\n') // ~9000 chars: one rich chunk, three plain parts
  let parts: Call[] = []

  test('a rejected long rich reply → plain parts ≤ 4096 split at line ends; effect on the first, buttons and on_press on the last', async () => {
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: long, format: 'rich', buttons: [['OK']], on_press: 'remove', effect: 'party', reply_to: '7' })
    expect(r.isError).toBeFalsy()
    expect(sent(n, 'sendRichMessage')).toHaveLength(1)
    parts = sent(n, 'sendMessage')
    expect(parts.length).toBe(3)
    expect(text(r)).toMatch(/^sent 3 parts \(ids: /)
    expect(text(r)).toMatch(/note: rich rejected \(.*rich message is invalid\)+; sent as plain text/)
    for (const p of parts) {
      expect(p.params.text.length).toBeLessThanOrEqual(4096)
      expect(p.params.parse_mode).toBeUndefined()
      expect(p.params.rich_message).toBeUndefined()
      expect(p.params.text.startsWith('\n')).toBe(false)
    }
    expect(parts.map(p => p.params.text).join('\n')).toBe(long)
    expect(parts.map(p => p.params.message_effect_id != null)).toEqual([true, false, false])
    expect(parts.map(p => p.params.reply_markup != null)).toEqual([false, false, true])
    expect(parts[0]!.params.reply_parameters).toEqual({ message_id: 7 })
    // on_press "remove" was remembered for the last part (the one with the buttons)
    const lastId = Number(text(r).match(/(\d+)\)/)![1])
    const m = calls.length
    await press({ message_id: lastId, text: parts[2]!.params.text, reply_markup: parts[2]!.params.reply_markup }, 'OK')
    expect(since(m).filter(c => EDITS.includes(c.method)).map(c => c.method)).toEqual(['editMessageReplyMarkup'])
  })

  // Was a bug until 0.1.13: sendFormatted's fallback spread reply_parameters into
  // every plain part, so with replyToMode "first" all parts quoted the message.
  test('only the first fallback part quotes reply_to (replyToMode "first")', () => {
    expect(parts.length).toBe(3)
    expect(parts.map(p => p.params.reply_parameters != null)).toEqual([true, false, false])
  })

  test('markdownv2 rejected → plain text without parse_mode; a non-400 error (403) → no fallback, an error', async () => {
    let n = calls.length
    const r1 = await call('reply', { chat_id: O, text: 'BADPARSE *x', format: 'markdownv2' })
    const s = sent(n, 'sendMessage')
    expect(s.map(c => c.params.parse_mode)).toEqual(['MarkdownV2', undefined])
    expect(s[1]!.params.text).toBe('BADPARSE *x')
    expect(text(r1)).toMatch(/markdownv2 rejected .*sent as plain text/)
    n = calls.length
    const r2 = await call('reply', { chat_id: O, text: 'RICH403 hi', format: 'rich' })
    expect(r2.isError).toBe(true)
    expect(text(r2)).toMatch(/reply failed after 0 of 1 chunk\(s\) sent: .*blocked/)
    expect(sent(n, 'sendMessage')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------- 5. long-message splitting
describe('long-message splitting (plain text)', () => {
  test('default (length mode): 10000 chars → 4096 + 4096 + 1808, lossless; reply_to/effect on the first, buttons on the last, silent on all', async () => {
    const t = Array.from({ length: 10000 }, (_, i) => String.fromCharCode(97 + (i % 26))).join('')
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: t, reply_to: '5', effect: 'party', buttons: [['Next']], silent: true })
    const s = sent(n, 'sendMessage')
    expect(text(r)).toMatch(/^sent 3 parts/)
    expect(s.map(c => c.params.text.length)).toEqual([4096, 4096, 1808])
    expect(s.map(c => c.params.text).join('')).toBe(t)
    expect(s.map(c => c.params.reply_parameters != null)).toEqual([true, false, false])
    expect(s.map(c => c.params.message_effect_id != null)).toEqual([true, false, false])
    expect(s.map(c => c.params.reply_markup != null)).toEqual([false, false, true])
    expect(s.every(c => c.params.disable_notification === true)).toBe(true)
  })

  test('access.json chunkMode "newline" + textChunkLimit: split at paragraphs, then at spaces; replyToMode "all" quotes every part', async () => {
    writeAccess({ chunkMode: 'newline', textChunkLimit: 100, replyToMode: 'all' })
    try {
      const p = (c: string) => c.repeat(60)
      const paras = `${p('a')}\n\n${p('b')}\n\n${p('c')}`
      let n = calls.length
      await call('reply', { chat_id: O, text: paras, reply_to: '3' })
      let s = sent(n, 'sendMessage')
      expect(s.map(c => c.params.text)).toEqual([p('a'), p('b'), p('c')])
      expect(s.every(c => c.params.reply_parameters?.message_id === 3)).toBe(true)
      const words = Array.from({ length: 50 }, (_, i) => `w${String(i).padStart(3, '0')}`).join(' ') // 249 chars, no newline
      n = calls.length
      await call('reply', { chat_id: O, text: words })
      s = sent(n, 'sendMessage')
      expect(s.length).toBe(3)
      expect(s.every(c => c.params.text.length <= 100)).toBe(true)
      expect(s.map(c => c.params.text).join('')).toBe(words)
      expect(s.every(c => /^w\d{3}( w\d{3})*$/.test(c.params.text.trim()))).toBe(true) // whole words only
    } finally {
      writeAccess()
    }
  })

  test('a failing middle part → "reply failed after 1 of 3 chunk(s) sent"; the rest is not attempted', async () => {
    const t = 'a'.repeat(4096) + 'FAILSEND' + 'b'.repeat(5000)
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: t })
    expect(r.isError).toBe(true)
    expect(text(r)).toMatch(/reply failed after 1 of 3 chunk\(s\) sent/)
    expect(sent(n, 'sendMessage')).toHaveLength(2)
  })

  // Was a bug until 0.1.13: chunk() cut at a raw UTF-16 index, so in length mode (the
  // default: the live access.json sets no chunkMode) an emoji at the 4096 boundary was
  // split into two lone surrogates (garbled characters, or Telegram rejects the part).
  test('a split never cuts an emoji (surrogate pair) in half', async () => {
    const t = 'a'.repeat(4095) + '😀' + ' tail'
    const n = calls.length
    await call('reply', { chat_id: O, text: t })
    const s = sent(n, 'sendMessage')
    expect(s.length).toBe(2)
    for (const c of s) expect(LONE_SURROGATE.test(c.params.text)).toBe(false)
    expect(s.map(c => c.params.text)).toEqual(['a'.repeat(4095), '😀 tail'])
  })

  test('splits land on grapheme boundaries: ZWJ family, flags, skin tones and accents stay whole', async () => {
    const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    const g = (x: string) => [...seg.segment(x)].map(p => p.segment)
    writeAccess({ textChunkLimit: 12 })
    try {
      const t = 'xy' + '👨‍👩‍👧‍👦' + '🇨🇭🇩🇪🇮🇹' + '👍🏽'.repeat(3) + 'e\u0301'.repeat(9) + 'end' // 'xy' misaligns every 12-unit cut
      const n = calls.length
      const r = await call('reply', { chat_id: O, text: t })
      expect(r.isError).toBeFalsy()
      const parts = sent(n, 'sendMessage').map(c => c.params.text as string)
      expect(parts.length).toBeGreaterThan(3)
      expect(parts.join('')).toBe(t)
      for (const p of parts) {
        expect(p.length).toBeLessThanOrEqual(12)
        expect(LONE_SURROGATE.test(p)).toBe(false)
      }
      expect(parts.flatMap(g)).toEqual(g(t)) // no grapheme was cut
    } finally {
      writeAccess()
    }
  })

  test('one grapheme longer than the limit: cut between code points, never mid surrogate pair, lossless', async () => {
    writeAccess({ textChunkLimit: 11 })
    try {
      const t = '👍' + '🏽'.repeat(10) + 'z' // one 22-unit grapheme + z
      const n = calls.length
      await call('reply', { chat_id: O, text: t })
      const parts = sent(n, 'sendMessage').map(c => c.params.text as string)
      expect(parts.join('')).toBe(t)
      for (const p of parts) {
        expect(p.length).toBeLessThanOrEqual(11)
        expect(LONE_SURROGATE.test(p)).toBe(false)
      }
    } finally {
      writeAccess()
    }
  })
})

// ---------------------------------------------------------------- 6. files
describe('files', () => {
  test('over 50 MB → refused up front: nothing is sent, not even the text', async () => {
    const big = join(FILES, 'big.bin')
    writeFileSync(big, '')
    truncateSync(big, 51 * 1024 * 1024) // sparse
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: 'with a big file', files: [big] })
    expect(r.isError).toBe(true)
    expect(text(r)).toMatch(/file too large: .*big\.bin \(51\.0MB, max 50MB\)/)
    expect(since(n).filter(c => /^send(Message|Document|Photo|MediaGroup)$/.test(c.method))).toHaveLength(0)
  })

  test('text + files: text first with the buttons; then .PNG as a photo, .pdf as a document, both quoting reply_to', async () => {
    const pngUpper = join(FILES, 'Shot.PNG')
    const pdf = join(FILES, 'doc.pdf')
    writeFileSync(pngUpper, png(1280, 720))
    writeFileSync(pdf, '%PDF-1.4 test')
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: 'see files', files: [pngUpper, pdf], buttons: [['Got it']], reply_to: '9' })
    expect(text(r)).toMatch(/^sent 3 parts/)
    const s = since(n).filter(c => /^send(Message|Document|Photo)$/.test(c.method))
    expect(s.map(c => c.method)).toEqual(['sendMessage', 'sendPhoto', 'sendDocument'])
    expect(s[0]!.params.reply_markup?.inline_keyboard?.[0]?.[0]?.text).toBe('Got it')
    expect(s[1]!.params.photo).toBe('[file:Shot.PNG]')
    expect(s[2]!.params.document).toBe('[file:doc.pdf]')
    expect(s.slice(1).every(c => c.params.reply_markup == null && c.params.reply_parameters?.message_id === 9)).toBe(true)
  })

  test('files only + buttons: the markup goes on the last file; a press there removes the buttons', async () => {
    const a = join(FILES, 'a.png')
    const b = join(FILES, 'b.pdf')
    writeFileSync(a, png(800, 600))
    writeFileSync(b, '%PDF-1.4 b')
    const n = calls.length
    const r = await call('reply', { chat_id: O, text: '', files: [a, b], buttons: [['Seen']] })
    const s = since(n).filter(c => /^send(Document|Photo)$/.test(c.method))
    expect(s.map(c => [c.method, c.params.reply_markup != null])).toEqual([['sendPhoto', false], ['sendDocument', true]])
    const lastId = Number(text(r).match(/(\d+)\)$/)![1])
    const m = calls.length
    const ev = await press({ message_id: lastId, document: { file_id: 'x', file_unique_id: 'ux' }, reply_markup: s[1]!.params.reply_markup }, 'Seen')
    expect(ev.params.meta.button_text).toBe('Seen')
    expect(since(m).filter(c => EDITS.includes(c.method)).map(c => c.method)).toEqual(['editMessageReplyMarkup'])
  })

  test('a file in the state inbox may be sent; the state dir\'s token file may not', async () => {
    mkdirSync(join(STATE, 'inbox'), { recursive: true })
    const inboxFile = join(STATE, 'inbox', 'x.pdf')
    writeFileSync(inboxFile, '%PDF-1.4 x')
    const n = calls.length
    const r1 = await call('reply', { chat_id: O, text: '', files: [inboxFile] })
    expect(r1.isError).toBeFalsy()
    expect(sent(n, 'sendDocument')).toHaveLength(1)
    const m = calls.length
    const r2 = await call('reply', { chat_id: O, text: '', files: [ENV_IN_STATE] })
    expect(r2.isError && text(r2)).toMatch(/refusing to send channel state/)
    expect(since(m).filter(c => /^send/.test(c.method))).toHaveLength(0)
  })
})

// ---------------------------------------------------------------- 7. drafts and Stop
describe('drafts and the Stop event', () => {
  const draftsSince = (n: number) => sent(n, 'sendMessageDraft')

  test('a stranger\'s Stop is dropped; a Stop for another draft is delivered but leaves the live draft running; a matching Stop ends it', async () => {
    let n = calls.length
    await call('draft', { chat_id: O, text: 'step 1', can_stop: true })
    expect(await waitFor(() => draftsSince(n).length > 0)).toBe(true)
    const id1 = draftsSince(n)[0]!.params.draft_id as number
    expect(draftsSince(n)[0]!.params.can_stop).toBe(true)

    const stops0 = events('stop').length
    push({ stopped_message_generation: { chat: { id: STRANGER, type: 'private' }, draft_id: id1 } })
    push({ stopped_message_generation: { chat: { id: OWNER, type: 'private' }, draft_id: id1 + 1 } })
    await waitFor(() => events('stop').length > stops0)
    await Bun.sleep(300)
    const got = events('stop').slice(stops0)
    expect(got.map(e => e.params.meta.draft_id)).toEqual([String(id1 + 1)])
    expect(got[0].params.meta.chat_id).toBe(O)

    // stale Stop: the live draft continues under the same id
    n = calls.length
    await call('draft', { chat_id: O, text: 'step 2', append: true })
    expect(await waitFor(() => draftsSince(n).some(c => String(c.params.text).includes('step 2')), 4000)).toBe(true)
    expect(draftsSince(n).every(c => c.params.draft_id === id1)).toBe(true)

    // matching Stop: delivered, and the next draft call starts a fresh draft
    push({ stopped_message_generation: { chat: { id: OWNER, type: 'private' }, draft_id: id1 } })
    expect(await waitFor(() => events('stop').some(e => e.params.meta.draft_id === String(id1)))).toBe(true)
    n = calls.length
    await call('draft', { chat_id: O, text: 'after stop' })
    expect(await waitFor(() => draftsSince(n).some(c => c.params.draft_id !== id1), 4000)).toBe(true)
    await call('reply', { chat_id: O, text: 'done' })
  })
})

// ---------------------------------------------------------------- 8. allowlist (outbound gate)
describe('outbound allowlist gate', () => {
  const tools: [string, Record<string, unknown>][] = [
    ['reply', { text: 'x' }],
    ['react', { message_id: '1', emoji: '👍' }],
    ['edit_message', { message_id: '1', text: 'x' }],
    ['draft', { text: 'x' }],
    ['chat_action', { action: 'typing' }],
    ['send_poll', { question: 'q', options: ['a', 'b'] }],
    ['send_location', { latitude: 1, longitude: 2 }],
    ['pin_message', { message_id: '1' }],
    ['delete_message', { message_id: '1' }],
  ]

  test('every chat tool refuses a stranger (also as a number) and an unknown group; no Bot API call', async () => {
    await Bun.sleep(300) // let typing from earlier presses settle
    const n = calls.length
    const bad: string[] = []
    for (const [name, args] of tools) {
      for (const [label, chat] of [['stranger', String(STRANGER)], ['unknown group', String(OTHER_GROUP)], ['numeric stranger id', STRANGER], ['numeric unknown group', OTHER_GROUP]] as const) {
        const r = await call(name, { ...args, chat_id: chat })
        const want = new RegExp(`^${name} failed: chat ${String(chat)} is not allowlisted`)
        if (!r.isError || !want.test(text(r))) bad.push(`${name}/${label}: ${text(r)}`)
      }
    }
    expect(bad).toEqual([])
    expect(since(n).filter(c => c.method !== 'sendChatAction')).toEqual([])
    expect(since(n).filter(c => String(c.params.chat_id) !== O)).toEqual([])
  })

  test('every chat tool says "chat_id is required" for a missing, null or empty chat_id (was "chat undefined"); a bad type is refused too', async () => {
    const n = calls.length
    const bad: string[] = []
    for (const [name, args] of tools) {
      for (const [label, extra, want] of [
        ['missing', {}, /chat_id is required/],
        ['null', { chat_id: null }, /chat_id is required/],
        ['empty', { chat_id: '' }, /chat_id is required/],
        ['blank', { chat_id: '  ' }, /chat_id is required/],
        ['fraction', { chat_id: 1.5 }, /chat_id must be an integer/],
        ['object', { chat_id: { id: OWNER } }, /chat_id must be a string or a number/],
      ] as const) {
        const r = await call(name, { ...args, ...extra })
        if (!r.isError || !new RegExp(`^${name} failed: `).test(text(r)) || !want.test(text(r)) || /undefined/.test(text(r))) bad.push(`${name}/${label}: ${text(r)}`)
      }
    }
    expect(bad).toEqual([])
    expect(since(n).filter(c => c.method !== 'sendChatAction')).toEqual([])
  })

  test('a numeric chat_id is coerced to a string: the owner as a number passes the gate of every tool', async () => {
    const id = idOf(await call('reply', { chat_id: OWNER, text: 'numeric hello' }))
    const n = calls.length
    const bad: string[] = []
    const numeric: [string, Record<string, unknown>][] = [
      ['reply', { text: 'numeric again' }],
      ['react', { message_id: String(id), emoji: '👍' }],
      ['edit_message', { message_id: String(id), text: 'numeric edited' }],
      ['chat_action', { action: 'typing' }],
      ['send_location', { latitude: 1, longitude: 2 }],
      ['pin_message', { message_id: String(id) }],
      ['delete_message', { message_id: String(id) }],
    ]
    for (const [name, args] of numeric) {
      const r = await call(name, { ...args, chat_id: OWNER })
      if (r.isError) bad.push(`${name}: ${text(r)}`)
    }
    expect(bad).toEqual([])
    const methods = since(n).map(c => c.method)
    for (const m of ['sendMessage', 'setMessageReaction', 'editMessageText', 'sendChatAction', 'sendLocation', 'pinChatMessage', 'deleteMessage']) expect(methods).toContain(m)
    expect(since(n).every(c => c.params.chat_id === O)).toBe(true) // sent as the string id
  })

  test('an allowlisted group passes the gate', async () => {
    const n = calls.length
    const r = await call('reply', { chat_id: String(GROUP), text: 'hello group' })
    expect(r.isError).toBeFalsy()
    expect(sent(n, 'sendMessage')[0]?.params.chat_id).toBe(String(GROUP))
  })

  test('after everything: only the mock was reached, always with the fake token', () => {
    expect(foreign).toBe(0)
    expect(calls.length).toBeGreaterThan(20)
  })
})
