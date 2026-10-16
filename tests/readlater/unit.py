"""Unit checks of readlater internals (loaded as a module from ~/bin/readlater). Prints ok/FAIL lines like run.sh."""
import importlib.machinery, importlib.util, sys

loader = importlib.machinery.SourceFileLoader('rl', sys.argv[1])
spec = importlib.util.spec_from_loader('rl', loader)
rl = importlib.util.module_from_spec(spec)
loader.exec_module(rl)
fails = 0


def eq(name, got, want):
    global fails
    if got == want:
        print(f'ok   - {name}')
    else:
        fails += 1
        print(f'FAIL - {name}\n       got:  {got!r}\n       want: {want!r}')


print('# U. unit: URL normalisation')
n = rl.normalize_url
eq('youtu.be + si', n('https://youtu.be/jNQXAC9IVRw?si=abc&t=5'), 'https://www.youtube.com/watch?v=jNQXAC9IVRw')
eq('shorts', n('https://youtube.com/shorts/abcDEF12345?feature=share'), 'https://www.youtube.com/watch?v=abcDEF12345')
eq('m.youtube + extra params', n('https://m.youtube.com/watch?v=abcDEF12345&list=PL1&index=2'),
   'https://www.youtube.com/watch?v=abcDEF12345')
eq('live/', n('https://www.youtube.com/live/abcDEF12345?si=x'), 'https://www.youtube.com/watch?v=abcDEF12345')
eq('utm/fbclid stripped, fragment dropped, host lowercased', n('HTTPS://Example.COM/a/b?id=7&utm_source=x&fbclid=y#top'),
   'https://example.com/a/b?id=7')
eq('bare domain gets https', n('example.com/post'), 'https://example.com/post')
try:
    n('not a url at all')
    eq('garbage refused', 'accepted', 'Fail')
except rl.Fail as e:
    eq('garbage refused (code 2)', e.code, 2)

print('# U. unit: robots.txt (RFC 9309)')
g = rl.parse_robots("""User-agent: *
Disallow: /private/
Allow: /private/ok.html
Disallow: /*.pdf$

User-agent: claude-user
User-agent: OtherBot
Disallow: /noclaude
""")
ra = rl.robots_allowed
eq('* disallow prefix', ra(g, 'readlater', '/private/x.html'), False)
eq('longer Allow wins', ra(g, 'readlater', '/private/ok.html'), True)
eq('wildcard + $ anchor', ra(g, 'readlater', '/files/a.pdf'), False)
eq('$ anchor not matched with query', ra(g, 'readlater', '/files/a.pdf?x=1'), True)
eq('unlisted path allowed', ra(g, 'readlater', '/news/1'), True)
eq('specific group replaces * (case-insensitive, shared group)', ra(g, 'Claude-User', '/private/x.html'), True)
eq('specific group rule applies', ra(g, 'Claude-User', '/noclaude/page'), False)
g2 = rl.parse_robots('User-agent: *\nDisallow: /a\nAllow: /a\n')
eq('equal length: Allow wins the tie', ra(g2, 'readlater', '/a/b'), True)
eq('empty Disallow allows all', ra(rl.parse_robots('User-agent: *\nDisallow:\n'), 'readlater', '/x'), True)
eq('percent-encoding normalised', ra(rl.parse_robots('User-agent: *\nDisallow: /caf%C3%A9\n'), 'readlater', '/café/menu'),
   False)

print('# U. unit: captions')
vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>hello</c> there

00:00:03.000 --> 00:00:03.010
hello there

00:00:03.010 --> 00:00:05.000
hello there
general kenobi
"""
eq('vtt rolling dedupe + tags stripped', rl.parse_vtt(vtt), [(1.0, 'hello there'), (3.01, 'general kenobi')])
srt = "1\n00:00:02,500 --> 00:00:04,000\nFirst line\n\n2\n01:00:00,000 --> 01:00:02,000\nAn hour in &amp; on\n"
eq('srt parsed (hours, entities)', rl.parse_vtt(srt), [(2.5, 'First line'), (3600.0, 'An hour in & on')])
eq('json3 parsed, newline-only events skipped',
   rl.parse_json3({'events': [{'tStartMs': 1500, 'segs': [{'utf8': 'a '}, {'utf8': 'b'}]},
                              {'tStartMs': 2000, 'segs': [{'utf8': '\n'}]}, {'tStartMs': 3000}]}), [(1.5, 'a b')])
tp = rl.timed_paragraphs([(0, 'One.'), (30, 'Two.'), (61, 'Three.'), (62, '[Music]'), (63, '[Music]'), (130, 'Four.')],
                         [{'start_time': 0, 'title': 'Intro'}, {'start_time': 120, 'title': 'Outro'}])
eq('paragraphs: chapters, ~1/min, [Music] collapsed', tp,
   '## Intro [0:00]\n\n[0:00] One. Two. Three.\n\n[1:02] [Music]\n\n## Outro [2:00]\n\n[2:10] Four.')
eq('fmt_dur', (rl.fmt_dur(59), rl.fmt_dur(61), rl.fmt_dur(3725)), ('0:59', '1:01', '1:02:05'))
eq('parse_dur', (rl.parse_dur('4h'), rl.parse_dur('90m'), rl.parse_dur('1h30m'), rl.parse_dur('45')),
   (14400, 5400, 5400, 45))
sp = rl.split_paragraphs(' '.join(f'Sentence number {i} has six words.' for i in range(50)), 600, 600)
eq('long chunk split into ~150-word paragraphs', len(sp), 2)
eq('  first exact timestamp, then interpolated', (sp[0][:7], sp[1][:8]), ('[10:00]', '[~15:00]'))

print('# U. unit: caption choice')
f = [{'ext': 'json3'}]
info = {'language': 'en', 'subtitles': {'de': f, 'en-US': f, 'live_chat': f},
        'automatic_captions': {'en-orig': f, 'en': f, 'de': f, 'fr': f}}
eq('manual in video language first', rl.pick_subs(info)[0], ('en-US', 'manual'))
eq('--lang de prefers manual de', rl.pick_subs(info, 'de')[0], ('de', 'manual'))
eq('live_chat never a candidate', any(k == 'live_chat' for k, _ in rl.pick_subs(info)), False)
info2 = {'language': 'sr', 'subtitles': {}, 'automatic_captions': {'sr-orig': f, 'sr': f, 'en': f, 'de': f}}
eq('auto: original-language track first', rl.pick_subs(info2)[0], ('sr-orig', 'auto'))
eq('no language info: -orig, then en', rl.pick_subs({'automatic_captions': {'af': f, 'en': f}})[0], ('en', 'auto'))
eq('nothing at all', rl.pick_subs({}), [])

print('# U. unit: paywall heuristics')
eq('paywall: JSON-LD false is enough', bool(rl.paywall_check('"isAccessibleForFree": false', 5000)), True)
eq('paywall: weak marker + long text = not paywalled', rl.paywall_check('<div class="paywall-promo">', 2000), None)
eq('paywall: weak marker + short text', bool(rl.paywall_check('Jetzt abonnieren und weiterlesen', 120)), True)
eq('audio: og:audio is strong', rl.find_audio('<meta property="og:audio" content="/e.mp3">', 'https://x.org/p/'),
   ('https://x.org/e.mp3', True))
eq('audio: <audio> is weak', rl.find_audio('<audio src="a.ogg"></audio>', 'https://x.org/p/'),
   ('https://x.org/p/a.ogg', False))

sys.exit(1 if fails else 0)
