#!/usr/bin/python3 -I
"""Fixture tests for ops/channel-check (the watchdog): every incident type, the restart rate limits,
quiet hours, no alert storms. Temp dirs for every path, stubs for `tg` and `restart`, a fake clock and a
fake `claude agents --json`; never touches the real state, the owner's chat or the session.
Run:  python3 -I test/channel-check.py"""
import datetime, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, '..', 'ops', 'channel-check'))
# Afternoon local time (no quiet hours).
T0 = time.mktime(time.strptime(time.strftime('%Y-%m-%d', time.localtime()) + ' 14:00:00', '%Y-%m-%d %H:%M:%S'))
MIN = 60
failures = 0


def check(name, cond, detail=None):
    global failures
    if cond:
        print('  ok   %s' % name)
    else:
        failures += 1
        print('  FAIL %s %s' % (name, json.dumps(detail, default=str)[:900] if detail is not None else ''))


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


class Env:
    WORKSPACE = '/fake/workspace'

    def __init__(self, name):
        self.d = tempfile.mkdtemp(prefix='cc-%s-' % name)
        for sub in ('tg', 'state', 'logs', 'projects', 'sessions', 'inbox'):
            os.makedirs(self.p(sub))
        with open(self.p('tg', 'access.json'), 'w') as f:
            json.dump({'dmPolicy': 'allowlist', 'allowFrom': ['111']}, f)
        for stub in ('tg', 'restart'):
            path = self.p('%s-stub' % stub)
            with open(path, 'w') as f:
                f.write('#!/bin/sh\nprintf \'%%s\\n\' "$*" >> %s\n' % self.p('%s.out' % stub))
            os.chmod(path, 0o755)
        with open(self.p('server.ts'), 'w') as f:
            f.write('sleep 3600\n')
        self.server = subprocess.Popen(['sh', self.p('server.ts')], start_new_session=True)  # a live "server.ts" (cmdline has it)
        self.now = T0
        self.sid = 'sess-1'
        self.transcript = self.p('projects', '%s.jsonl' % self.sid)
        open(self.transcript, 'w').close()
        self.extra = {}
        self.autopoll = True  # a polling plugin refreshes last_poll_ok all the time

    def p(self, *a):
        return os.path.join(self.d, *a)

    def close(self):
        try:
            os.killpg(self.server.pid, 9)  # sh and its sleep
        except OSError:
            pass
        self.server.wait()
        shutil.rmtree(self.d, ignore_errors=True)

    def run(self, *args):
        h = self.p('tg', 'health.json')
        if self.autopoll and os.path.exists(h):
            d = json.load(open(h))
            if d.get('poll_role') == 'poll':
                d['last_poll_ok'] = iso(self.now - 20)
                json.dump(d, open(h, 'w'))
        env = {'PATH': os.environ['PATH'], 'HOME': self.d, 'CC_NOW': str(self.now), 'CC_STATE': self.p('state'),
               'CC_TG_DIR': self.p('tg'), 'CC_MCP_LOGS': self.p('logs'), 'CC_PROJECTS': self.p('projects'),
               'CC_SESSIONS': self.p('sessions'), 'CC_INBOX': self.p('inbox'), 'CC_WORKSPACE': self.WORKSPACE,
               'CC_AGENTS_JSON': self.p('agents.json'), 'CC_TG': self.p('tg-stub'), 'CC_RESTART': self.p('restart-stub'),
               'CC_STUCK_FLAG': self.p('stuck'), **self.extra}
        r = subprocess.run([SCRIPT, *args], env=env, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
        return r

    def at(self, t):
        self.now = t
        return self.run()

    def lines(self, name):
        try:
            with open(self.p(name)) as f:
                return [l for l in f.read().split('\n') if l]
        except OSError:
            return []

    def tg(self):
        return self.lines('tg.out')

    def restarts(self):
        return self.lines('restart.out')

    def notes(self):
        return sorted(os.listdir(self.p('inbox')))

    def state(self):
        with open(self.p('state', 'state.json')) as f:
            return json.load(f)

    def log(self):
        return '\n'.join(self.lines('state/log'))

    def botpid(self, pid=None):
        with open(self.p('tg', 'bot.pid'), 'w') as f:
            f.write('%d\ntelegram-plus\n' % (pid or self.server.pid))

    def health(self, **kw):
        h = {'version': '0.1.5', 'pid': self.server.pid, 'started_at': iso(self.now - 3600), 'poll_role': 'poll',
             'last_poll_ok': iso(self.now - 20), 'last_inbound': None,
             'last_tool_call': {'at': iso(self.now - 3600), 'name': 'reply'}, 'last_answer': None,
             'channels_accepted': True, 'channels_reason': None,
             'delivery': {'state': 'ok', 'reason': None, 'kind': None, 'since': iso(self.now - 3600)}}
        h.update(kw)
        with open(self.p('tg', 'health.json'), 'w') as f:
            json.dump(h, f)
        self.botpid(h['pid'])

    def blocked(self, kind, reason):
        self.health(poll_role='blocked', channels_accepted=kind == 'dropped',
                    delivery={'state': 'blocked', 'kind': kind, 'reason': reason, 'since': iso(self.now)})

    def session(self, status='idle', started_ago=3600, waiting=None, status_ago=60):
        a = {'pid': 4242, 'cwd': self.WORKSPACE, 'kind': 'interactive', 'startedAt': int((self.now - started_ago) * 1000),
             'sessionId': self.sid, 'status': status}
        if waiting:
            a['waitingFor'] = waiting
        with open(self.p('agents.json'), 'w') as f:
            json.dump([a], f)
        with open(self.p('sessions', '4242.json'), 'w') as f:
            json.dump({'pid': 4242, 'status': status, 'statusUpdatedAt': int((self.now - status_ago) * 1000)}, f)

    def owner_msg(self, mid, ago, status='acked', kind='message'):
        at = self.now - ago
        return {'id': 'j%d' % mid, 'at': iso(at), 'kind': kind, 'owner': True, 'chat_id': '111', 'message_id': str(mid),
                'ts': iso(int(at)), 'content': 'owner says %d' % mid, 'meta': {}, 'status': status, 'tries': 1, 'pid': self.server.pid}

    def journal(self, *entries):
        with open(self.p('tg', 'inbound-journal.jsonl'), 'w') as f:
            for e in entries:
                f.write(json.dumps(e) + '\n')

    def seen(self, e, kind='user'):
        tag = '<channel source="plugin:telegram-plus:telegram" chat_id="111" message_id="%s" user="o" ts="%s">\n%s\n</channel>' % (
            e['message_id'], e['ts'], e['content'])
        line = {'type': kind, 'message': {'role': 'user', 'content': tag}} if kind == 'user' else {'type': kind, 'attachment': {'content': tag}}
        with open(self.transcript, 'a') as f:
            f.write(json.dumps(line, separators=(',', ':')) + '\n')

    def api_error(self, text, uid, synthetic=True, **extra):
        """An assistant entry after the last one: Claude Code's own error record (synthetic), or model text."""
        line = {'type': 'assistant', 'uuid': uid, 'isApiErrorMessage': synthetic,
                'message': {'model': '<synthetic>' if synthetic else 'claude-opus-5-5', 'role': 'assistant',
                            'content': [{'type': 'text', 'text': text}], **extra}}
        with open(self.transcript, 'a') as f:
            f.write(json.dumps(line, separators=(',', ':')) + '\n')

    def mcp_log(self, *msgs, name='2026-10-09T12-00-00-000Z.jsonl'):
        d = self.p('logs', '-fake-workspace', 'mcp-logs-plugin-telegram-plus-telegram')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), 'w') as f:
            for m in msgs:
                f.write(json.dumps({'debug': m, 'timestamp': iso(self.now), 'sessionId': 's', 'cwd': '/x'}) + '\n')

    def dead_pid(self):
        p = subprocess.Popen(['true'])
        p.wait()
        return p.pid


print('— healthy: nothing to do, and no `claude agents` call')
e = Env('healthy')
e.health()
r = e.run()
check('healthy plugin: no owner message, no restart, no note, no incident',
      r.returncode == 0 and not e.tg() and not e.restarts() and not e.notes() and not e.state()['incidents'], e.log())
check('a healthy check does not call `claude agents`', 'session (claude agents)' not in e.log(), e.log())
r = e.run('--status')
check('--status prints the view', 'plugin:      telegram-plus 0.1.5' in r.stdout and 'delivery:    ok' in r.stdout, r.stdout)
e.close()

print('— not delivered (plugin reports it)')
e = Env('nd-plugin')
e.session('idle')
e.blocked('disabled', 'channels feature is not currently available')
with open(e.p('tg', 'delivery-alert.json'), 'w') as f:
    json.dump({'kind': 'disabled', 'reason': 'x', 'at': iso(e.now), 'pid': e.server.pid, 'sent': True}, f)
e.at(T0)
check('first sighting: wait for a second run (no action yet)', not e.tg() and not e.restarts() and not e.notes())
e.at(T0 + MIN)
check('second run: incident, inbox note, auto-restart of the idle session; no owner message (the plugin already sent one)',
      not e.tg() and len(e.restarts()) == 1 and e.restarts()[0].startswith('auto-recover: Claude Code isn\'t delivering')
      and any(n.startswith('channel-not_delivered-') for n in e.notes()), {'tg': e.tg(), 'restarts': e.restarts(), 'notes': e.notes()})
e.at(T0 + 3 * MIN)
check('still broken 2 min later: no repeat (one notice per incident; ≤1 restart per 15 min)', not e.tg() and len(e.restarts()) == 1)
e.health()
os.remove(e.p('tg', 'delivery-alert.json'))
e.at(T0 + 4 * MIN)
check('fixed: incident closed; no "back to normal" from the watchdog (the plugin sends its own)',
      not e.tg() and not e.state()['incidents'], e.state())
e.close()

print('— not delivered (no plugin alert) → owner notice, restart, back to normal')
e = Env('nd-own')
e.session('idle')
e.blocked('dropped', 'Claude Code dropped channel messages: channels were off at delivery time')
e.at(T0)
e.at(T0 + MIN)
check('owner gets one "⚠️ (auto)" notice; idle session restarted',
      len(e.tg()) == 1 and e.tg()[0].startswith('⚠️ (auto) Your Telegram messages aren\'t reaching me') and 'Remote Control' in e.tg()[0]
      and len(e.restarts()) == 1, {'tg': e.tg(), 'restarts': e.restarts()})
e.health()
e.at(T0 + 3 * MIN)
check('resolved → one "✅ (auto) Back to normal"', len(e.tg()) == 2 and e.tg()[1].startswith('✅ (auto) Back to normal'), e.tg())
e.close()

print('— not delivered because of config → no restart')
e = Env('nd-config')
e.session('idle')
e.blocked('allowlist', 'plugin telegram-plus@home-plugins is not on the approved channels allowlist')
e.at(T0)
e.at(T0 + MIN)
note = open(e.p('inbox', e.notes()[0])).read() if e.notes() else ''
check('allowlist/policy-type skip: notice + note saying it is config; no restart (it would not help)',
      len(e.tg()) == 1 and not e.restarts() and 'config' in note, {'tg': e.tg(), 'note': note, 'log': e.log()})
e.close()

print('— older plugin (no health.json): Claude Code\'s MCP log')
e = Env('legacy')
e.session('idle')
e.botpid()
e.mcp_log('Starting connection with timeout of 30000ms', 'Channel notifications registered', 'Starting connection with timeout of 30000ms',
          'Successfully connected (transport: stdio) in 123ms',
          'Channel notifications skipped: channels feature is not currently available')
e.at(T0)
e.at(T0 + MIN)
check('newest log segment says "skipped": notice (messages may be lost) + restart',
      len(e.tg()) == 1 and 'not currently available' in e.tg()[0] and 'may be lost' in e.tg()[0] and len(e.restarts()) == 1, {'tg': e.tg(), 'r': e.restarts()})
e.mcp_log('Starting connection with timeout of 30000ms', 'Channel notifications registered', name='2026-10-09T13-00-00-000Z.jsonl')
os.utime(e.p('logs', '-fake-workspace', 'mcp-logs-plugin-telegram-plus-telegram', '2026-10-09T13-00-00-000Z.jsonl'), (time.time() + 5, time.time() + 5))
e.at(T0 + 3 * MIN)
check('the new session\'s log says "registered": back to normal', len(e.tg()) == 2 and 'Back to normal' in e.tg()[1], e.tg())
e.close()

e = Env('noverdict')
e.session('idle')
e.botpid()
e.mcp_log('Starting connection with timeout of 30000ms', 'Successfully connected (transport: stdio) in 99ms',
          'Connection established with capabilities: {}')
e.at(T0 + 30)
e.at(T0 + 50)
check('connected <60 s ago without a verdict line yet: nothing', not e.tg() and not e.state()['incidents'])
e.at(T0 + 2 * MIN)
e.at(T0 + 3 * MIN)
e.at(T0 + 10 * MIN)
check('no "registered"/"skipped" line a minute after connecting: one "can\'t confirm" notice, a note, no restart',
      len(e.tg()) == 1 and "can't confirm" in e.tg()[0] and not e.restarts() and any('not_delivered' in n for n in e.notes()),
      {'tg': e.tg(), 'r': e.restarts(), 'log': e.log()})
e.close()

print('— poller down / stuck')
e = Env('poller')
e.session('idle')
dead = e.dead_pid()
e.health(pid=dead)
e.at(T0)
check('dead bot.pid, first sighting: nothing yet', not e.tg() and not e.restarts())
e.at(T0 + MIN)
check('dead bot.pid, second run: notice + restart',
      len(e.tg()) == 1 and 'connection is down' in e.tg()[0] and len(e.restarts()) == 1, {'tg': e.tg(), 'r': e.restarts()})
e.health()
e.at(T0 + 4 * MIN)
check('poller back: "back to normal"', len(e.tg()) == 2 and 'Back to normal' in e.tg()[1], e.tg())
e.close()

e = Env('stuck')
e.autopoll = False
e.session('idle')
e.health(last_poll_ok=iso(T0 - 5 * MIN))
e.at(T0)
e.at(T0 + MIN)
check('poller alive but no getUpdates round-trip for 3+ min: notice + restart',
      len(e.tg()) == 1 and 'no successful getUpdates' in e.tg()[0] and len(e.restarts()) == 1, {'tg': e.tg(), 'r': e.restarts()})
e.close()

e = Env('starting')
e.session('idle', started_ago=60)
e.health(pid=e.dead_pid())
e.at(T0)
e.at(T0 + MIN)
check('a session that just started gets time to connect: no incident yet', not e.tg() and not e.restarts() and not e.state()['incidents'])
e.close()

print('— quiet hours (00-08)')
e = Env('quiet')
e.session('idle')
night = T0 + 13 * 3600  # 03:00 the next night
e.health(pid=e.dead_pid())
e.at(night)
e.at(night + MIN)
check('at 03:00 the notice is deferred, the restart still happens', not e.tg() and len(e.restarts()) == 1, {'tg': e.tg(), 'r': e.restarts()})
e.health()
e.at(night + 5 * MIN)
e.at(night + 6 * 3600)  # 09:00
check('resolved before morning: the owner never hears of it', not e.tg() and not e.state()['incidents'], e.tg())
e.close()

e = Env('quiet2')
e.session('idle')
e.health(pid=e.dead_pid())
e.at(night)
e.at(night + MIN)
e.at(night + 5 * 3600 + MIN)  # 08:01, still broken
check('still broken when the quiet hours end: the deferred notice goes out once',
      len(e.tg()) == 1 and 'connection is down' in e.tg()[0], e.tg())
e.at(night + 5 * 3600 + 3 * MIN)
check('…and only once (later: the per-incident restart limit speaks up instead)',
      len([t for t in e.tg() if 'connection is down' in t]) == 1, e.tg())
e.close()

print('— stalls: owner messages the session never saw')
e = Env('stall')
e.session('idle')
e.health()
m = e.owner_msg(700, 4 * MIN)
e.journal(m)
e.at(T0)
req = json.load(open(e.p('tg', 'replay-request.json')))
check('unseen for 3+ min, session idle: first the plugin is asked to re-send it; no owner message, no restart',
      req['ids'] == ['j700'] and not e.tg() and not e.restarts(), {'req': req, 'tg': e.tg()})
os.remove(e.p('tg', 'replay-request.json'))  # the plugin consumed it
e.at(T0 + 2 * MIN)
check('within 3 min of the re-send: wait', not e.tg() and not e.restarts() and not os.path.exists(e.p('tg', 'replay-request.json')))
e.at(T0 + 4 * MIN)
check('still unseen after the re-send: owner notice, re-send requested again, restart',
      len(e.tg()) == 1 and 'didn\'t reach me' in e.tg()[0] and len(e.restarts()) == 1 and os.path.exists(e.p('tg', 'replay-request.json')),
      {'tg': e.tg(), 'r': e.restarts()})
e.seen(m)
e.at(T0 + 6 * MIN)
check('the session saw it (transcript): resolved, "back to normal"', len(e.tg()) == 2 and 'Back to normal' in e.tg()[1], e.tg())
e.close()

e = Env('answered')
e.session('idle')
m = e.owner_msg(701, 5 * MIN)
e.health(last_tool_call={'at': iso(T0 - MIN), 'name': 'reply'})
e.journal(m)
e.at(T0)
check('a Telegram tool call after the message: nothing to do', not e.tg() and not e.restarts() and not os.path.exists(e.p('tg', 'replay-request.json')))
e.close()

e = Env('seen')
e.session('idle')
e.health()
m = e.owner_msg(702, 11 * MIN)
e.journal(m)
e.seen(m, kind='attachment')
e.at(T0)
e.at(T0 + MIN)
check('seen (attachment entry) but never answered, idle 10+ min: one inbox note for me; nothing to the owner, no restart',
      [n for n in e.notes() if n.startswith('channel-unanswered-')].__len__() == 1 and not e.tg() and not e.restarts()
      and not os.path.exists(e.p('tg', 'replay-request.json')), {'notes': e.notes(), 'tg': e.tg()})
e.close()

e = Env('dropped-journal')
e.session('idle')
e.health()
e.journal(e.owner_msg(703, 5 * MIN, status='dropped'))
e.at(T0)
check('journal entries the plugin marked "dropped" are its job (it re-sends them): no stall handling',
      not os.path.exists(e.p('tg', 'replay-request.json')) and not e.tg())
e.close()

print('— fatal API errors after a seen message (#101715 safeguards, #101181 empty system block)')
SAFEGUARDS = ("API Error: Opus 5's safeguards flagged this message (https://www.anthropic.com/legal/aup). This sometimes "
              "happens with safe, normal conversations.\n\nDetails: `[reasoning_extraction]`")
e = Env('api-safeguards')
e.session('idle')
e.health()
m = e.owner_msg(710, 4 * MIN)
e.journal(m)
e.seen(m)
e.api_error(SAFEGUARDS, 'err-1')
e.at(T0)
req = json.load(open(e.p('tg', 'replay-request.json'))) if os.path.exists(e.p('tg', 'replay-request.json')) else {}
check('safeguards error after a seen message, idle: the plugin re-sends it once; nothing to the owner, no restart',
      req.get('ids') == ['j710'] and 'API error' in req.get('reason', '') and not e.tg() and not e.restarts(), {'req': req, 'log': e.log()})
os.remove(e.p('tg', 'replay-request.json'))
e.at(T0 + MIN)
check('the same error entry on the next run: no second re-send, no owner message',
      not os.path.exists(e.p('tg', 'replay-request.json')) and not e.tg(), e.log())
e.seen(m)  # the re-sent copy reached the session…
e.api_error(SAFEGUARDS, 'err-2')  # …and failed again
e.at(T0 + 2 * MIN)
check('failed again after the re-send: one short owner line and an apierror note; no re-send, no restart',
      len(e.tg()) == 1 and 'failed twice' in e.tg()[0] and 'safety filter' in e.tg()[0] and '\n' not in e.tg()[0]
      and [n for n in e.notes() if n.startswith('channel-apierror-')].__len__() == 1
      and not os.path.exists(e.p('tg', 'replay-request.json')) and not e.restarts(), {'tg': e.tg(), 'notes': e.notes()})
e.api_error(SAFEGUARDS, 'err-3')
e.at(T0 + 12 * MIN)
check('…told only once, and no extra "unanswered" note for it',
      len(e.tg()) == 1 and not [n for n in e.notes() if n.startswith('channel-unanswered-')], {'tg': e.tg(), 'notes': e.notes()})
r = e.run('--status')
check('--status lists the API-error bookkeeping', 'api errors:  {"j710"' in r.stdout, r.stdout)
e.close()

e = Env('api-system-block')
e.session('idle')
e.health()
m = e.owner_msg(711, 4 * MIN)
e.journal(m)
e.seen(m)
e.api_error('API Error: 400 messages.3: system content must contain at least one block', 'err-1')
e.at(T0)
check('"system content must contain at least one block": re-sent once',
      os.path.exists(e.p('tg', 'replay-request.json')) and json.load(open(e.p('tg', 'replay-request.json')))['ids'] == ['j711'] and not e.tg())
os.remove(e.p('tg', 'replay-request.json'))
e.health(last_tool_call={'at': iso(T0 + 30), 'name': 'reply'})  # the re-send worked: answered
e.at(T0 + 2 * MIN)
check('answered after the re-send: nothing more', not e.tg() and not os.path.exists(e.p('tg', 'replay-request.json')) and not e.restarts())
e.close()

e = Env('api-refusal')
e.session('idle')
e.health()
m = e.owner_msg(712, 4 * MIN)
e.journal(m)
e.seen(m)
e.api_error('', 'msg-1', synthetic=False, stop_reason='refusal')
e.at(T0)
check('a stop_reason "refusal" entry counts as a safeguards stop: re-sent once', os.path.exists(e.p('tg', 'replay-request.json')))
e.close()

e = Env('api-talk')
e.session('idle')
e.health()
m = e.owner_msg(713, 11 * MIN)
e.journal(m)
e.seen(m)
e.api_error('I added "safeguards flagged" and "system content must contain at least one block" to channel-check.', 'm-1', synthetic=False)
e.at(T0)
e.at(T0 + MIN)
check('the model merely talking about the errors: no re-send (just the usual "unanswered" note)',
      not os.path.exists(e.p('tg', 'replay-request.json')) and not e.tg()
      and [n for n in e.notes() if n.startswith('channel-unanswered-')].__len__() == 1, {'notes': e.notes(), 'log': e.log()})
e.close()

e = Env('api-before')
e.session('idle')
e.health()
m = e.owner_msg(714, 4 * MIN)
e.journal(m)
e.api_error(SAFEGUARDS, 'old-err')  # an older failure, before this message arrived
e.seen(m)
e.at(T0)
check('an API error from before the message: not this message\'s problem', not os.path.exists(e.p('tg', 'replay-request.json')))
e.close()

e = Env('api-busy')
e.session('busy')
e.health()
m = e.owner_msg(715, 4 * MIN)
e.journal(m)
e.seen(m)
e.api_error(SAFEGUARDS, 'err-1')
e.at(T0)
check('session busy (already on something new): no re-send', not os.path.exists(e.p('tg', 'replay-request.json')) and not e.tg())
e.close()

print('— long wait, dialog, hung')
e = Env('busy')
e.session('busy')
e.health()
e.journal(e.owner_msg(800, 4 * MIN))
e.at(T0)
check('busy session, unseen 4 min: nothing yet (and never a restart for a slow answer)', not e.tg() and not e.restarts())
e.at(T0 + 7 * MIN)
check('busy session, unseen 11 min: one owner notice, no restart', len(e.tg()) == 1 and 'still waiting' in e.tg()[0] and not e.restarts(), e.tg())
e.at(T0 + 9 * MIN)
check('…no repeat', len(e.tg()) == 1)
e.close()

e = Env('dialog')
e.session('waiting', waiting='dialog open')
e.health()
e.journal(e.owner_msg(801, 4 * MIN))
e.at(T0)
check('session waiting on a dialog with a message behind it: notice, no restart',
      len(e.tg()) == 1 and 'dialog open' in e.tg()[0] and not e.restarts(), e.tg())
e.close()

e = Env('dialog2')
e.session('waiting', waiting='dialog open')
open(e.p('stuck'), 'w').close()  # the heartbeat already told the owner about the dialog
e.health()
e.journal(e.owner_msg(802, 4 * MIN))
e.at(T0)
check('…unless the heartbeat already reported that dialog', not e.tg() and not e.restarts())
e.close()

e = Env('hung')
e.session('busy', status_ago=30 * MIN)
e.health()
e.journal(e.owner_msg(803, 4 * MIN))
old = time.time() - 30 * MIN
os.utime(e.transcript, (old, old))
e.now = old + 30 * MIN  # "now" = real now: the transcript is 30 min old
e.health()
e.journal(e.owner_msg(803, 4 * MIN))
e.session('busy', status_ago=30 * MIN)
os.utime(e.transcript, (old, old))
e.at(e.now)
check('busy for 30 min with no transcript writes (hung): handled like idle (re-send first)',
      os.path.exists(e.p('tg', 'replay-request.json')) and not e.tg(), e.log())
os.remove(e.p('tg', 'replay-request.json'))
e.at(e.now + 4 * MIN)
check('…then restarted', len(e.restarts()) == 1, {'r': e.restarts(), 'log': e.log()})
e.close()

e = Env('not-hung')
e.session('busy', status_ago=30 * MIN)  # status old, but a subagent transcript is fresh
e.health()
os.makedirs(e.p('projects', e.sid, 'subagents'))
open(e.p('projects', e.sid, 'subagents', 'agent-x.jsonl'), 'w').close()
e.now = time.time()
e.health()
e.journal(e.owner_msg(804, 4 * MIN))
e.session('busy', status_ago=30 * MIN)
e.at(e.now)
check('busy with a subagent still writing (e.g. a long foreground agent): not hung, no re-send, no restart',
      not os.path.exists(e.p('tg', 'replay-request.json')) and not e.restarts(), e.log())
e.close()

print('— restart rate limits')
e = Env('limits')
e.session('idle')
dead = e.dead_pid()


def incident(start):
    e.health(pid=dead)
    e.at(start)
    e.at(start + MIN)
    n = len(e.restarts())
    e.health()
    e.at(start + 2 * MIN)
    return n


r1 = incident(T0)
r2 = incident(T0 + 20 * MIN)
r3 = incident(T0 + 40 * MIN)
check('three separate incidents 20 min apart: three restarts', (r1, r2, r3) == (1, 2, 3), (r1, r2, r3))
check('incidents back within 30 min of the last one: no new owner notice (one per incident, then quiet)',
      len([t for t in e.tg() if t.startswith('⚠️')]) == 1, e.tg())
e.health(pid=dead)
e.at(T0 + 60 * MIN)
e.at(T0 + 61 * MIN)
check('a fourth within 6 h: no restart, the owner is told it needs them (once)',
      len(e.restarts()) == 3 and len([t for t in e.tg() if 'stopped restarting' in t]) == 1, {'tg': e.tg(), 'r': e.restarts()})
e.at(T0 + 80 * MIN)
check('…and not again', len(e.restarts()) == 3 and len([t for t in e.tg() if 'stopped restarting' in t]) == 1)
e.at(T0 + 7 * 3600)
check('6 h later the budget is back', len(e.restarts()) == 4, e.restarts())
e.close()

e = Env('gap')
e.session('idle')
dead = e.dead_pid()
e.health(pid=dead)
e.at(T0)
e.at(T0 + MIN)
e.at(T0 + 6 * MIN)
check('one long incident: no second restart within 15 min', len(e.restarts()) == 1, e.restarts())
e.at(T0 + 17 * MIN)
check('second restart after 15 min', len(e.restarts()) == 2, e.restarts())
e.at(T0 + 33 * MIN)
check('third would exceed 2 per incident: refused, owner told once', len(e.restarts()) == 2 and any('stopped restarting' in t for t in e.tg()), e.tg())
e.close()

print('— dry run and the owner-message cap')
e = Env('dry')
e.session('idle')
e.health()
e.journal(e.owner_msg(900, 4 * MIN))
r = e.run('--dry-run')
check('--dry-run prints what it would do and changes nothing',
      'replay:' in r.stdout and not os.path.exists(e.p('tg', 'replay-request.json')) and not os.path.exists(e.p('state', 'state.json'))
      and not e.tg() and not e.restarts(), r.stdout)
e.close()

e = Env('cap')
e.session('idle')
e.health(pid=e.dead_pid())
os.makedirs(e.p('state'), exist_ok=True)
with open(e.p('state', 'state.json'), 'w') as f:
    json.dump({'sent': [T0 - i * 10 * MIN for i in range(1, 7)]}, f)
e.at(T0)
e.at(T0 + MIN)
check('six owner messages in the last 6 h already: the seventh is suppressed (logged)', not e.tg() and 'suppressed' in e.log(), e.log())
e.close()

print(('\n%d FAILED' % failures) if failures else '\nall passed')
sys.exit(1 if failures else 0)
