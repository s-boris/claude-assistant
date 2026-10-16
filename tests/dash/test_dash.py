#!/usr/bin/env python3
from zoneinfo import ZoneInfo
from datetime import datetime
"""Tests for ~/bin/dash-data, the data-free page template ~/opt/dash/index.html (repo: dash/index.html) and the site
dash-data generates from it in ~/.local/state/dash/www (index.html with the data inlined, data.json, dashboard.html).
Run: ~/opt/dash-tests/run.sh

1. clean(): masks addresses, e-mails, phones, IBAN/AHV, IPs, long numbers, token-like strings.
2. Fixture transcripts (temp dir): dedupe, cost maths, kinds, subagent meta, turn classes, cache reuse,
   and planted private text (address, token, e-mail) never reaches the output.
3. Real run: schema sanity, totals add up, data map references resolve.
4. Privacy sweep of the real data.json, template, generated index.html, dashboard.html: secretscan clean, no e-mails, phones,
   IPs, IBANs, card numbers, bot tokens, the owner's chat ids or any street from the memory notes.
5. Page structure: title, markers, no external requests, artifact fragment has no doctype/html/head/body.
   Template (also in a checkout/CI): data block {}, profile values only as {{key}} tokens dash-data may fill;
   the split on fixtures: template only read, www/ gets the page + data, {{network.server_name}} from the profile.
6. Headless Chrome render (DOM dump) of every tab (6 since the 2026-10-15 consolidation: Today, Usage, Activity,
   Codebase, Privacy, Architecture; old hashes land on their new tab), light + dark phone screenshots to
   ~/reports/dash/, the work log's Today and Last night views from page copies (afternoon / morning data), and the
   "every metric once" rules: one line count, one agents-today number, one snapshot count, server health in Health.
8. Last night card: night windows (22:00–08:00, named by the morning), ~times, untimed lines, scrub() of names
   (fictional people note) and amounts, plus a real-data sweep of every log-derived line.
7. dash-growth on fixture repos: last commit of a day wins, carry-forward, vault repo preferred, data ignored,
   activity merged from dash-data's daily counts, --if-stale.
"""
import datetime as dt
import glob
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HOME = os.path.expanduser('~')
DASH = os.environ.get('DASH_BIN', os.path.join(HOME, 'bin', 'dash-data'))   # DASH_BIN/DASH_PAGE_DIR: test a copy before installing
PAGE_DIR = os.environ.get('DASH_PAGE_DIR', os.path.join(HOME, 'opt', 'dash'))
TEMPLATE = os.path.join(PAGE_DIR, 'index.html')   # the data-free page template
if not os.path.exists(TEMPLATE) and 'DASH_PAGE_DIR' not in os.environ:   # checkout / CI: the repo's dash/index.html
    TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), 'dash', 'index.html')
WWW = os.environ.get('DASH_WWW', os.path.join(HOME, '.local', 'state', 'dash', 'www'))   # DASH_WWW: the generated site
PAGE = os.path.join(WWW, 'index.html')            # template + inlined fallback data (what dash-publish copies)
FRAG = os.path.join(WWW, 'dashboard.html')
# Synthetic profile for the fixture runs (tests never give dash-data the real one): repo layout tests/profile,
# live layout ~/opt/profile-tests. The live-install checks (real data) run with the environment as it is.
FIXTURE_PROFILE = None
for _pf in ('profile', 'profile-tests'):
    _pf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _pf, 'fixtures', 'profile.json')
    if os.path.isfile(_pf):
        FIXTURE_PROFILE = _pf
        break
REPORTS = os.path.join(HOME, 'reports', 'dash')
FAILS = []
PASSES = 0


def ok(cond, name):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(name)
        print('FAIL', name)


def load_module():
    loader = importlib.machinery.SourceFileLoader('dashdata', DASH)
    spec = importlib.util.spec_from_loader('dashdata', loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


# ------------------------------------------------------------------------------------------- 1. clean()
def test_assistant_helpers(m):
    ok(m.is_private('---\nname: x\nprivate: true\n---\nbody') and m.is_private('---\nname: x\ndashboard: hide\n---\n'),
       'private skills recognised')
    ok(not m.is_private('---\nname: x\ndescription: private data stays local\n---\nprivate: true'), 'only frontmatter flags count')
    ok(all(m.RX_CHECK.match(l) for l in ('    assert x == 1', '  self.assertEqual(a, b)', 'ok(x, "y")', 'eq "$a" b',
                                           '  expect(x).toBe(1)', 'assert.equal(a, b)')), 'test check lines recognised')
    ok(not any(m.RX_CHECK.match(l) for l in ('def ok(cond, name):', '# assert nothing', 'okay = 1')), 'non-check lines ignored')
    tmp = tempfile.mkdtemp(prefix='dash-loc-')
    try:
        for n in ('a.py', 'b.py'):
            with open(os.path.join(tmp, n), 'w') as f:
                f.write('x = 1\n\n# c\nassert x\n')
        with open(os.path.join(tmp, 'tool'), 'w') as f:
            f.write('#!/bin/bash\necho hi\n')
        langs = dict((os.path.basename(p), lg) for p, lg in m.code_files(tmp))
        ok(langs == {'a.py': 'Python', 'b.py': 'Python', 'tool': 'Shell'}, 'languages from extension and shebang')
        seen = set()
        ok(m.file_lines(os.path.join(tmp, 'a.py'), True, seen) == (3, 1) and m.file_lines(os.path.join(tmp, 'b.py'), True, seen) == (0, 0),
           'non-blank lines counted, identical copies once')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    leaf = {'a': ['fine', 'ghp_' + 'A' * 36], 'b': {'c': 'ok too'}}
    m.redact_strings(leaf)
    ok(leaf['a'][0] == 'fine' and 'A' * 20 not in leaf['a'][1] and leaf['b']['c'] == 'ok too', 'assistant strings get a redact pass')


def test_clean(m):
    c = m.clean
    cases = {
        'Examplestrasse 1, 1234 Exampletown': ['Examplestrasse', '39,'],
        'mail alex.test@example.com now': ['@', 'example.com'],
        'call +41 79 123 45 67 today': ['123 45 67'],
        'call 079 123 45 67 today': ['123 45 67'],
        'IBAN CH93 0076 2011 6238 5295 7 ok': ['0076 2011'],
        'AHV 756.1234.5678.97': ['1234.5678'],
        'chat 100000001 id': ['100000001'],
        'host 198.51.100.5:8000 up': ['192.168'],
        'see https://x.example/a?token=abc123 here': ['token=abc123'],
        'Check-out at Via Luigi Sacco 5 by 10': ['Sacco 5'],
        'password: hunter2 and token=abc': ['hunter2'],
        'key sk-ant-api03-AbCdEf0123456789AbCdEf0123456789xyz': ['AbCdEf0123456789AbCdEf0123456789'],  # secretscan:allow (planted fake)
    }
    for src, banned in cases.items():
        out = c(src)
        for b in banned:
            ok(b not in out, f'clean() masks {b!r} in {src!r} -> {out!r}')
    ok(c('Build privacy vault container') == 'Build privacy vault container', 'clean() keeps plain titles')
    ok(c('Exampletown') == 'Exampletown', 'clean() keeps town names')
    ok(c('x' * 200, 50).endswith('…') and len(c('x' * 200, 50)) <= 50, 'clean() shortens')
    ok(m.cron_human(['*/5', '*', '*', '*', '*']) == 'every 5 min', 'cron every 5 min')
    ok(m.cron_human(['7', '9', '*', '*', '1']) == 'Mon 09:07', 'cron weekly')
    ok(m.cron_human(['13', '8', '*', '*', '*']) == 'daily 08:13', 'cron daily')
    ok(m.cron_human(['23', '9', '1', '*', '*']) == 'monthly on day 1, 09:23', 'cron monthly')
    ok(m.cron_human(['5', '8-22/2', '*', '*', '*']) == 'every 2 h, 08:05–22:05', 'cron stepped hours')
    t = m.title_of('PRIVACY VAULT (owner-approved, levels 1+2): new container `claude-vault` on Unraid (compose x); more')
    ok(t == 'PRIVACY VAULT: new container claude-vault on Unraid', f'title_of: {t!r}')



# ------------------------------------------------------------------------------------------- 1b. names, amounts, nights
def test_lastnight(m):
    tmp = tempfile.mkdtemp(prefix='dash-ln-')
    try:
        note = os.path.join(tmp, 'people.md')
        with open(note, 'w') as f:
            f.write('---\nname: people\n---\nFamily:\n- Mirela "Mira" Testova (Oct 3)\n- Kasimir Plonk: organizes the trip\n'
                    '- Ottilie, his neighbour\n')
        m.PEOPLE_NOTE, m._PEOPLE = note, None
        first, sur = m.people()
        ok({'Mirela', 'Mira', 'Kasimir', 'Ottilie'} <= first and sur == {'Testova', 'Plonk'} and 'Family' not in first and 'Oct' not in first,
           f'people(): first names and surnames from the note ({sorted(first)}, {sorted(sur)})')
        sc = m.scrub
        cases = [
            ('paid CHF 120 to Mirela Testova', 'paid [amount] to someone'),
            ("Kasimir's gift idea, Ottilie asked", "someone's gift idea, someone asked"),
            ('Kasimir Plonk', 'someone'),
            ('best 22.57 CHF/TB, premium 375.00 in 2027', 'best [amount]/TB, premium [amount] in 2027'),
            ('~$0.09/run, ~$15–20/day, €1.2k, Fr. 80.–, 120.– cash', '~[amount]/run, ~[amount]/day, [amount], [amount], [amount] cash'),
            ("rent 1'250.50 and fee: 35", 'rent [amount] and fee: [amount]'),
            ('enable 16.10 09:05, 4.95 TB, 65.25%, CC 2.1.299, mod 0.3.1, 29/29, −1.3k lines, costs 3 h, cost ~1.1M tokens',
             'enable 16.10 09:05, 4.95 TB, 65.25%, CC 2.1.299, mod 0.3.1, 29/29, −1.3k lines, costs 3 h, cost ~1.1M tokens'),
            ("Unraid's restart, The World's End, Jule's invite", "Unraid's restart, The World's End, someone's invite"),
        ]
        for src, want in cases:
            got = sc(src, {'Unraid'})
            ok(got == want, f'scrub({src!r}) -> {got!r}, want {want!r}')
        ok(sc('call Ottilie, Testova later', {'Ottilie', 'Testova'}) == 'call Ottilie, someone later',
           'scrub: a first name already shown elsewhere stays, a surname never')

        ws = os.path.join(tmp, 'ws')
        os.makedirs(ws)
        long_ = 'Built a very long thing ' + 'with many words ' * 12
        with open(os.path.join(ws, 'PROACTIVE.md'), 'w') as f:
            f.write('# Proactive log\n- 2026-10-01 02:00 too old\n- 2026-10-07 02:00 ninth night back\n- 2026-10-08 03:00 oldest shown\n'
                    '- 2026-10-14 ~02:30 approx time\n- 2026-10-14 03:00 — paid CHF 120 to Mirela Testova\n'
                    '- 2026-10-14 21:59 day item\n- 2026-10-14 22:00 evening item\n- 2026-10-15 01:00 — out of order\n'
                    '- 2026-10-15 00:10 started: night build\n- 2026-10-15 — untimed line\n- 2026-10-15 02:00 ' + long_ + '\n'
                    '- 2026-10-15 07:59 last one\n- 2026-10-15 08:00 morning report\n- 2026-10-15 22:10 next night\n')
        m.WORKSPACE = ws
        Z = ZoneInfo('Europe/Zurich')
        ln = m.lastnight({'Unraid'}, datetime(2026, 10, 15, 3, 50, tzinfo=Z))
        t = ln['tonight']
        ok(t['date'] == '2026-10-15' and t['in_progress'], f'lastnight 03:50: tonight is the 15th, in progress ({t["date"]})')
        ok([e['time'] for e in t['items']] == ['22:00', '00:10', '01:00', '02:00', '07:59'],
           f'lastnight: 22:00..07:59 window, sorted ({[e["time"] for e in t["items"]]})')
        ok(t['items'][1]['kind'] == 'started' and t['items'][1]['text'] == 'night build', 'lastnight: kind split off')
        ok(len(t['items'][3]['text']) <= 90 and t['items'][3]['text'].endswith('…'), 'lastnight: items shortened to 90 chars')
        ok([n['date'] for n in ln['earlier']] == ['2026-10-%02d' % d for d in range(14, 7, -1)], 'lastnight: the 7 nights before')
        n14 = ln['earlier'][0]
        ok([e['text'] for e in n14['items']] == ['approx time', 'paid [amount] to someone'], f'lastnight: ~time parsed, scrubbed ({n14["items"]})')
        ok(ln['earlier'][-1]['count'] == 1 and ln['untimed'] == 1, f'lastnight: oldest night kept, untimed counted ({ln["untimed"]})')
        ok(not any('too old' in e['text'] or 'ninth' in e['text'] or 'day item' in e['text'] or 'morning' in e['text']
                   for n in [t] + ln['earlier'] for e in n['items']), 'lastnight: day items and old nights left out')
        ln = m.lastnight(set(), datetime(2026, 10, 15, 12, 0, tzinfo=Z))
        ok(ln['tonight']['date'] == '2026-10-15' and not ln['tonight']['in_progress'], 'lastnight at noon: last night, done')
        ln = m.lastnight(set(), datetime(2026, 10, 15, 22, 30, tzinfo=Z))
        ok(ln['tonight']['date'] == '2026-10-16' and ln['tonight']['in_progress'] and ln['tonight']['count'] == 1,
           'lastnight 22:30: the new night has begun')
        ln = m.lastnight(set(), datetime(2026, 10, 14, 23, 0, tzinfo=Z))
        ok(ln['tonight']['date'] == '2026-10-15' and ln['tonight']['in_progress'] and ln['tonight']['count'] >= 1,
           'lastnight 23:00 with an item: tonight shown')
        ln = m.lastnight(set(), datetime(2026, 10, 11, 22, 30, tzinfo=Z))
        ok(ln['tonight']['date'] == '2026-10-11' and not ln['tonight']['in_progress'] and len(ln['earlier']) == 7,
           f'lastnight: an empty night just begun falls back to the last one ({ln["tonight"]["date"]})')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        m.PEOPLE_NOTE, m._PEOPLE = os.environ.get('DASH_PEOPLE', m.PEOPLE_NOTE), None


def test_goals(m):
    root = tempfile.mkdtemp(prefix='dash-goals-')
    try:
        g = os.path.join(root, 'GOALS.md')
        with open(g, 'w') as f:
            f.write('# Goals (draft)\n\nIntro line\n- [ ] not under a goal, ignored\n\n'
                    '## 1. Admin on autopilot (his top annoyance, confirmed 2026-10-09)\nDone = x.\n'
                    '- [x] Bills tracked\n- [ ] Bills via Gmail (blocked: vault Gmail)\n'
                    '- [ ] Call +41 79 555 66 77 at Musterweg 12 about the lease; then more text here\n'
                    '## 2. Money\n- [ ] Waiting on the owner for the token\n  - [x] nested done step\n'
                    '## Untitled numbering\n')
        r = m.parse_goals(g)
        gs = r['goals']
        ok([x['n'] for x in gs] == [1, 2, 3] and gs[0]['title'] == 'Admin on autopilot', f'goals parsed, parens dropped: {[x["title"] for x in gs]}')
        ok((gs[0]['done'], gs[0]['open'], gs[0]['total'], gs[0]['blocked']) == (1, 2, 3, 1), f'goal 1 counts: {gs[0]}')
        ok(gs[0]['next'] and not gs[0]['next_blocked'] and gs[0]['next'].startswith('Call'), 'next step skips blocked steps')
        ok('555 66 77' not in gs[0]['next'] and 'Musterweg' not in gs[0]['next'], f'next step masked: {gs[0]["next"]!r}')
        ok(gs[1]['next_blocked'] and gs[1]['done'] == 1, 'only blocked steps left -> next_blocked; nested steps count')
        ok(gs[2]['total'] == 0 and gs[2]['pct'] == 0, 'goal without steps')
        ok((r['done'], r['open'], r['total']) == (2, 3, 5), f'goal totals: {r["done"]}/{r["total"]}')
        ok(m.parse_goals(os.path.join(root, 'missing.md'))['goals'] == [], 'missing GOALS.md -> no goals')
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------------------------------- 2. fixtures
PLANTED = ['Musterweg 12', 'planted.person@example.org', 'sk-ant-api03-PLANTEDsecretPLANTEDsecret0123456789',  # secretscan:allow (planted fakes)
           'Top secret family conflict', '+41 79 555 66 77']


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def make_fixture(root):
    import time
    # 1 h ago, but never before today's Zurich midnight (else 'today' checks fail just after 00:00)
    _z = datetime.now(ZoneInfo('Europe/Zurich'))
    now = max(time.time() - 3600, _z.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 1)
    proj = os.path.join(root, 'projects', '-home-node-workspace')
    os.makedirs(os.path.join(proj, 'S1', 'subagents'))
    secret_text = ' '.join(PLANTED)

    def asst(mid, ts, model, inp, out, cr, cw1h, tools=(), side=False):
        return {'type': 'assistant', 'isSidechain': side, 'timestamp': iso(ts), 'version': '9.9.9',
                'message': {'id': mid, 'model': model, 'role': 'assistant',
                            'content': [{'type': 'text', 'text': secret_text}] +
                                       [{'type': 'tool_use', 'id': f'{mid}-t{i}', 'name': n, 'input': {'cmd': secret_text}} for i, n in enumerate(tools)],
                            'usage': {'input_tokens': inp, 'output_tokens': out, 'cache_read_input_tokens': cr,
                                      'cache_creation_input_tokens': cw1h,
                                      'cache_creation': {'ephemeral_1h_input_tokens': cw1h, 'ephemeral_5m_input_tokens': 0}}}}

    main = [
        {'type': 'user', 'timestamp': iso(now), 'origin': {'kind': 'channel', 'server': 'plugin:telegram-plus:telegram'},
         'message': {'role': 'user', 'content': '<channel source="plugin:telegram-plus:telegram">' + secret_text + '</channel>'}},
        asst('m1', now + 1, 'claude-opus-5-5', 10, 1000, 100000, 2000, tools=['Bash', 'mcp__plugin_telegram-plus_telegram__reply']),
        asst('m1', now + 1, 'claude-opus-5-5', 10, 1000, 100000, 2000),   # duplicate content block of the same message
        {'type': 'user', 'timestamp': iso(now + 2), 'origin': {'kind': 'human'},
         'message': {'role': 'user', 'content': '[heartbeat] Idle 12 min'}},
        {'type': 'user', 'timestamp': iso(now + 3), 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'm1-t0', 'content': secret_text}]}},
        asst('m2', now + 4, 'claude-opus-5-5', 0, 500, 0, 0),
        {'type': 'cost-state', 'startTime': 1, 'totalCostUSD': 1.5, 'modelUsage': {}},
    ]
    with open(os.path.join(proj, 'S1.jsonl'), 'w') as f:
        for r in main:
            f.write(json.dumps(r) + '\n')
    # forked copy of the main session repeats m1 (must be deduped)
    with open(os.path.join(proj, 'S0.jsonl'), 'w') as f:
        f.write(json.dumps(main[1]) + '\n')
    sub = [asst('s1', now + 10, 'claude-sonnet-5-5', 100, 2000, 50000, 1000, tools=['Read'], side=True),
           asst('s2', now + 70, 'claude-sonnet-5-5', 0, 1000, 0, 0, side=True)]
    with open(os.path.join(proj, 'S1', 'subagents', 'agent-abc123def456.jsonl'), 'w') as f:
        for r in sub:
            f.write(json.dumps(r) + '\n')
    with open(os.path.join(proj, 'S1', 'subagents', 'agent-abc123def456.meta.json'), 'w') as f:
        json.dump({'agentType': 'general-purpose', 'description': 'Fixture run at Musterweg 12', 'effort': 'xhigh',
                   'requestShape': 'background', 'spawnDepth': 1}, f)
    today = _z.strftime('%Y-%m-%d')
    ws = os.path.join(root, 'workspace')
    os.makedirs(ws)
    with open(os.path.join(ws, 'GOALS.md'), 'w') as f:
        f.write('## 1. Fixture goal\n- [x] first step\n- [ ] second step at Musterweg 12\n')
    with open(os.path.join(ws, 'PROACTIVE.md'), 'w') as f:
        f.write(f'- 2000-01-01 10:00 done: ancient entry\n- {today} 09:15 done: built fixture thing; mail planted.person@example.org\n'
                f'- {today} 10:30 started: second fixture thing\n')
    with open(os.path.join(ws, 'CHANGELOG.md'), 'w') as f:
        f.write(f'- {today} — fixture change at Musterweg 12\n- 2000-01-01 — old change\n')
    with open(os.path.join(ws, 'TODO.md'), 'w') as f:
        f.write('# TODO\n## In progress\n- **Fixture task** — running\n')
    aw = os.path.join(root, 'aw')
    os.makedirs(aw)
    with open(os.path.join(aw, 'agent-watch.log'), 'w') as f:
        f.write(f'{today} 09:00:01 aaaa running -> done\n{today} 09:05:01 progress bbbb sent (14 min)\n'
                f'{today} 09:10:01 stalled cccc silent 16m -> inbox/x.md\n2000-01-01 09:00:01 dddd running -> done\n')
    with open(os.path.join(aw, 'state.json'), 'w') as f:
        json.dump({'agents': {'a1': {'run_start': now, 'status': 'done'}, 'a2': {'run_start': 946720800, 'status': 'done'}}}, f)
    ctx = {'schema': 1, 'generated_at': _z.isoformat(timespec='seconds'), 'date': today,
           'sources': [{'name': 'hn', 'status': 'ok', 'public': '2 picks from 30 front-page stories'},
                       {'name': 'location', 'status': 'ok', 'public': 'Home Assistant, updated 5 min ago'},
                       {'name': 'telegram', 'status': 'ok', 'public': '3 chats summarized'},
                       {'name': 'weather', 'status': 'error', 'public': 'x', 'error': 'URLError at https://x.example/a?token=abc123'},
                       {'name': 'odd', 'status': 'weird-status', 'public': 'y'}],
           'server': {'health': '1 critical, 3 warning(s); new since last run: 0 (report 2026-10-15 08:13)',
                      'capacity': 'array 91.0% used, 8.5 TB free; full ~2026-11-21 (in 38 d at the 30-day trend)', 'media': 'x'},
           'hn': {'picks': [{'id': 123, 'title': 'Claude agents on Unraid', 'points': 42, 'domain': 'example.com', 'tags': ['AI', 'evil-tag'],
                             'url': 'https://example.com/post?ref=PLANTEDURL'},
                            {'id': 'bad'}]},
           'agenda': {'reminders': [{'at': today + ' 10:00', 'text': 'PLANTEDREMINDER for PersonB'}]},
           'location': {'status': 'ok', 'place': 'PLANTEDPLACE', 'lat': 47.5000, 'lon': 8.5000},
           'telegram': {'status': 'ok', 'chats': 3, 'summary': 'PLANTEDTGCONTENT'}}
    with open(os.path.join(root, 'context.json'), 'w') as f:
        json.dump(ctx, f)
    jobs = os.path.join(root, 'jobs', 'demo')
    os.makedirs(jobs)
    with open(os.path.join(jobs, 'run.jsonl'), 'w') as f:
        f.write(json.dumps(asst('j1', now + 20, 'claude-sonnet-5-5', 6, 30, 0, 100)) + '\n')
        f.write(json.dumps({'type': 'result', 'total_cost_usd': 0.0123, 'result': secret_text,
                            'modelUsage': {'claude-sonnet-5-5': {'inputTokens': 6, 'outputTokens': 386,
                                                                  'cacheReadInputTokens': 23361, 'cacheCreationInputTokens': 15220}}}) + '\n')


def fixture_env(root, extra_env=None):
    dfm = os.path.join(root, 'dataflow.json')   # synthetic data map; the curated live one is not in a checkout
    if not os.path.exists(dfm):
        with open(dfm, 'w') as f:
            json.dump({'nodes': [], 'stores': [], 'flows': []}, f)
    env = dict(os.environ, DASH_DATAFLOW=dfm, DASH_PROJECTS=os.path.join(root, 'projects'), DASH_JOBS=os.path.join(root, 'jobs'),
               DASH_STATE=os.path.join(root, 'state'), DASH_PAGE_DIR=os.path.join(root, 'page'),
               DASH_WORKSPACE=os.path.join(root, 'workspace'),
               DASH_DAYLINE=os.path.join(root, 'state', 'dayline.json'), DAYLINE_AW_DIR=os.path.join(root, 'aw'),
               DASH_CONTEXT=os.path.join(root, 'context.json'), DASH_VAULT='/bin/false', DASH_VAULT_TTL='0')
    env.pop('DASH_WWW', None)
    if FIXTURE_PROFILE:
        env['ASSISTANT_PROFILE'] = FIXTURE_PROFILE
    env.update(extra_env or {})
    return env


def run_fixture(root, extra_env=None):
    return subprocess.run([DASH, '--stdout'], capture_output=True, text=True, env=fixture_env(root, extra_env), timeout=120)


def test_split():
    """dash-data on fixtures (not --stdout): the template is only read; www/ gets the page with the data inlined,
    data.json and (--inline) the artifact fragment; {{network.server_name}} comes from the fixture profile and a
    token outside dash-data's allowlist stays as it is."""
    root = tempfile.mkdtemp(prefix='dash-split-')
    try:
        make_fixture(root)
        os.makedirs(os.path.join(root, 'page'), exist_ok=True)
        tpl = ('<!doctype html><html><head><!--dash:head--><title>Claude Bot Dashboard</title><!--/dash:head--></head><body>'
               '<!--dash:body--><svg><text>Unraid server · {{network.server_name}}</text><text>{{home.street}}</text></svg>'
               '<script id="dash-data" type="application/json" data-mode="fallback">{}</script><!--/dash:body--></body></html>\n')
        tp = os.path.join(root, 'page', 'index.html')
        with open(tp, 'w') as f:
            f.write(tpl)
        r = subprocess.run([DASH, '--inline'], capture_output=True, text=True, env=fixture_env(root), timeout=120)
        ok(r.returncode == 0, f'split: dash-data --inline on fixtures exits 0 ({r.stderr[-300:]})')
        www = os.path.join(root, 'state', 'www')
        ok(open(tp).read() == tpl, 'split: the template is left untouched')
        ok(sorted(os.listdir(os.path.join(root, 'page'))) == ['index.html'], 'split: nothing written next to the template')
        gen = open(os.path.join(www, 'index.html')).read() if os.path.exists(os.path.join(www, 'index.html')) else ''
        m = re.search(r'<script id="dash-data" type="application/json" data-mode="fallback">(.*?)</script>', gen, re.S)
        cur = json.load(open(os.path.join(root, 'state', 'data.json'))) if r.returncode == 0 else {}
        emb = json.loads(m.group(1).replace('<\\/', '</')) if m else {}
        ok(bool(cur) and emb == cur, 'split: www/index.html carries the data as its fallback copy')
        ok(os.path.exists(os.path.join(www, 'data.json')) and json.load(open(os.path.join(www, 'data.json'))) == cur,
           'split: www/data.json = the data')
        ok('Unraid server · Testsrv</text>' in gen, 'split: {{network.server_name}} filled from the (fixture) profile')
        ok('<text>{{home.street}}</text>' in gen, 'split: a token outside the allowlist is not filled')
        frag = open(os.path.join(www, 'dashboard.html')).read() if os.path.exists(os.path.join(www, 'dashboard.html')) else ''
        ok('data-mode="inline"' in frag and 'Testsrv' in frag and not re.search(r'<!doctype|<html|<head[\s>]|<body', frag, re.I),
           'split: --inline writes the artifact fragment to www/')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fixture():
    root = tempfile.mkdtemp(prefix='dash-test-')
    try:
        make_fixture(root)
        r = run_fixture(root)
        ok(r.returncode == 0, f'fixture run exits 0 (stderr: {r.stderr[-300:]})')
        if r.returncode != 0:
            return
        d = json.loads(r.stdout)
        t = d['tokens']
        bk = t['by_kind']
        ok(bk['main']['messages'] == 2, f"main messages deduped to 2 (got {bk['main']['messages']})")
        exp_main = (10 * 4 + 1000 * 20 + 100000 * 0.2 + 2000 * 8 + 500 * 20) / 1e6
        ok(abs(bk['main']['cost_usd'] - exp_main) < 1e-4, f"main cost {bk['main']['cost_usd']} == {exp_main}")
        exp_sub = (100 * 2 + 3000 * 10 + 50000 * 0.1 + 1000 * 4) / 1e6
        ok(abs(bk['subagent']['cost_usd'] - exp_sub) < 1e-4, f"subagent cost {bk['subagent']['cost_usd']} == {exp_sub}")
        ok(abs(bk['headless']['cost_usd'] - 0.0123) < 1e-4, 'headless cost taken from the result record')
        ok(bk['headless']['output'] == 386, 'headless tokens from the result record, not partial stream usage')
        ok(t['totals']['tokens'] == sum(b['tokens'] for b in bk.values()), 'totals = sum of kinds')
        runs = {x['id']: x for x in t['top_runs']}
        ok('abc123de' in runs and runs['abc123de']['effort'] == 'xhigh', 'subagent run listed with its meta')
        ok(runs.get('abc123de', {}).get('minutes') == 1.0, 'subagent run duration 1 min')
        ok('[street]' in runs.get('abc123de', {}).get('description', ''), 'street in a subagent description is masked')
        td = t['today'] or {}
        ok(td.get('turns', {}).get('telegram') == 1 and td.get('turns', {}).get('heartbeat') == 1,
           f"turn classes counted: {td.get('turns')}")
        names = {x['name']: x['calls'] for x in t['tools_top']}
        ok(names.get('telegram: reply') == 1 and names.get('Bash') == 1 and names.get('Read') == 1, f'tool labels: {names}')
        ok(td.get('telegram_replies') == 1, 'telegram replies counted')
        ok(t['claude_code_counter_usd'] == 1.5, 'Claude Code counter read')
        ok(len(t['hourly']) == 48 and sum(sum(h['tokens'].values()) for h in t['hourly']) == t['totals']['tokens'],
           'hourly window holds the fixture tokens')
        # cost per kind per day, cost pace, 24 h timeline (the budget tile was retired 2026-10-15)
        ok(all(abs(sum(x['cost_by_kind'].values()) - x['cost_usd']) < 1e-3 for x in t['daily']), 'daily cost_by_kind adds up to cost_usd')
        ok(abs(td.get('cost_by_kind', {}).get('subagent', 0) - exp_sub) < 1e-4, 'subagent cost lands in cost_by_kind')
        cp = t.get('cost_pace') or {}
        ok(cp.get('days') == 0 and cp.get('avg_usd') is None and abs(cp.get('today_usd', -1) - td.get('cost_usd', 0)) < 1e-6,
           f'cost pace without earlier days: {cp}')
        ok('timeline' not in t, 'timeline moved out of tokens')
        tl = d['activity'].get('timeline') or {}
        kinds_tl = sorted(x['kind'] for x in tl.get('runs', []))
        ok(kinds_tl == ['headless', 'subagent'], f'timeline holds the subagent and worker runs: {kinds_tl}')
        sub_tl = [x for x in tl.get('runs', []) if x['kind'] == 'subagent']
        ok(sub_tl and sub_tl[0]['minutes'] == 1.0 and '[street]' in sub_tl[0]['description'], 'timeline run: duration and masked description')
        ok(all(x['start'] <= x['end'] and tl['from'] <= x['end'] for x in tl.get('runs', [])), 'timeline runs inside the window')
        om = tl.get('owner_messages', [])
        ok(len(om) == 1 and om[0]['via'] == 'telegram' and set(om[0]) == {'at', 'via'}, f'owner messages: times only, heartbeats excluded ({om})')
        ok('budget' not in d, 'no budget block (tile retired 2026-10-15)')
        hc = {c['name']: c for c in d['health']['checks']}
        ok(hc.get('Server (Unraid)', {}).get('status') == 'critical' and hc['Server (Unraid)']['value'] == '1 critical · 3 warnings',
           f"server health from the context snapshot: {hc.get('Server (Unraid)')}")
        ok(hc.get('Array capacity', {}).get('status') == 'warn' and hc['Array capacity']['value'] == '91% used, full in ~38 d',
           f"array capacity check: {hc.get('Array capacity')}")
        ok(d['health']['summary'].get('critical', 0) >= 1 and [c['name'] for c in d['health']['checks']][:2] == ['Server (Unraid)', 'Array capacity'],
           'server checks come first and count in the summary')
        # goals, dayline (live: no dayline.json in the fixture), context card
        gl = d.get('goals') or {}
        ok([x['title'] for x in gl.get('goals', [])] == ['Fixture goal'] and (gl['done'], gl['total']) == (1, 2), f'fixture goals: {gl}')
        ok('[street]' in (gl['goals'][0]['next'] or ''), 'goal next step masked')
        dl = d['activity'].get('dayline') or {}
        ok(dl.get('today') and set(dl) == {'date', 'today', 'done', 'changes'}, f'dayline: today, done lines and a change count: {sorted(dl)}')
        ok([e['text'][:19] for e in dl.get('done', [])] == ['second fixture thin', 'built fixture thing'], f'dayline entries, newest first: {dl.get("done")}')
        ok('[email]' in dl['done'][1]['text'], 'dayline entry masked')
        ok(dl.get('done', [{}])[0].get('kind') == 'started', 'dayline keeps the entry kind')
        ok(dl.get('changes') == 1, f'dayline: count of today\'s setup changes only (the lines are on the Codebase tab): {dl.get("changes")}')
        # file path: a fresh dayline.json for today is used as-is
        with open(os.path.join(root, 'state', 'dayline.json'), 'w') as f:
            json.dump({'date': dt.datetime.now(ZoneInfo('Europe/Zurich')).strftime('%Y-%m-%d'), 'proactive': [{'time': '07:00', 'kind': 'done', 'text': 'from file'}],
                       'changes': [], 'agents': {}}, f)
        df_ = json.loads(run_fixture(root).stdout)['activity']['dayline']
        ok(df_['done'][0]['text'] == 'from file', f'dayline.json used when fresh: {df_}')
        os.utime(os.path.join(root, 'state', 'dayline.json'), (1, 1))
        df_ = json.loads(run_fixture(root).stdout)['activity']['dayline']
        ok(df_['done'][0]['text'] != 'from file', 'stale dayline.json (older than the logs) -> live')
        cx = d.get('context') or {}
        ok([x['id'] for x in cx.get('hn', [])] == [123] and cx['hn'][0]['tags'] == ['AI'], f'context HN picks validated: {cx.get("hn")}')
        ok(set(cx['hn'][0]) == {'id', 'title', 'points', 'domain', 'tags'}, 'context picks carry no URL')
        ok(set(cx) == {'generated_local', 'age_min', 'hn'}, f'context card: HN picks only (sources list retired 2026-10-15): {sorted(cx)}')
        ok('token=abc123' not in r.stdout and '3 chats summarized' not in r.stdout, 'context source notes never in output')
        text = r.stdout
        for p in ['PLANTEDURL', 'PLANTEDREMINDER', 'PLANTEDPLACE', 'PLANTEDTGCONTENT', '47.5000', '8.5000', 'PersonB']:
            ok(p not in text, f'private context field never in output: {p}')
        for p in PLANTED + ['Musterweg']:
            ok(p not in text, f'planted private text never in output: {p[:12]}…')
        # second run reuses the cache and gives the same token numbers
        r2 = run_fixture(root)
        d2 = json.loads(r2.stdout)
        ok(d2['tokens']['totals'] == t['totals'], 'cached run gives identical totals')
        cache = open(os.path.join(root, 'state', 'cache.json')).read()
        for p in PLANTED:
            ok(p not in cache, f'cache never holds message text: {p[:12]}…')
    finally:
        shutil.rmtree(root, ignore_errors=True)



FAKE_VAULT = r"""#!/usr/bin/env python3
import json, sys, datetime as dt
from zoneinfo import ZoneInfo
now = dt.datetime.now(ZoneInfo('Europe/Zurich'))
ago = lambda **k: (now - dt.timedelta(**k)).isoformat(timespec='seconds')
cmd = sys.argv[1]
if cmd == 'audit':
    print(json.dumps([
        {'ts': ago(days=6, hours=12), 'event': 'job_done'},
        {'ts': ago(days=6), 'event': 'raw_request', 'source': 'tg', 'query': 'PLANTEDQUERY words', 'reason': 'PLANTEDREASON'},
        {'ts': ago(hours=50), 'event': 'tg_recent', 'chars': 10},
        {'ts': ago(hours=30), 'event': 'paperless_search'},
        {'ts': ago(hours=10), 'event': 'raw_released', 'source': 'test'},
        {'ts': ago(hours=9), 'event': 'raw_released', 'source': 'tg', 'query': 'PLANTEDQUERY2'},
        {'ts': ago(hours=8), 'event': 'llm_run', 'flags': ['--anonymize']},
        {'ts': ago(hours=7), 'event': 'paperless_meta'},
        {'ts': ago(hours=6), 'event': 'find'},
        {'ts': ago(hours=5), 'event': 'firefly_summary'},
        {'ts': ago(hours=3), 'event': 'paperless_get'},
        {'ts': ago(hours=2), 'event': 'gmail_get'},
        {'ts': ago(hours=1), 'event': 'tg_search'},
        {'ts': 'not a time', 'event': 'tg_search'},
    ]))
elif cmd == 'status':
    print(json.dumps({'vault': {'policy': 'llm-local-v4.1+regex-2026-10-13'}, 'llm': {'model': 'gemma-4-26B-A4B-it-UD-IQ4_XS.gguf', 'healthy': True},
                      'tg': {'logged_in': True, 'live': {'state': 'running'}}, 'gmail': {'configured': True, 'error': None,
                      'accounts': [{'account': 'main'}, {'account': 'personal'}]}, 'paperless': {'configured': True},
                      'firefly': {'configured': False}, 'raw': {'pending': 1}, 'alerts': 0}))
elif cmd == 'metrics':
    print(json.dumps({'routes': [{'route': 'GET /status', 'calls': 10, 'errors': 0}, {'route': 'POST /tg/search', 'calls': 3, 'errors': 2}]}))
else:
    sys.exit(2)
"""


def test_vault_live():
    root = tempfile.mkdtemp(prefix='dash-test-vault-')
    try:
        make_fixture(root)
        fake = os.path.join(root, 'vault')
        with open(fake, 'w') as f:
            f.write(FAKE_VAULT)
        os.chmod(fake, 0o755)
        r = run_fixture(root, extra_env={'DASH_VAULT': fake, 'DASH_VAULT_TTL': '0'})
        ok(r.returncode == 0, f'vault fixture run exits 0 ({r.stderr[-200:]})')
        if r.returncode != 0:
            return
        L = json.loads(r.stdout)['dataflow']['live']
        ok(L['reads_24h'] == 5 and L['reads_24h_by'] == {'telegram': 1, 'mail': 1, 'documents': 1, 'finance': 1, 'search': 1},
           f"vault: 24 h lookups counted per source, status/admin/LLM events left out ({L['reads_24h']}, {L['reads_24h_by']})")
        ok(L['raw_released_7d'] == 1 and L['raw_tests_7d'] == 1 and L['raw_pending'] == 1, 'vault: raw releases split real/test, pending from status')
        ser = L['reads_daily']
        ok(len(ser) == 7 and ser[-1]['today'] and sum(x['n'] or 0 for x in ser) == 7,
           'vault: 7 daily columns ending today, every lookup of the week counted once')
        first = (datetime.now(ZoneInfo('Europe/Zurich')) - dt.timedelta(days=6, hours=12)).date().isoformat()
        full = [x['n'] for x in ser if not x['today'] and x['date'] > first]
        ok(all(x['n'] is None for x in ser if x['date'] < first) and L['reads_avg'] == (round(sum(full) / len(full), 1) if full else None),
           'vault: days before the first audit entry are empty; average over complete days only')
        ok(L['llm'] == 'Gemma 4 26B' and L['policy'] == 'v4.1' and L['llm_ok'] is True and L['errors_24h'] == 2 and 'calls_24h' not in L,
           'vault: model label, policy and errors from status/metrics (calls not shipped)')
        ok([(x['name'], x['ok']) for x in L['sources']] == [('Telegram', True), ('Gmail', True), ('paperless', True), ('Firefly', False)]
           and L['sources'][1]['note'] == '2 accounts', 'vault: sources and their state')
        ok('PLANTED' not in r.stdout and 'query' not in json.dumps(L) and 'reason' not in json.dumps(L), 'vault: audit text (query, reason) never copied')
        ok(os.path.exists(os.path.join(root, 'state', 'vault-live.json')), 'vault: numbers cached in the state dir')
        r2 = run_fixture(root, extra_env={'DASH_VAULT': '/bin/false', 'DASH_VAULT_TTL': '1800'})
        L2 = json.loads(r2.stdout)['dataflow']['live']
        ok(L2.get('reads_24h') == 5 and '_at' not in L2, 'vault: cache answers within the TTL without asking the vault')
        r3 = run_fixture(root, extra_env={'DASH_VAULT': '/bin/false', 'DASH_VAULT_TTL': '0'})
        ok(r3.returncode == 0 and json.loads(r3.stdout)['dataflow']['live'] == {'error': 'privacy vault not reachable'},
           'vault: unreachable vault gives an error note, the build still succeeds')
    finally:
        shutil.rmtree(root, ignore_errors=True)



# ------------------------------------------------------------------------------------------- 3. real run
def df_nodes(d):
    return (d.get('dataflow') or {}).get('nodes') or []


def test_real(tmpdir):
    out = os.path.join(tmpdir, 'data.json')
    r = subprocess.run([DASH, '--out', out], capture_output=True, text=True, timeout=180)
    ok(r.returncode == 0, f'real run exits 0 ({r.stderr[-300:]})')
    if r.returncode != 0:
        return None
    d = json.load(open(out))
    for k in ('schema', 'generated_at', 'tokens', 'activity', 'health', 'dataflow', 'now'):
        ok(k in d, f'top-level key {k}')
    t = d['tokens']
    ok(t['totals']['tokens'] > 0, 'some tokens counted')
    ok(len(t['hourly']) == 48, '48 hourly buckets')
    dates = [x['date'] for x in t['daily']]
    ok(dates == sorted(dates) and len(set(dates)) == len(dates), 'daily sorted and unique')
    if len(dates) > 1:
        d0 = dt.date.fromisoformat(dates[0])
        ok(all(dt.date.fromisoformat(x) == d0 + dt.timedelta(days=i) for i, x in enumerate(dates)), 'daily contiguous')
    ok(abs(sum(b['cost_usd'] for b in t['by_kind'].values()) - t['totals']['cost_usd']) < 0.05, 'kinds add up to total cost')
    ok(sum(b['tokens'] for b in t['by_model']) == t['totals']['tokens'], 'models add up to total tokens')
    ok(sum(x['tokens']['main'] + x['tokens']['subagent'] + x['tokens']['headless'] for x in t['daily']) == t['totals']['tokens'],
       'daily adds up to total tokens')
    ok(all(abs(sum(x['cost_by_kind'].values()) - x['cost_usd']) < 0.01 for x in t['daily']), 'real daily cost_by_kind adds up')
    cp = t['cost_pace']
    ok(set(cp) >= {'days', 'avg_usd', 'today_usd', 'today_pct_of_avg', 'day_elapsed_pct'} and cp['days'] <= 14, 'cost pace fields')
    tl = d['activity']['timeline']
    starts = [x['start'] for x in tl['runs']]
    ok(starts == sorted(starts) and all(x['end'] >= tl['from'] for x in tl['runs']), 'real timeline sorted, last 24 h only')
    ok(all(set(o) == {'at', 'via'} and o['at'] >= tl['from'] for o in tl['owner_messages']), 'real owner messages: times only, last 24 h')
    ok('budget' not in d and 'assistant' not in d, 'retired blocks gone (budget, assistant -> codebase)')
    a0 = d['activity']
    ok(not ({'proactive', 'brain', 'capabilities', 'inbox_pending'} & set(a0)), 'activity ships no duplicates (proactive, brain, capabilities, inbox)')
    ok(not ({'tools_today', 'unknown_models', 'unknown_models_priced_as'} & set(t)) and 'agents_running' not in d['now'],
       'unused token/now fields gone')
    ok(not ({'container_uptime_s', 'disk_used_pct'} & set(d['health'])) and 'recurring' not in (a0['reminders'] or {}), 'unused health/reminder fields gone')
    tops = [x['tokens'] for x in t['top_runs']]
    ok(tops == sorted(tops, reverse=True), 'top runs sorted by tokens')
    ok('list price' in t['note'].lower() and 'estimate' in t['note'].lower(), 'cost labelled as an estimate')
    ok(all(c['status'] in ('ok', 'warn', 'critical', 'unknown') for c in d['health']['checks']), 'health statuses valid')
    ok(len(d['health']['checks']) >= 8, 'health has the expected checks')
    a = d['activity']
    ok(all({'when', 'job', 'schedule', 'enabled'} <= set(c) for c in a['cron']) and len(a['cron']) >= 10, 'cron parsed')
    ok(isinstance(a['todo']['waiting'], list), 'todo parsed')
    ok(a['reminders'] is None or set(a['reminders']) >= {'total', 'next_at', 'by_tag'}, 'reminders are counts only')
    ok(a['reminders'] is None or 'text' not in json.dumps(a['reminders']), 'no reminder text')
    cb = d.get('codebase') or {}
    st = cb.get('stats') or {}
    ok(set(cb) == {'stats', 'skills', 'agents', 'brain'} and set(st) == {'computed_local', 'plugins', 'hooks', 'tests'},
       f'codebase block: stats, one skills/agents list, brain ({sorted(cb)}, {sorted(st)})')
    ok(set(st.get('tests', {})) >= {'suites', 'checks'} and st['tests']['suites'] >= 5, 'codebase: test suites counted')
    ok(set(cb.get('brain') or {}) == {'commits', 'manual', 'last', 'last_7d'} and cb['brain']['commits'] >= cb['brain']['last_7d'] > 0,
       'codebase: one live config-snapshot counter')
    ok(len(cb.get('skills', [])) >= 5 and all(set(x) == {'name', 'what'} for x in cb['skills'] + cb['agents']), 'codebase: skills and subagents listed once')
    ok(os.path.exists(os.path.join(HOME, '.local', 'state', 'dash', 'assistant-stats.json')), 'codebase: hourly cache written')
    ok(not re.search(r'\.secrets|/context/|telegram\.md|signals\.md|/reports/', json.dumps(cb)), 'codebase: no private paths')
    g = d.get('growth') or {}
    gl_ = (g.get('days') or [{}])[-1]
    ok(g.get('days') and (not g.get('langs') or sum(x['loc'] for x in g['langs']) == gl_.get('loc_total')),
       'one line count: languages add up to the last growth point')
    gt = {x['date']: x for x in g.get('activity', [])}.get(t['today']['date'] if t.get('today') else '', {})
    tt = (t.get('today') or {})
    ok(not tt or (gt.get('subagents') == tt.get('subagent_runs', 0) and gt.get('owner_msgs') == tt['turns'].get('telegram', 0) + tt['turns'].get('you (app/terminal)', 0)),
       f"activity per day: today's point is this run's own counts ({gt} vs {tt.get('subagent_runs')})")
    ok({c['name'] for c in d['health']['checks']} >= {'Server (Unraid)', 'Array capacity'} or not os.path.exists(os.path.join(HOME, '.local', 'state', 'context', 'today.json')),
       'health includes the server checks')
    ok(all(set(n) <= {'id', 'label', 'sub', 'group'} for n in df_nodes(d)) and not ({'routes', 'edge_labels', 'viewbox', 'version', 'sensitivity_levels'} & set(d['dataflow'])),
       'data map drawing fields gone (Architecture draws the system)')
    df = d['dataflow']
    ids = {n['id'] for n in df.get('nodes', [])}
    ok(all(f['from'] in ids and f['to'] in ids and (not f.get('via') or f['via'] in ids) for f in df.get('flows', [])),
       'every flow references known nodes')
    ok(all(f['filter'] in ('raw', 'anonymized', 'metadata') for f in df['flows']), 'flow filters valid')
    ok(all(s_['sensitivity'] in ('low', 'medium', 'high', 'critical') for s_ in df['stores']), 'store sensitivity valid')
    ok(any(f.get('highlight') and f['to'] == 'anthropic' for f in df['flows']), 'Anthropic flow highlighted')
    ok(any(f['from'] == 'vault' and f['filter'] == 'anonymized' for f in df['flows']), 'vault path shown as anonymized')
    gl = d.get('goals') or {}
    ok(isinstance(gl.get('goals'), list) and gl['total'] == sum(g['total'] for g in gl['goals']), 'real goals parsed and add up')
    ok(len(gl['goals']) >= 1 and all(g['title'] for g in gl['goals']), 'GOALS.md has titled goals')
    ln = a.get('lastnight') or {}
    ok(set(ln) == {'window', 'tonight', 'earlier', 'untimed'} and len(ln.get('earlier', [])) == 7, 'real lastnight: tonight + 7 nights')
    nts = [ln.get('tonight') or {}] + ln.get('earlier', [])
    ok(all(n.get('count') == len(n.get('items', [])) or n.get('count', 0) > 40 for n in nts), 'real lastnight: counts match')
    ok(all(len(e['text']) <= 90 and re.fullmatch(r'\d\d:\d\d', e['time']) and (e['time'] < '08:00' or e['time'] >= '22:00')
           for n in nts for e in n.get('items', [])), 'real lastnight: timed night items, at most 90 chars')
    dl = a['dayline']
    ok(dl is None or (set(dl) == {'date', 'today', 'done', 'changes'} and dl['today'] and isinstance(dl['changes'], int)), 'real dayline is today\'s')
    cx = d.get('context')
    ok(cx is None or set(cx) == {'generated_local', 'age_min', 'hn'}, 'real context card has only the HN picks')
    sec = [s_ for s_ in df['stores'] if s_['id'] == 'secrets'][0]
    ok({'homeassistant', 'github', 'gmail', 'firefly'} <= ids and any(f['from'] == 'homeassistant' for f in df['flows']),
       'map shows Home Assistant (location), GitHub and the vault-only sources')
    ok(not any(n['id'] == 'google' and 'Gmail' in n['label'] for n in df['nodes']), 'Gmail is no longer drawn as a claude.ai connector')
    live = df.get('live') or {}

    def leaves(o):
        if isinstance(o, dict):
            for v_ in o.values():
                yield from leaves(v_)
        elif isinstance(o, list):
            for v_ in o:
                yield from leaves(v_)
        else:
            yield o
    ok('error' in live or (isinstance(live.get('reads_24h'), int) and len(live.get('reads_daily', [])) == 7),
       'real vault numbers present (or an error note)')
    ok(all(v_ is None or isinstance(v_, (int, float, bool)) or (isinstance(v_, str) and len(v_) <= 40) for v_ in leaves(live))
       and not re.search(r'"(query|reason|text|chat|sender)"', json.dumps(live)), 'vault numbers are counts and short labels only')
    ok('bytes' not in sec.get('live', {}), 'secrets store shows a file count only')
    return d


# ------------------------------------------------------------------------------------------- 4. privacy sweep
def private_literals():
    """Values that must never show up: owner chat ids and streets from the memory notes (never printed)."""
    lits = set()
    try:
        acc = json.load(open(os.path.join(HOME, '.claude', 'channels', 'telegram', 'access.json')))
        lits |= {str(x) for x in acc.get('allowFrom', [])}
    except (OSError, ValueError):
        pass
    rx = re.compile(r"\b([A-ZÄÖÜ][\wäöüß-]+(?:strasse|straße|gasse|weg)\s*\d+\w?)")
    for f in glob.glob(os.path.join(HOME, '.claude', 'projects', '-home-node-workspace', 'memory', '*.md')):
        lits |= set(rx.findall(open(f, encoding='utf-8', errors='replace').read()))
    return {x for x in lits if len(x) >= 5}


def luhn(num):
    ds = [int(c) for c in num][::-1]
    tot = sum(ds[0::2]) + sum(sum(divmod(2 * x, 10)) for x in ds[1::2])
    return tot % 10 == 0


def sweep(name, text, strs):
    r = subprocess.run([os.path.join(HOME, 'bin', 'secretscan'), '--stdin', '-q'], input=text, capture_output=True, text=True)
    ok(r.returncode == 0, f'{name}: secretscan clean ({r.stdout.strip()[:200]})')
    joined = '\n'.join(strs)
    checks = {
        'e-mail': r'[\w.+-]+@[\w-]+\.[\w.-]+',
        'phone': r'(?<![\w.])\+?\d{2,3}[\s/.-]?\(?\d{2,3}\)?[\s/.-]?\d{3}[\s/.-]?\d{2}[\s/.-]?\d{2}(?![\w.])',
        'IPv4': r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
        'IBAN': r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}',
        'AHV': r'\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b',
        'bot token': r'\b\d{8,10}:[A-Za-z0-9_-]{30,}',
        'api key': r'sk-ant-[A-Za-z0-9_-]{10,}',
        'long number in text': r'(?<![\w.:/-])\d{7,}(?![\w.-])',
        'street + number': r"\b[A-ZÄÖÜ][\wäöüß-]+(?:strasse|straße|gasse)\s*\d+",
        'url with query': r'https?://\S+\?\S+',
    }
    for label, rx in checks.items():
        hits = re.findall(rx, joined)
        ok(not hits, f'{name}: no {label} ({len(hits)} hit(s))')
    cards = [m for m in re.findall(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)', joined) if luhn(re.sub(r'\D', '', m))]
    ok(not cards, f'{name}: no card-like numbers ({len(cards)})')
    for lit in private_literals():
        ok(lit not in text, f'{name}: a private literal from access.json/memory is absent')


def log_lines(data):
    a = data.get('activity') or {}
    ln = a.get('lastnight') or {}
    dl = a.get('dayline') or {}
    out = [e['text'] for n in [ln.get('tonight') or {}] + ln.get('earlier', []) for e in n.get('items', [])]
    out += [e['text'] for e in a.get('changelog', []) + dl.get('done', [])]
    return out


def test_log_privacy(m, data):
    """Names and amounts in log-derived lines: no surname from the people note, a first name only if the rest of the page
    shows it, no money amounts."""
    lines = log_lines(data)
    ok(len(lines) > 10, f'log-derived lines found ({len(lines)})')
    m._PEOPLE = None
    first, sur = m.people()
    shown = m.shown_words(data, m.LOG_BLOCKS)
    bad = {w for w in (first - shown) | sur if any(re.search(r'\b' + re.escape(w) + r'\b', x) for x in lines)}
    ok(not bad, f'log lines: no names from the people note ({len(bad)} hit(s))')
    money = [x for x in lines if re.search(r"(?:CHF|Fr\.|EUR|USD|\$|€|£)\s?~?\d|\d\s?(?:CHF|Fr\.|EUR|€|£)(?![A-Za-z])", x)]
    ok(not money, f'log lines: no currency amounts ({len(money)} hit(s))')


def test_privacy(d):
    data_text = open(os.path.join(HOME, '.local', 'state', 'dash', 'data.json')).read()
    sweep('data.json', data_text, list(strings(json.loads(data_text))))
    for p in (TEMPLATE, PAGE, FRAG):
        if os.path.exists(p):
            txt = open(p).read()
            m = re.search(r'<script id="dash-data"[^>]*>(.*?)</script>', txt, re.S)
            emb = json.loads(m.group(1).replace('<\\/', '</')) if m else None
            sweep(os.path.basename(p), txt, list(strings(emb)) if emb else [])


# ------------------------------------------------------------------------------------------- 5. page structure
def structure(page, name):
    ok('<title>Claude Bot Dashboard</title>' in page[:8192], f'{name}: title in the first 8 KB')
    ok('<!--dash:head-->' in page and '<!--dash:body-->' in page, f'{name}: artifact markers present')
    ok(not re.search(r'<script[^>]+src=|<link[^>]+href=|@import|url\(\s*["\']?https?:', page), f'{name}: no external scripts, styles or fonts')
    ok(not re.search(r'fetch\(\s*["\']https?:', page), f'{name}: no fetch to other hosts')
    ok('prefers-color-scheme: dark' in page and ':root[data-theme="dark"]' in page, f'{name}: dark mode tokens')
    ok('[hidden] { display: none !important; }' in page, f'{name}: hidden attribute enforced')


def test_template(m):
    """The template holds no data (data block {}; dash-data fills a copy) and shows profile values only as {{key}}
    tokens from dash-data's allowlist. Runs in a checkout too (the repo's dash/index.html)."""
    ok(os.path.exists(TEMPLATE), 'page template exists')
    if not os.path.exists(TEMPLATE):
        return
    page = open(TEMPLATE, encoding='utf-8').read()
    d = re.search(r'<script id="dash-data" type="application/json" data-mode="fallback">(.*?)</script>', page, re.S)
    ok(bool(d) and d.group(1) == '{}', 'template: data block present and empty ({})')
    toks = set(re.findall(r'\{\{([a-z_]+(?:\.[a-z_]+)+)\}\}', page))
    ok(toks and toks <= set(m.PAGE_PROFILE_KEYS), f'template: profile tokens only from the allowlist ({sorted(toks)})')
    structure(page, 'template')
    test_arch_static(page)
    test_live_wiring(page)


def test_page():
    page = open(PAGE).read()
    structure(page, 'page')
    ok(not re.search(r'\{\{[a-z_.]+\}\}', page), 'page: every profile token filled')
    m = re.search(r'<script id="dash-data" type="application/json" data-mode="fallback">(.*?)</script>', page, re.S)
    ok(bool(m), 'index.html carries the fallback data block')
    if m:
        emb = json.loads(m.group(1).replace('<\\/', '</'))
        cur = json.load(open(os.path.join(HOME, '.local', 'state', 'dash', 'data.json')))
        ok(emb.get('generated_at') == cur.get('generated_at'), 'inlined copy matches data.json')
    ok(os.path.exists(FRAG), 'dashboard.html exists')
    if os.path.exists(FRAG):
        frag = open(FRAG).read()
        ok(not re.search(r'<!doctype|<html|<head[\s>]|<body', frag, re.I), 'artifact fragment has no doctype/html/head/body')
        ok(frag.lstrip().startswith('<title>Claude Bot Dashboard</title>'), 'fragment starts with its title')
        ok('data-mode="inline"' in frag, 'fragment uses the inline data only')
        ok(len(frag.encode()) < 16 * 1024 * 1024, 'fragment under 16 MB')


ARCH_WORDS = ['Main session', 'Policy filter', 'Anthropic', 'Cron jobs', 'Vault bot', 'nginx VM', 'Unraid server']


def test_live_wiring(page):
    """Live data-flow panel (flowd /live): mapped lines are drawn in both maps, flowd edge ids are real, same origin only."""
    m = re.search(r'var LIVE_MAP = \{(.*?)\};', page, re.S)
    n = re.search(r'var LIVE_NONE = \{(.*?)\};', page, re.S)
    t = re.search(r'<template id="arch-tpl">(.*?)</template>', page, re.S)
    ok(bool(m and n and t), 'live: LIVE_MAP, LIVE_NONE and the map template present')
    if not (m and n and t):
        return
    pairs = re.findall(r"'([\w:-]+)': \[([^\]]*)\]", m.group(1))
    keys = {k for k, _ in pairs}
    vals = {v for _, vs in pairs for v in re.findall(r"'([^']+)'", vs)}
    maps = re.findall(r'<svg class="arch (arch-[pl])"(.*?)</svg>', t.group(1), re.S)
    for name, body in maps:
        body = re.sub(r'<!--.*?-->', '', body, flags=re.S)   # comments name the id scheme
        drawn = set(re.findall(r'data-edge="([\w-]+)"', body))
        ok(all(k in drawn for k in keys if k != 'node:main') and 'data-node="main"' in body, f'live: every mapped line is drawn in {name}')
        none = set(re.findall(r"'([\w-]+)':", n.group(1)))
        inside = drawn - keys - none
        ok(inside <= {'feed-filter', 'filter-approvals', 'filter-llm', 'data-llm', 'paperless-unraid'},
           f'live: {name}: only lines inside the vault have no feed ({sorted(inside)})')
    fl = os.path.join(HOME, 'bin', 'flowd')
    edges = subprocess.run([fl, 'edges'], capture_output=True, text=True).stdout.split() if os.path.exists(fl) else []
    ok(bool(edges) and vals <= set(edges) and set(edges) <= vals, 'live: map uses exactly the flowd edge ids')
    ok("new EventSource('live?" in page and not re.search(r"EventSource\(\s*['\"](?:https?:)?//", page)
       and "fetch('live/more?id='" in page, 'live: same-origin stream and more-fetch only')
    ok('liveWire(v);' in page and 'livePulse(view ===' in page and "if (view !== 'architecture') liveClose();" in page,
       'live: wired on render, pulse stream only while the tab is shown')
    lines = [ln for ln in page.split('\n') if 'localStorage.' in ln]
    ok(lines and all('try {' in ln for ln in lines), 'live filters: every localStorage access sits in a try')
    ok("'dash-live-f:' + key" in page and "'dash-live-f:' + live.key" in page, 'live filters: stored per view')
    ok('innerHTML' not in page.split('var LF_KEYS')[1].split('function liveFlash')[0], 'live filters: highlight without innerHTML')
    ok("['kind', 'from', 'to', 'dir'].forEach" in page, 'live filters: kind, from, to and direction go to flowd')
    ok('carry metadata only' not in page and "'me→vault': 'Everything my session sees" in page,
       'live: the vault note says it shows what my session sees')
    ok("q.push('err=1')" in page and "'&since='" in page and "'edge=' + encodeURIComponent(edges.join(','))" in page,
       'live filters: errors and time window go to flowd, the search stays in the page')


def test_arch_static(page):
    ok('id="tab-architecture"' in page and "'privacy', 'architecture']" in page and 'architecture: renderArchitecture' in page,
       'architecture: tab, view list and renderer wired')
    m = re.search(r'<template id="arch-tpl">(.*?)</template>', page, re.S)
    ok(bool(m), 'architecture: template present')
    if not m:
        return
    t = m.group(1)
    svgs = {c: (a, b) for c, a, b in re.findall(r'<svg class="arch (arch-[pl])"([^>]*)>(.*?)</svg>', t, re.S)}
    ok(set(svgs) == {'arch-p', 'arch-l'} and t.count('<svg class="arch') == 2, 'architecture: portrait and landscape SVG, one each')
    for c, (a, b) in svgs.items():
        ok('role="img"' in a and re.search(r'aria-label="[^"]{80,}"', a), f'architecture {c}: inline SVG with role=img and a real aria-label')
        ok(all(w in b for w in ARCH_WORDS), f'architecture {c}: all zones and key parts drawn')
        refs, defs = set(re.findall(r'url\(#([\w-]+)\)', b)), set(re.findall(r'\bid="([^"]+)"', b))
        ok(refs and refs <= defs, f'architecture {c}: uses only its own markers (the hidden map cannot break it)')
    if set(svgs) == {'arch-p', 'arch-l'}:
        test_arch_pair(svgs)
    ok('<figcaption' in t and 'privacy boundary' in t and 'control or approval' in t, 'architecture: caption and legend')
    ok(not re.search(r'<script|<style|<foreignObject|<image|href="http', t), 'architecture: SVG self-contained (no script/style/foreignObject/external refs)')
    ok(not re.search(r'#[0-9a-fA-F]{3,6}\b', t), 'architecture: colours only via CSS variables')
    ok(not re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', t), 'architecture: no IP addresses')
    ok(not re.search(r'[\w.+-]+@[\w-]+\.[\w.]+|https?://', t), 'architecture: no e-mails or URLs')
    r = subprocess.run([os.path.join(HOME, 'bin', 'secretscan'), '--stdin'], input=t, capture_output=True, text=True,
                       timeout=60)   # ~/bin by path: CI's PATH has no ~/bin
    ok(r.returncode == 0, 'architecture: secretscan clean')
    ids = re.findall(r'\bid="([^"]+)"', t)
    ok(len(ids) == len(set(ids)) and all(i.startswith('ax-') for i in ids), 'architecture: SVG ids unique and prefixed ax-')
    ok(all(re.search(r'id="' + i + r'"', t) for i in re.findall(r'url\(#([\w-]+)\)', t)), 'architecture: every marker reference resolves')
    m = load_module()
    first, sur = m.people()
    txt = ' '.join(re.findall(r'>([^<]+)<', t) + re.findall(r'aria-label="([^"]+)"', t))
    if not os.path.exists(m.PEOPLE_NOTE):   # checkout/CI: no people note, nothing to leak
        print('skip: architecture people-name check (no people note)')
        return
    ok(first and not any(re.search(r'\b' + re.escape(w) + r'\b', txt) for w in first | sur), 'architecture: no names from the people note')


def arch_parts(body):
    """{'node': {name: rect classes}, 'edge': {name: path classes}} of one map, plus a list of duplicate names."""
    root = ET.fromstring('<svg>' + body + '</svg>')
    out, dup = {'node': {}, 'edge': {}}, []
    for g in root.iter('g'):
        for kind in ('node', 'edge'):
            name = g.get('data-' + kind)
            if name is None:
                continue
            if name in out[kind]:
                dup.append(name)
            tag = 'rect' if kind == 'node' else 'path'
            el = next((x for x in g if x.tag == tag), None)
            out[kind][name] = el.get('class') if el is not None else None
    return out, dup


def test_arch_pair(svgs):
    """Portrait (phones) and landscape (>= 760 px): same nodes and edges by name, same edge styles."""
    vb = {c: [float(x) for x in re.search(r'viewBox="([^"]+)"', a).group(1).split()] for c, (a, b) in svgs.items()}
    ok(vb['arch-p'] == [0, 0, 360, 836], 'architecture: phone map keeps its portrait viewBox')
    ok(vb['arch-l'][2] >= 1000 and vb['arch-l'][2] >= 1.6 * vb['arch-l'][3], 'architecture: landscape map is wide (>= 1000 units, >= 1.6:1)')
    try:
        (p, dp), (l, dl) = arch_parts(svgs['arch-p'][1]), arch_parts(svgs['arch-l'][1])
    except ET.ParseError as e:
        ok(False, f'architecture: both maps parse as XML ({e})')
        return
    ok(not dp and not dl, f'architecture: node and edge names unique within each map ({dp + dl})')
    ok(len(p['node']) >= 20 and set(p['node']) == set(l['node']), 'architecture: same data-node names in both maps')
    ok(len(p['edge']) >= 20 and set(p['edge']) == set(l['edge']), 'architecture: same data-edge names in both maps')
    ok(all(re.fullmatch(r'[a-z]+', n) for n in p['node']), 'architecture: node names are plain lowercase words')
    ok(all(len(e.split('-')) == 2 and set(e.split('-')) <= set(p['node']) for e in p['edge']), 'architecture: edge names are from-to node names')
    ok(all(v and 'b' in v.split() or v == 'grp' for v in list(p['node'].values()) + list(l['node'].values())), 'architecture: every node has its box')
    ok(all(p['edge'][e] and p['edge'][e].split()[0] == 'e' for e in p['edge']) and all(p['edge'][e] == l['edge'].get(e) for e in p['edge']),
       'architecture: every edge has a path, styled the same in both maps')
    ok(sum('hot' in (v or '') for v in l['edge'].values()) == 1 and sum('ok' in (v or '').split() for v in l['edge'].values()) >= 1,
       'architecture: one Anthropic edge, answers-only edge present')
    ok(svgs['arch-l'][1].count('data-part="boundary"') == 2 and svgs['arch-p'][1].count('data-part="boundary"') == 2, 'architecture: boundary line and lock tagged in both maps')


# ------------------------------------------------------------------------------------------- 7. dash-growth
def test_growth():
    gb = os.environ.get('GROWTH_BIN', os.path.join(HOME, 'bin', 'dash-growth'))
    root = tempfile.mkdtemp(prefix='dash-growth-')
    try:
        tree, vwt = os.path.join(root, 'home'), os.path.join(root, 'vault')
        today = dt.datetime.now(ZoneInfo('Europe/Zurich')).date()
        d1, d2, d3 = (today - dt.timedelta(days=k) for k in (3, 2, 1))

        def w(base, rel, text, mode=0o644):
            p = os.path.join(base, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write(text)
            os.chmod(p, mode)

        def commit(gd, wt, day, hhmm):
            stamp = f'{day.isoformat()}T{hhmm}:00+02:00'
            env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, GIT_AUTHOR_NAME='t', GIT_COMMITTER_NAME='t',
                       GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_EMAIL='t@example.invalid')
            g = ['git', '--git-dir', gd, '--work-tree', wt]
            subprocess.run(g + ['add', '-A'], env=env, check=True, capture_output=True, cwd=wt)
            subprocess.run(g + ['commit', '-q', '-m', 'x'], env=env, check=True, capture_output=True, cwd=wt)
        brain = os.path.join(root, 'brain.git')
        subprocess.run(['git', 'init', '-q', '--bare', brain], check=True)
        subprocess.run(['git', 'init', '-q', vwt], check=True)
        w(tree, 'bin/a', '#!/usr/bin/env python3\nx = 1\n\ny = 2\n', 0o755)
        w(tree, 'workspace/CLAUDE.md', 'x' * 2047 + '\n')
        w(tree, 'crontab', '# c\nPATH=/bin\n* * * * * a\n5 * * * * b\n')
        w(tree, '.claude/skills/s1/SKILL.md', 'a\nb\nc\nd\n')
        w(tree, '.claude/agents/x.md', 'a\nb\n')
        commit(brain, tree, d1, '10:00')
        w(tree, 'bin/a', '#!/usr/bin/env python3\nx = 1\ny = 2\nz = 3\nw = 4\n', 0o755)   # 5 lines: the day's last commit wins
        commit(brain, tree, d1, '22:00')
        w(tree, 'bin/b', '#!/bin/sh\necho hi\n', 0o755)
        w(tree, 'opt/claude-vault/app/v.py', ''.join(f'v{i} = {i}\n' for i in range(6)))
        w(tree, 'opt/claude-vault/data/x.json', '{"k": "PRIVATE-NEVER-COUNTED"}\n' * 50)
        w(tree, 'opt/claude-vault/app/notes.txt', 'not code\n' * 9)
        commit(brain, tree, d3, '12:00')
        w(vwt, 'app/v.py', ''.join(f'v{i} = {i}\n' for i in range(6)))
        w(vwt, 'app/w.py', 'w = 1\n')
        w(vwt, 'data/big.json', '{}\n' * 99)
        commit(os.path.join(vwt, '.git'), vwt, d3, '12:30')
        w(tree, 'opt/claude-vault/app/w.py', 'w = 1\n')            # live tree = vault repo state
        dd = os.path.join(root, 'data.json')
        with open(dd, 'w') as f:
            json.dump({'tokens': {'daily': [{'date': d1.isoformat(), 'turns': {'heartbeat': 4, 'telegram': 2, 'you (app/terminal)': 1},
                                             'subagent_runs': 3}]}}, f)
        out = os.path.join(root, 'growth.json')
        env = dict(os.environ, DASH_GROWTH_OUT=out, DASH_GROWTH_BRAIN=brain, DASH_GROWTH_VAULT=vwt, DASH_GROWTH_TREE=tree,
                   DASH_GROWTH_DASHDATA=dd)
        r = subprocess.run([gb, '--backfill'], env=env, capture_output=True, text=True, timeout=60)
        ok(r.returncode == 0, f'dash-growth --backfill exits 0 ({r.stderr[-200:]})')
        if r.returncode != 0:
            return
        g = json.load(open(out))
        days = {x['date']: x for x in g['days']}
        ok(sorted(days) == [d.isoformat() for d in (d1, d2, d3, today)], 'growth: one point per day, gaps carried forward, today live')
        a, b, c, t = (days[d.isoformat()] for d in (d1, d2, d3, today))
        ok(a['loc']['scripts'] == 5 and a['counts'] == {'scripts': 1, 'skills': 1, 'agents': 1, 'cron': 2}, 'growth: last commit of the day, counts')
        ok(a['claude_md_kb'] == 2.0 and a['loc']['skills'] == 4 and a['loc']['agents'] == 2 and a['loc']['notes'] == 1, 'growth: prompts and CLAUDE.md KB')
        ok(b['source'] == 'git (carried)' and b['loc'] == a['loc'], 'growth: a day without commits carries the previous state')
        ok(c['counts']['scripts'] == 2 and c['loc']['vault'] == 7, 'growth: vault from its own repo (code only, data ignored)')
        ok(t['source'] == 'live' and t['loc'] == c['loc'] and t['counts'] == c['counts'], 'growth: live tree matches the last git state')
        ok(g['activity'].get(d1.isoformat()) == {'heartbeats': 4, 'owner_msgs': 3, 'subagents': 3}, 'growth: activity from dash-data daily counts')
        ok('PRIVATE' not in open(out).read(), 'growth: numbers only')
        m0 = os.path.getmtime(out)
        subprocess.run([gb, '--if-stale', '55'], env=env, capture_output=True, timeout=60)
        ok(os.path.getmtime(out) == m0, 'growth: --if-stale skips a fresh file')
        g['days'] = [x for x in g['days'] if x['date'] != d2.isoformat()]          # a missed day + yesterday still 'live'
        for x in g['days']:
            if x['date'] == d3.isoformat():
                x['source'], x['loc'] = 'live', dict(x['loc'], scripts=999)
        with open(out, 'w') as f:
            json.dump(g, f)
        r = subprocess.run([gb], env=env, capture_output=True, text=True, timeout=60)
        days = {x['date']: x for x in json.load(open(out))['days']}
        ok(r.returncode == 0 and d2.isoformat() in days and days[d3.isoformat()]['source'] == 'git'
           and days[d3.isoformat()]['loc'] == t['loc'], 'growth: a new day rebuilds past days from git (gap filled, live point replaced)')
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------------------------------- 6. render
def chrome(args, timeout=90, budget=4000):
    prof = tempfile.mkdtemp(prefix='dash-chrome-')
    try:
        return subprocess.run(['google-chrome', '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
                               '--hide-scrollbars', '--user-data-dir=' + prof, f'--virtual-time-budget={budget}'] + args,
                              capture_output=True, text=True, timeout=timeout)
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def test_render(tmpdir):
    if not shutil.which('google-chrome'):
        ok(False, 'google-chrome available')
        return
    expect = {'today': ['Tokens today', 'Agent runs today', 'Your messages today', '>Health<', 'Right now', '>Goals<', '>Work log<', '>Last night<', '>Today<'],
              'usage': ['Last 24 hours', 'Token usage', 'Cost per day', '>By type<', 'Where the tokens go', 'By model', 'Most used tools', '>Agent runs<', '>Latest<', 'How the estimate works'],
              'activity': ['Last 24 hours', 'Background agents', 'What woke me up', 'Activity per day', '>Reminders<'],
              'codebase': ['Lines of code + prompts', 'Scheduled jobs', 'Config snapshots', 'Lines over time', 'Counts over time', 'CLAUDE.md size',
                           'Code by language', 'Skills and subagents', 'Changes to my setup', 'Around the clock'],
              'privacy': ['Where data is stored', 'Flows', 'Sent to Anthropic', 'Filtered by the vault', 'class="nodechips"'],
              'architecture': ['Architecture', 'How it works', 'watch its traffic live'] + ARCH_WORDS}
    gone = ['Cost per day by source', 'Estimated cost per day', 'Biggest agent runs', 'Latest agent runs', 'My task list', 'What I can do', '>Config history<',
            'Recent work', 'Recent changes', 'Features that run on their own', '🤖 Assistant', '📈 Growth', 'Health checks', '>Sources<', 'class="diagram', 'budget']
    doms = {}
    for view, words in expect.items():
        r = chrome(['--window-size=390,2400', '--dump-dom', f'file://{PAGE}#{view}'])
        dom = doms[view] = r.stdout
        ok(all(w in dom for w in words), f'{view}: rendered text present ({[w for w in words if w not in dom]})')
        ok('<div class="err"' not in dom, f'{view}: no render error')
        sel = re.search(r'id="view-' + view + r'"[^>]*>', dom)
        ok(sel and 'hidden' not in sel.group(0), f'{view}: its section is the visible one')
    allv = doms['today']   # every tab is rendered into the DOM, so one dump holds all of them
    ok(allv.count('role="tab"') == 6 and allv.count('<section class="view"') == 6, 'six tabs, six sections')
    # retired cards: headings, tab labels and classes only (data such as a work-log line may quote an old card name)
    struct = ''.join('>' + re.sub(r'<[^>]+>', '', x[1]) + '<' for x in re.findall(r'<(h[1-6]|button)\b[^>]*>(.*?)</\1>', allv.split('<script')[0], re.S))
    struct += ' '.join(re.findall(r'class="[^"]*', allv.split('<script')[0]))
    ok(not [w for w in gone if w in struct], f'retired cards are gone ({[w for w in gone if w in struct]})')
    for old, new in (('overview', 'today'), ('health', 'today'), ('tokens', 'usage'), ('assistant', 'codebase'), ('growth', 'codebase'), ('data', 'privacy')):
        dom = chrome(['--window-size=390,1200', '--dump-dom', f'file://{PAGE}#{old}']).stdout
        sel = re.search(r'id="view-' + new + r'"[^>]*>', dom)
        ok(sel and 'hidden' not in sel.group(0) and '<div class="err"' not in dom, f'old hash #{old} opens {new}')
    dom = doms['usage']
    ok(dom.count('<path') > 20, 'token charts drew bars')
    ok('>avg $' in dom or 'no earlier days yet' in dom, 'cost chart shows the daily average line')
    cur = json.load(open(os.path.join(HOME, '.local', 'state', 'dash', 'data.json')))
    ok(doms['activity'].count('<circle') >= len(cur['activity']['timeline']['owner_messages']), 'timeline drew a dot per owner message')
    # every metric once: the numbers that used to disagree
    def view_of(dom, v):
        a_ = dom.find(f'id="view-{v}"')
        return dom[a_:dom.find('</section>', a_)]
    td_ = cur['tokens']['today']
    tday = view_of(allv, 'today')
    ok(f'>{td_["runs"]:,}<' in tday and f'{td_["subagent_runs"]:,} subagent' in tday, 'today: one "agent runs today" tile with its subagent split')
    ok(not any('subagents today' in view_of(allv, v) for v in ('today', 'codebase', 'activity')), 'no second "subagents today" number')
    g = cur.get('growth') or {}
    if g.get('days'):
        lt = g['days'][-1]['loc_total']
        cbv = view_of(allv, 'codebase')
        ok(cbv.count('>Lines of code') == 1 and '>Lines of code' not in tday and 'lines of code<' not in tday, 'one line-count tile, none on Today')
        ok(not g.get('langs') or sum(x['loc'] for x in g['langs']) == lt, 'code by language adds up to the Lines tile')
    br = cur['codebase']['brain']
    ok(view_of(allv, 'codebase').count(f'>{br["commits"]:,}<') == 1 and '>Config history<' not in allv, 'one config-snapshot count')
    hc = [c for c in cur['health']['checks'] if c['status'] != 'ok']
    ok(all(c['name'] + '<' in tday for c in hc), 'today: every non-OK health check (incl. the server) is listed')
    srv = [c for c in cur['health']['checks'] if c['name'] == 'Server (Unraid)']
    ok(not srv or srv[0]['value'] in tday, 'today: the server\'s critical/warning counts are on the Health card')
    ok(tday.count('In progress<') == 1 and allv.count('In progress<') == 1, 'to-do board shown once (Today)')
    ok('class="tile wide headroom"' not in allv and 'disk-row' not in allv and 'disk_headroom' not in cur, 'no disk headroom tile (retired with trip-guard)')
    # work log: page copies with afternoon and morning data (mornings open on last night)
    page = open(PAGE).read()
    lnr = cur['activity'].get('lastnight') or {}

    def copy_with(name, hhmm):
        c2 = json.loads(json.dumps(cur))
        c2['generated_local'] = c2['generated_local'][:11] + hhmm
        payload = json.dumps(c2, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
        out = os.path.join(tmpdir, name)
        with open(out, 'w') as f:
            f.write(re.sub(r'(<script id="dash-data"[^>]*>)(.*?)(</script>)', lambda m: m.group(1) + payload + m.group(3), page, count=1, flags=re.S))
        return out
    mpage, apage = copy_with('morning.html', '07:30'), copy_with('afternoon.html', '15:30')
    dom = chrome(['--window-size=390,2400', '--dump-dom', f'file://{apage}#today']).stdout
    ok('>Today I did · ' in dom and '<div class="err"' not in dom, 'work log: afternoons open on today')
    dom = chrome(['--window-size=390,2400', '--dump-dom', f'file://{mpage}#today']).stdout
    if (lnr.get('tonight') or {}).get('count'):
        ok('>Last night · ' in dom and re.search(r'>Last \d nights</summary>', dom)
           and dom.count('<details class="table night"') == sum(1 for n in lnr.get('earlier', []) if n['count']),
           'work log: mornings open on last night, earlier nights collapsible')
    else:
        ok('>Today I did · ' in dom, 'work log: an empty night stays on today')
    # the artifact fragment inside a skeleton like the one claude.ai adds
    wrap = os.path.join(tmpdir, 'artifact.html')
    with open(wrap, 'w') as f:
        f.write('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
                'initial-scale=1, viewport-fit=cover"><style>[hidden]{display:none!important}body{margin:0}</style></head><body>'
                + open(FRAG).read() + '</body></html>')
    dom = chrome(['--window-size=390,2400', '--dump-dom', f'file://{wrap}#privacy']).stdout
    ok('Where data is stored' in dom and '<div class="err"' not in dom, 'artifact fragment renders')
    # phone screenshots (light, plus dark for two tabs)
    os.makedirs(REPORTS, exist_ok=True)
    for view in expect:
        out = os.path.join(REPORTS, f'phone-{view}.png')
        chrome(['--window-size=390,2200', f'--screenshot={out}', f'file://{PAGE}#{view}'])
        ok(os.path.exists(out) and os.path.getsize(out) > 20000, f'screenshot {view}')
    for name, dark in (('phone-today-morning.png', False), ('phone-today-morning-dark.png', True)):
        out = os.path.join(REPORTS, name)
        chrome(['--window-size=390,2600'] + (['--force-dark-mode'] if dark else []) + [f'--screenshot={out}', f'file://{mpage}#today'])
        ok(os.path.exists(out) and os.path.getsize(out) > 20000, f'screenshot {name}')
    dom = doms['privacy']
    live = cur['dataflow'].get('live') or {}
    ok(('Private lookups' in dom and 'Exact text released' in dom and 'Vault filter' in dom) if not live.get('error') else 'Live vault numbers unavailable' in dom,
       'data: live vault tiles (or the unavailable note) rendered')
    st = dom[dom.find('Where data is stored'):]
    ranks = [{'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}[m] for m in re.findall(r'</span>(Critical|High|Medium|Low)</span>', st)]
    ok(len(ranks) == len(cur['dataflow']['stores']) and ranks == sorted(ranks), 'data: stores sorted by sensitivity, one chip each')
    ok(dom.count('<details class="store"') == len(cur['dataflow']['stores']), 'data: stores are compact expandable rows')
    used = {x for f in cur['dataflow']['flows'] for x in (f['from'], f['to'], f.get('via')) if x}
    ok(view_of(dom, 'privacy').count('<button type="button" class="g-') == len(used), 'privacy: one filter chip per place that has flows')
    dom = doms['codebase']
    g = cur.get('growth') or {}
    ok(g.get('days') and dom.count('<path') >= len(g.get('parts', [])) + 6 and 'class="empty">No growth data yet' not in dom, 'growth: stacked area + line charts drawn')
    ok(dom.count('class="mult"') == 4, 'growth: four count multiples')
    dom = chrome(['--window-size=390,2400', '--dump-dom', f'file://{PAGE}#architecture']).stdout
    sec = dom[dom.find('id="view-architecture"'):dom.find('<footer')]
    ok(re.match(r'id="view-architecture"[^>]*>\s*<div class="grid2 arch-grid">', sec)
       and sec.count('<svg class="arch arch-p"') == 1 and sec.count('<svg class="arch arch-l"') == 1,
       'architecture: both maps cloned into the view once')
    ok(dom.count('class="b') >= 60 and dom.count('marker-end=') >= 40, 'architecture: boxes and arrows drawn')
    for w in (390, 800, 1280):
        pr = arch_probe(tmpdir, w)
        ok(pr is not None, f'architecture probe ran at {w}px')
        if pr:
            want = 'arch arch-p' if w < 760 else 'arch arch-l'
            ok(pr['nvis'] == 1 and pr['vis'] == want, f'architecture: only the {want[10:]} map shown at {w}px ({pr["nvis"]} shown)')
            ok(pr['sw'] <= pr['iw'], f'architecture: no horizontal page scroll at {w}px')
            ok(not pr['overflow'], f'architecture: every label fits its box at {w}px ({pr["overflow"][:3]})')
            ok(pr['tabvis'], f'architecture: selected tab visible in the tab bar at {w}px')
            ok(0 < pr['svgw'] <= pr['panelw'], f'architecture: SVG fits its panel at {w}px')
            if w >= 1200:
                ok(pr['svgw'] >= 0.95 * pr['panelw'] and pr['notesbelow'], f'architecture: map spans the panel, notes under it at {w}px')
                ok(pr['lpx'] >= 12.5, f'architecture: box labels readable at {w}px ({pr["lpx"]:.1f}px)')
    for view in ('today', 'usage', 'activity', 'codebase', 'privacy', 'architecture'):
        out = os.path.join(REPORTS, f'phone-{view}-dark.png')
        chrome(['--window-size=390,2200', '--force-dark-mode', f'--screenshot={out}', f'file://{PAGE}#{view}'])
        ok(os.path.exists(out) and os.path.getsize(out) > 20000, f'dark screenshot {view}')


ARCH_PROBE = """<script>(function () {
  var de = document.documentElement, out = [], shown = [].filter.call(document.querySelectorAll('svg.arch'), function (s) { return s.getBoundingClientRect().width > 0; });
  var svg = shown[0] || null;
  var boxes = svg ? [].slice.call(svg.querySelectorAll('rect.b, rect.grp, rect.z')) : [];
  if (svg) [].forEach.call(svg.querySelectorAll('text.l, text.s, text.m'), function (t) {
    var bb = t.getBBox(), x = parseFloat(t.getAttribute('x')), y = parseFloat(t.getAttribute('y')), host = null;
    boxes.forEach(function (r) {   // smallest box holding the label's anchor point
      var rx = +r.getAttribute('x'), ry = +r.getAttribute('y'), rw = +r.getAttribute('width'), rh = +r.getAttribute('height');
      if (x >= rx && x <= rx + rw && y - 4 >= ry && y <= ry + rh && (!host || rw * rh < host[2] * host[3])) host = [rx, ry, rw, rh];
    });
    if (!host || bb.x < host[0] + 1 || bb.x + bb.width > host[0] + host[2] - 1 || bb.y < host[1] || bb.y + bb.height > host[1] + host[3] + 1)
      out.push(t.textContent);
  });
  var tab = document.getElementById('tab-architecture'), bar = tab.parentNode, tr = tab.getBoundingClientRect(), br = bar.getBoundingClientRect();
  var panel = svg && svg.closest('.panel');
  document.body.setAttribute('data-probe', JSON.stringify({ sw: de.scrollWidth, iw: innerWidth, overflow: out,
    tabvis: tr.left >= br.left - 1 && tr.right <= br.right + 1,
    svgw: svg ? svg.getBoundingClientRect().width : 0, panelw: panel ? panel.clientWidth : 0,
    nvis: shown.length, vis: svg ? svg.getAttribute('class') : '',
    notesbelow: !!(panel && document.querySelector('.arch-notes') && document.querySelector('.arch-notes').getBoundingClientRect().top >= panel.getBoundingClientRect().bottom),
    lpx: svg ? parseFloat(getComputedStyle(svg.querySelector('text.l')).fontSize) * svg.getBoundingClientRect().width / svg.viewBox.baseVal.width : 0 }));
})();</script>"""


def arch_probe(tmpdir, width):
    """Architecture tab measured in a real layout: page width, labels inside their boxes, tab in view."""
    page = open(PAGE).read()
    i = page.rindex('</body>')
    probe = os.path.join(tmpdir, 'arch-probe.html')
    with open(probe, 'w') as f:
        f.write(page[:i] + ARCH_PROBE + page[i:])
    dom = chrome([f'--window-size={width},1400', '--dump-dom', f'file://{probe}#architecture']).stdout
    m = re.search(r'data-probe="([^"]*)"', dom)
    return json.loads(m.group(1).replace('&quot;', '"').replace('&amp;', '&')) if m else None



# ------------------------------------------------------------------------------------------- 7. live filters (probe)
LIVE_FAKE = """<script>(function () {
  var now = Date.now() / 1000, pad = new Array(60).join('filler text ');
  window.__evs = [
    {id: 1, t: now - 7200, edge: 'me→vault', from: 'me', to: 'vault', kind: 'vault_call', chip: 'ask', actor: 'main', meta: {cmd: 'ask', tool: 'Bash'}},
    {id: 2, t: now - 100, edge: 'cron→inbox', from: 'cron', to: 'inbox', kind: 'note', chip: 'agent-watch', actor: 'cron', text: 'agent stalled', meta: {}},
    {id: 3, t: now - 40, edge: 'me→anthropic', from: 'me', to: 'anthropic', kind: 'request', chip: 'request', actor: 'main', text: pad + ' the needle sits deep here', meta: {}},
    {id: 4, t: now - 30, edge: 'me→tools', from: 'tools', to: 'me', kind: 'tool_result', chip: 'error', actor: 'main', text: 'boom: needle failed', meta: {exit: 2}},
    {id: 5, t: now - 20, edge: 'me→tools', from: 'me', to: 'tools', kind: 'tool_call', chip: 'Bash', actor: 'main', text: 'ls -la', meta: {cmd: 'ls'}},
    {id: 6, t: now - 10, edge: 'telegram→me', from: 'telegram', to: 'me', kind: 'message', chip: 'text', actor: 'main', text: 'Can you check the Needle server?', meta: {}}
  ];
  window.__es = [];
  function ES(url) {
    var s = this;
    s.url = url; s.readyState = 1; s.l = {}; window.__es.push(s);
    setTimeout(function () { s.emit('hello', {}); window.__evs.forEach(function (e) { s.emit('ev', e); }); }, 20);
  }
  ES.prototype.addEventListener = function (t, fn) { (this.l[t] = this.l[t] || []).push(fn); };
  ES.prototype.close = function () { this.readyState = 2; this.closed = true; };
  ES.prototype.emit = function (t, d) { if (this.closed) return; (this.l[t] || []).forEach(function (fn) { fn({ data: JSON.stringify(d) }); }); };
  window.EventSource = ES;
})();</script>"""

LIVE_PROBE = """<script>(function () {
  var out = [];
  function P() { return document.querySelector('aside.live'); }
  function liveES() { return (window.__es || []).filter(function (x) { return x.url.indexOf('edge=') >= 0 && !x.closed; }).pop(); }
  function snap(name) {
    var p = P(), st = null, e = liveES();
    try { st = localStorage.getItem('dash-live-f:all'); } catch (err) { st = 'blocked'; }
    out.push({ name: name, open: !!(p && !p.hidden), title: p ? p.querySelector('h3').textContent : '',
      n: document.querySelectorAll('aside.live .live-ev').length, marks: document.querySelectorAll('aside.live mark.lf-hit').length,
      openRows: document.querySelectorAll('aside.live .live-ev.open').length, errRows: document.querySelectorAll('aside.live .live-ev.is-err').length,
      q: p ? p.querySelector('.lf-search input').value : '', hash: location.hash, es: e ? e.url : '', nes: (window.__es || []).length, store: st,
      count: p ? p.querySelector('.ls-count').textContent : '', status: p ? p.querySelector('.ls-text').textContent : '',
      none: !!document.querySelector('aside.live .lf-none'), pressed: p ? p.querySelector('.lf-err').getAttribute('aria-pressed') : '',
      win: p ? p.querySelector('select[data-f="win"]').value : '', grid: p ? !p.querySelector('.lf-grid').hidden : null,
      sheet: p ? Math.round(p.getBoundingClientRect().height) : 0, list: p ? Math.round(p.querySelector('.live-list').getBoundingClientRect().height) : 0,
      bar: p ? Math.round(p.querySelector('.live-filters').getBoundingClientRect().height) : 0, ih: window.innerHeight,
      sw: document.documentElement.scrollWidth, iw: window.innerWidth, pw: p ? p.scrollWidth - p.clientWidth : 0 });
  }
  function sel(name, v) { var s = P().querySelector('select[data-f="' + name + '"]'); s.value = v; s.dispatchEvent(new Event('change')); }
  function click(css) { P().querySelector(css).click(); }
  var steps = [
    function () { snap('boot'); click('.lf-err'); },
    function () { snap('errors'); click('.lf-clear'); },
    function () { snap('errors-noq'); click('.lf-err'); sel('kind', 'tool_call'); },
    function () { snap('kind'); sel('kind', ''); sel('dir', 'in'); },
    function () { snap('dir'); sel('dir', ''); sel('win', '300'); },
    function () { snap('win'); click('.live-head button:first-of-type'); },
    function () { liveES().emit('ev', { id: 7, t: Date.now() / 1000, edge: 'telegram→me', from: 'telegram', to: 'me', kind: 'message', chip: 'text', actor: 'main', text: 'late one', meta: {} });
      snap('paused'); click('.live-head button:first-of-type'); },
    function () { snap('resumed'); click('.live-head button[aria-label="Close"]'); },
    function () { snap('closed'); document.querySelector('.live-all').click(); },
    function () { snap('reopened'); sel('kind', 'vault_call'); },
    function () { snap('nomatch'); click('.lf-reset'); },
    function () { snap('reset'); click('.lf-more'); },
    function () { snap('toggled'); location.hash = '#architecture&live=main-anthropic&st=err'; },
    function () { snap('hashnav'); document.documentElement.setAttribute('data-probe', JSON.stringify(out)); }
  ];
  var i = 0;
  function next() { if (i < steps.length) { try { steps[i++](); } catch (err) { out.push({ name: 'error', msg: String(err) }); i = steps.length; document.documentElement.setAttribute('data-probe', JSON.stringify(out)); return; } setTimeout(next, 500); } }
  setTimeout(next, 1500);
})();</script>"""


def live_probe(tmpdir, width, height):
    """The filter UI driven in a real layout over http (fake EventSource that replays six events)."""
    import http.server
    import threading
    d = os.path.join(tmpdir, 'live-probe')
    os.makedirs(d, exist_ok=True)
    page = open(PAGE).read()
    page = page.replace('<body>', '<body>' + LIVE_FAKE, 1)
    i = page.rindex('</body>')
    with open(os.path.join(d, 'index.html'), 'w') as f:
        f.write(page[:i] + LIVE_PROBE + page[i:])
    shutil.copy(os.path.join(WWW, 'data.json'), os.path.join(d, 'data.json'))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=d, **kw)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f'http://127.0.0.1:{srv.server_address[1]}/index.html#architecture&live=all&q=needle'
        dom = chrome([f'--window-size={width},{height}', '--dump-dom', url], budget=15000).stdout
    finally:
        srv.shutdown()
    m = re.search(r'data-probe="([^"]*)"', dom)
    return json.loads(m.group(1).replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')) if m else None


def test_live_filters(tmpdir):
    for width, height in ((390, 844), (1280, 900)):
        tag = f'live filters {width}px'
        r = live_probe(tmpdir, width, height)
        ok(bool(r) and r[-1].get('name') == 'hashnav', f'{tag}: probe ran all steps ({(r or [{}])[-1]})')
        if not r or r[-1].get('name') != 'hashnav':
            continue
        S = {x['name']: x for x in r}
        b = S['boot']
        ok(b['open'] and b['title'] == 'All traffic' and b['q'] == 'needle', f'{tag}: bookmarked hash opens the filtered all-traffic view')
        ok(b['n'] == 3 and b['marks'] >= 3 and b['count'] == '3 of 6 shown', f'{tag}: search keeps matching rows and highlights ({b["n"]}, {b["marks"]}, {b["count"]!r})')
        ok(b['openRows'] == 1, f'{tag}: a hit below the fold opens its row')
        ok(b['es'].startswith('live?edge=*') and 'q=' not in b['es'], f'{tag}: all traffic streams edge=*, the search stays client-side')
        e = S['errors']
        ok(e['n'] == 1 and e['errRows'] == 1 and e['pressed'] == 'true' and 'err=1' in e['es'], f'{tag}: errors only (client + flowd err=1)')
        ok('st=err' in e['hash'] and 'q=needle' in e['hash'] and '"st":"err"' in (e['store'] or ''), f'{tag}: filters in the hash and localStorage')
        ok(S['errors-noq']['n'] == 1 and S['errors-noq']['q'] == '' and 'q=' not in S['errors-noq']['hash'], f'{tag}: clear button empties the search')
        ok(S['kind']['n'] == 1 and 'kind=tool_call' in S['kind']['es'], f'{tag}: kind filter (client + flowd)')
        ok(S['dir']['n'] == 2 and 'dir=in' in S['dir']['es'], f'{tag}: direction filter')
        w = S['win']
        ok(w['n'] == 5 and 'since=' in w['es'] and 'win=300' in w['hash'], f'{tag}: time window drops the 2 h old event and asks flowd since=')
        ok(S['paused']['n'] == 5 and S['paused']['status'] == 'paused · 1 new', f'{tag}: pause holds new events ({S["paused"]["status"]!r})')
        ok(S['resumed']['n'] == 6, f'{tag}: resume shows the held event')
        ok(not S['closed']['open'] and S['closed']['hash'] == '#architecture', f'{tag}: close clears the hash')
        ro = S['reopened']
        ok(ro['open'] and ro['win'] == '300' and 'win=300' in ro['hash'] and ro['n'] == 5, f'{tag}: reopening restores the stored filters')
        ok(S['nomatch']['n'] == 0 and S['nomatch']['none'], f'{tag}: no match shows a reset hint')
        rs = S['reset']
        ok(rs['n'] == 6 and rs['hash'] == '#architecture&live=all' and rs['count'] == '6 events', f'{tag}: reset shows everything ({rs["hash"]!r})')
        ok(all(x['sw'] <= x['iw'] and x['pw'] <= 0 for x in r), f'{tag}: no horizontal scroll, page or panel')
        hn = S['hashnav']
        ok(hn['open'] and hn['title'] == 'Me ⇄ Anthropic API' and hn['pressed'] == 'true' and 'err=1' in hn['es'] and 'anthropic' in hn['es'],
           f'{tag}: a pasted bookmark (hashchange) opens that view with its filters')
        if width < 760:
            ok(not rs['grid'] and S['toggled']['grid'], f'{tag}: filter grid collapsed by default on phone, opens on tap')
            ok(abs(rs['sheet'] - 0.44 * rs['ih']) < 3 and rs['bar'] <= 56 and rs['list'] >= 150,
               f'{tag}: sheet 44vh, compact bar ({rs["bar"]} px), list {rs["list"]} px')
            ok(S['toggled']['sheet'] > rs['sheet'] and S['toggled']['list'] >= 120, f'{tag}: open filters make the sheet taller ({S["toggled"]["sheet"]} px)')
        else:
            ok(rs['grid'] and not S['toggled']['grid'], f'{tag}: filter grid open by default on desktop, collapses on click')
            ok(rs['sheet'] == rs['ih'] and rs['list'] >= 400, f'{tag}: full-height drawer, list {rs["list"]} px')


def main():
    m = load_module()
    tmp = tempfile.mkdtemp(prefix='dash-tests-')
    try:
        test_clean(m)
        test_assistant_helpers(m)
        test_goals(m)
        test_lastnight(m)
        test_fixture()
        test_split()
        test_template(m)
        test_vault_live()
        test_growth()
        if not os.path.exists(os.environ.get('DASH_DATAFLOW', os.path.join(HOME, '.config', 'dash', 'dataflow.json'))):
            print('skip: real run, privacy sweep of the real data, page and render: no live install '
                  '(~/.config/dash/dataflow.json missing)')
            print(f'{PASSES} passed, {len(FAILS)} failed')
            sys.exit(1 if FAILS else 0)
        d = test_real(tmp)
        r = subprocess.run([DASH, '--inline'], capture_output=True, text=True, timeout=180)
        ok(r.returncode == 0, 'dash-data --inline exits 0')
        test_privacy(d)
        test_log_privacy(load_module(), json.load(open(os.path.join(HOME, '.local', 'state', 'dash', 'data.json'))))
        test_page()
        if '--no-render' not in sys.argv:
            test_render(tmp)
            test_live_filters(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{PASSES} passed, {len(FAILS)} failed')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
