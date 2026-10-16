"""Offline tests for ~/bin/container-watch (weekly container security check, cron Wed 19:13).

Never touches the network, `ssh unraid`, the real state/cache/inbox or Telegram:
- HOME points at a temp sandbox before the script is loaded; every run gets its own state/cache/inbox dirs.
- GitHub releases/advisories, NVD, Ubiquiti and Docker Hub are served from fixtures/up/<layer>/ (raw API JSON)
  by FakeNet, patched over urllib.request.urlopen. Unknown hosts are recorded and refused.
- `ssh`, `unraid-ssh` and `tg` are fakes in a temp bin dir that is first on PATH; CONTAINER_WATCH_TG and
  UNRAID_SSH_BIN point at them too. The fake tg only writes the message to a file.
- The Cron tests run the real cron arguments (parsed from ~/crontab) in a subprocess through boot.py, which
  blocks every socket and fixes the clock.
"""
import atexit, contextlib, datetime as dt, email.message, fcntl, hashlib, importlib.machinery, importlib.util
import io, json, os, re, shutil, stat, subprocess, sys, tempfile, unittest, urllib.error, urllib.parse, warnings
from unittest import mock

# Synthetic profile (tests never read the real one): repo layout tests/profile, live layout ~/opt/profile-tests.
for _pf in ('profile', 'profile-tests'):
    _pf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _pf, 'fixtures', 'profile.json')
    if os.path.isfile(_pf):
        os.environ['ASSISTANT_PROFILE'] = _pf
        break

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, 'fixtures')
INV = os.path.join(FX, 'inventory.txt')
BOOT = os.path.join(HERE, 'boot.py')
BIN = os.environ.get('CW_BIN') or os.path.expanduser('~/bin/container-watch')   # CW_BIN=<copy> tests a copy
CRONTAB = os.path.expanduser('~/crontab')
NOW = dt.datetime(2026, 10, 14, 17, 13, tzinfo=dt.timezone.utc)        # Wednesday 19:13 Zurich

# ---------------------------------------------------------------- sandbox: set up BEFORE loading the script
SANDBOX = tempfile.mkdtemp(prefix='cw-test-')
atexit.register(shutil.rmtree, SANDBOX, True)
FAKEBIN = os.path.join(SANDBOX, 'fakebin')
os.makedirs(FAKEBIN)
FAKES = {
    'tg': r'''#!/usr/bin/env bash
# fake tg (container-watch tests): records the message, never sends anything
d="${CW_TG_DIR:?fake tg: CW_TG_DIR unset}"; mkdir -p "$d"
printf '%s' "$1" > "$d/$(date +%s%N).txt"
exit "${CW_TG_EXIT:-0}"
''',
    'ssh': r'''#!/usr/bin/env bash
# fake ssh: `ssh -G host` (host IP lookup) and `ssh ... host bash -s` (collector on stdin)
echo "ssh $*" >> "${CW_SSH_LOG:-/dev/null}"
if [ "$1" = "-G" ]; then echo "hostname 127.0.0.9"; echo "user root"; exit 0; fi
stdin=$(cat)
case "$stdin" in *"@@containers"*) ;; *) echo "fake ssh: no collector on stdin" >&2; exit 97;; esac
printf '%s' "$stdin" > "${CW_SSH_STDIN:-/dev/null}"
if [ -n "${CW_SSH_FAIL:-}" ]; then echo "ssh: connect to host unraid port 22: Connection refused" >&2; exit 255; fi
cat "${CW_INVENTORY:?}"
''',
    'unraid-ssh': r'''#!/usr/bin/env bash
# fake ~/bin/unraid-ssh: `mode` and `--host H run containers`
echo "unraid-ssh $*" >> "${CW_SSH_LOG:-/dev/null}"
if [ "$1" = "mode" ]; then echo "${CW_LOCKDOWN_MODE:-root}"; exit 0; fi
[ "$3 $4" = "run containers" ] || { echo "fake unraid-ssh: unexpected $*" >&2; exit 98; }
[ -n "${CW_SSH_FAIL:-}" ] && exit 255
cat "${CW_INVENTORY:?}"
''',
}
for _n, _body in FAKES.items():
    _p = os.path.join(FAKEBIN, _n)
    with open(_p, 'w') as _f:
        _f.write(_body)
    os.chmod(_p, 0o755)
FAKE_TG = os.path.join(FAKEBIN, 'tg')
os.environ['HOME'] = os.path.join(SANDBOX, 'home')
os.makedirs(os.environ['HOME'])
os.environ['PATH'] = FAKEBIN + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin')
os.environ['CONTAINER_WATCH_TG'] = FAKE_TG
os.environ['UNRAID_SSH_BIN'] = os.path.join(FAKEBIN, 'unraid-ssh')
os.environ.pop('CONTAINER_WATCH_HOST', None)

_ld = importlib.machinery.SourceFileLoader('cw', BIN)
_sp = importlib.util.spec_from_loader('cw', _ld)
CW = importlib.util.module_from_spec(_sp)
_ld.exec_module(CW)
assert CW.TG_CMD == FAKE_TG and shutil.which('tg') == FAKE_TG, 'refusing to run: tg is not the fake'
assert str(CW.HOME).startswith(SANDBOX), 'refusing to run: HOME is not the sandbox'


# ---------------------------------------------------------------- fake network
def _hdrs(d):
    m = email.message.Message()
    for k, v in (d or {}).items():
        m[k] = v
    return m


class FakeResp:
    def __init__(self, body, headers):
        self.body, self.headers = body, _hdrs(headers)

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """urlopen stand-in. layers: fixture dirs under fixtures/up, first match wins. mode: None, 'down',
    'ratelimit' (GitHub 403 + X-RateLimit-Remaining: 0), '429', '304' (GitHub answers If-None-Match with 304),
    'nvd-down' (NVD 503)."""

    def __init__(self, *layers, mode=None):
        self.layers = [os.path.join(FX, 'up', l) for l in (layers or ('base',))]
        self.mode, self.calls, self.unexpected = mode, [], []

    def _body(self, rel, default):
        for d in self.layers:
            p = os.path.join(d, rel)
            if os.path.exists(p):
                with open(p, 'rb') as f:
                    return f.read()
        return json.dumps(default).encode()

    def host_calls(self, host):
        return [u for u, _ in self.calls if urllib.parse.urlparse(u).netloc == host]

    def __call__(self, req, timeout=None, context=None):
        url = req.full_url
        self.calls.append((url, req))
        u = urllib.parse.urlparse(url)
        if self.mode == 'down':
            raise urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused'))
        headers = {}
        if u.netloc == 'api.github.com':
            if self.mode == 'ratelimit':
                raise urllib.error.HTTPError(url, 403, 'rate limit exceeded',
                                             _hdrs({'X-RateLimit-Remaining': '0'}), io.BytesIO(b'{}'))
            if self.mode == '429':
                raise urllib.error.HTTPError(url, 429, 'Too Many Requests', _hdrs({}), io.BytesIO(b''))
            if self.mode == '304' and req.has_header('If-none-match'):
                raise urllib.error.HTTPError(url, 304, 'Not Modified', _hdrs({'X-RateLimit-Remaining': '58'}),
                                             io.BytesIO(b''))
            m = re.fullmatch(r'/repos/([^/]+)/([^/]+)/(releases|security-advisories)', u.path)
            kind = 'releases' if m.group(3) == 'releases' else 'advisories'
            body = self._body(f'gh/{m.group(1)}__{m.group(2)}.{kind}.json', [])
            headers = {'ETag': '"' + hashlib.sha1(body).hexdigest()[:12] + '"', 'X-RateLimit-Remaining': '59'}
        elif u.netloc == 'services.nvd.nist.gov':
            if self.mode == 'nvd-down':
                raise urllib.error.HTTPError(url, 503, 'Service Unavailable', _hdrs({}), io.BytesIO(b''))
            cpe = urllib.parse.parse_qs(u.query)['virtualMatchString'][0].split(':')
            body = self._body(f'nvd/{cpe[3]}__{cpe[4]}.json', {'vulnerabilities': []})
        elif u.netloc == 'fw-update.ubnt.com':
            body = self._body('ubiquiti.json', {'_embedded': {'firmware': []}})
        elif u.netloc == 'hub.docker.com':
            ns, repo = re.fullmatch(r'/v2/repositories/([^/]+)/([^/]+)/tags', u.path).groups()
            body = self._body(f'hub/{ns}__{repo}.json', {'results': []})
        else:
            self.unexpected.append(url)
            raise urllib.error.URLError('unexpected host in test: ' + u.netloc)
        return FakeResp(body, headers)


class FakeUp:
    """Upstream stand-in for evaluate() unit tests (already-trimmed data)."""

    def __init__(self, releases=None, advisories=None, nvd=None, ubi=(None, None), hub=None):
        self.r, self.a, self.n, self.ubi, self.h = releases or {}, advisories or {}, nvd or {}, ubi, hub or {}

    def releases(self, repo):
        return self.r.get(repo)

    def advisories(self, repo):
        return self.a.get(repo, [])

    def nvd(self, vp, version):
        return self.n.get(vp, [])

    def ubiquiti_latest(self):
        return self.ubi

    def hub_tags(self, repo):
        return self.h.get(repo, [])


# ---------------------------------------------------------------- helpers
def rel(tag, pub, body='', pre=False):
    return dict(tag=tag, name=tag, published=pub, pre=pre, draft=False, body=body,
                url=f'https://github.com/x/y/releases/tag/{tag}')


def adv(gid, sev, rng, patched, pub='2026-09-01T00:00:00Z', cve=None, summary='Some bug', withdrawn=None):
    return dict(id=gid, cve=cve, summary=summary, severity=sev, score=None, published=pub, withdrawn=withdrawn,
                state='published', url=f'https://github.com/x/y/security/advisories/{gid}',
                vulns=[dict(pkg='y', range=rng, patched=patched)])


def mini_inv(image='lscr.io/linuxserver/radarr:latest', version='5.8.3.8933-ls230', name='radarr', status=None,
             env=None):
    c = dict(name='/' + name, image=image, imageId='sha256:x', state='running', startedAt='', created='',
             labels={}, net='bridge', ports={}, ips='')
    i = dict(id='sha256:x', tags=[image], digests=[], created='2026-09-01T00:00:00Z',
             labels={'org.opencontainers.image.version': version} if version else {})
    st = {CW.unraid_status_key(image): {'status': status}} if status else {}
    lines = ['@@meta', '@@containers', json.dumps(c), '@@env']
    lines += [f'/{name}\t{k}={v}' for k, v in (env or {}).items()]
    lines += ['@@images', json.dumps(i), '@@history', '@@unraid-status', json.dumps(st), '@@autoupdate', '{}',
              '@@mdns', '@@end']
    return CW.build_inventory('\n'.join(lines))


RADARR_RELS = [rel('v5.8.3.8933', '2026-08-30T10:00:00Z'), rel('v5.9.1.9070', '2026-09-20T10:00:00Z'),
               rel('v5.10.0.9100', '2026-10-13T10:00:00Z')]


def evaluate(advs=(), rels=None, grace=3, state=None, **inv_kw):
    up = FakeUp(releases={'Radarr/Radarr': RADARR_RELS if rels is None else rels},
                advisories={'Radarr/Radarr': list(advs)})
    st = {} if state is None else state
    rows, items = CW.evaluate(mini_inv(**inv_kw), CW.Local(False), up, NOW, grace, st)
    return rows[0], items, st


class Base(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter('ignore', ResourceWarning)      # main() leaves its lock file to the GC
        self.d = tempfile.mkdtemp(prefix='t-', dir=SANDBOX)
        self.state, self.cache, self.inbox, self.tgdir = (os.path.join(self.d, x) for x in
                                                          ('state', 'cache', 'inbox', 'tg'))
        self.sshlog = os.path.join(self.d, 'ssh.log')
        self.env = dict(CW_TG_DIR=self.tgdir, CW_TG_EXIT='0', CW_SSH_LOG=self.sshlog, CW_INVENTORY=INV,
                        CW_SSH_STDIN=os.path.join(self.d, 'ssh-stdin.txt'))
        self._envp = mock.patch.dict(os.environ, self.env)
        self._envp.start()
        self.assertEqual(CW.TG_CMD, FAKE_TG)

    def tearDown(self):
        self._envp.stop()

    def run_main(self, *args, net=None, input_file=INV, now=NOW, state=None, cache=None, inbox=None):
        argv = ['container-watch', '--state-dir', state or self.state, '--cache-dir', cache or self.cache,
                '--inbox-dir', inbox or self.inbox] + (['--input', input_file] if input_file else []) + list(args)
        self.net = net or FakeNet('base')
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch('urllib.request.urlopen', self.net), \
                mock.patch.object(CW, 'now_utc', return_value=now), mock.patch('time.sleep') as self.sleep, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = CW.main()
        self.assertEqual(self.net.unexpected, [], 'contacted an unexpected host')
        return rc, out.getvalue(), err.getvalue()

    def sent(self):
        if not os.path.isdir(self.tgdir):
            return []
        out = []
        for n in sorted(os.listdir(self.tgdir)):
            with open(os.path.join(self.tgdir, n)) as f:
                out.append(f.read())
        return out

    def _inbox(self):
        return sorted(os.listdir(self.inbox)) if os.path.isdir(self.inbox) else []

    def notes(self):
        return [n for n in self._inbox() if n.startswith('container-watch-') and 'errors' not in n]

    def error_notes(self):
        return [n for n in self._inbox() if n.startswith('container-watch-errors-')]

    def statej(self):
        with open(os.path.join(self.state, 'state.json')) as f:
            return json.load(f)


# ================================================================ version parsing and comparison
class Versions(unittest.TestCase):
    def test_vparse_vclean(self):
        self.assertEqual(CW.vparse('v4.0.20.3014-ls326'), (4, 0, 20, 3014))
        self.assertEqual(CW.vclean('v4.0.20.3014-ls326'), '4.0.20.3014')
        self.assertEqual(CW.vclean('5.21.1.9799-ls123'), '5.21.1.9799')
        self.assertEqual(CW.vparse('version-5.21.1.9799'), (5, 21, 1, 9799))
        self.assertEqual(CW.vclean('5.1.3-1-01'), '5.1.3')                     # binhex RELEASETAG
        self.assertEqual(CW.vclean('9.0.114-unraid'), '9.0.114')
        self.assertEqual(CW.vparse('2026.9.4'), (2026, 9, 4))
        self.assertIsNone(CW.vparse('latest'))
        self.assertIsNone(CW.vparse(None))
        self.assertEqual(CW.vclean('latest'), 'latest')

    def test_vcmp_numeric_not_lexical(self):
        v = lambda a, b: CW.vcmp(CW.vparse(a), CW.vparse(b))
        self.assertEqual(v('1.10.0', '1.9.0'), 1)
        self.assertEqual(v('4.0.20.3014', '4.0.20'), 1)
        self.assertEqual(v('1.0', '1.0.0'), 0)
        self.assertEqual(v('v5.10.0.9100', '5.8.3.8933-ls230'), 1)

    def test_date_tags(self):
        v = lambda a, b: CW.vcmp(CW.vparse(a), CW.vparse(b))
        self.assertEqual(v('2026.10.0', '2026.9.4'), 1)                         # Home Assistant
        self.assertEqual(v('2025.12.3', '2026.1.0'), -1)
        self.assertEqual(v('26.10.1', '26.09.2'), 1)                            # jlesage vYY.MM.N
        self.assertTrue(CW.is_pre('2026.10.0b3'))
        self.assertFalse(CW.is_pre('2026.10.0'))

    def test_vkey_prerelease_and_build_suffixes(self):
        k = CW.vkey
        self.assertEqual(CW.kcmp(k('1.6.2-beta.3'), k('1.6.2')), -1)
        self.assertEqual(CW.kcmp(k('1.6.2'), k('1.6.3')), -1)
        self.assertEqual(CW.kcmp(k('1.6.2-rc1'), k('1.6.2')), -1)
        self.assertEqual(CW.kcmp(k('1.6.2-beta.2'), k('1.6.2-beta.10')), -1)
        for build in ('5.1.3-ls326', '5.1.3-r0', '5.1.3-1-01', 'v5.1.3', '5.1.3-alpine', '5.1.3-unraid'):
            self.assertEqual(CW.kcmp(k(build), k('5.1.3')), 0, build)
            self.assertFalse(CW.is_pre(build), build)
        self.assertIsNone(k('latest'))

    def test_stable_releases_filter(self):
        rels = [rel('v2.0.0', '2026-01-01'), rel('v2.1.0-beta.1', '2026-01-02'), rel('v2.1.0-rc.2', '2026-01-03'),
                rel('4.3.3RC1', '2026-01-04'), rel('2026.10.0b3', '2026-01-05'), rel('nightly-2026', '2026-01-06'),
                rel('v2.0.1', '2026-01-07', pre=True), dict(rel('v2.0.2', '2026-01-08'), draft=True),
                rel('2026082801', '2026-01-09'), rel('v2.0.3-ls12', '2026-01-10')]
        self.assertEqual([r['tag'] for r in CW.stable_releases(rels)], ['v2.0.0', 'v2.0.3-ls12'])

    def test_image_repo_tag_and_unraid_key(self):
        self.assertEqual(CW.image_repo_tag('lscr.io/linuxserver/radarr:latest'), ('lscr.io', 'linuxserver/radarr', 'latest'))
        self.assertEqual(CW.image_repo_tag('redis:7.2.4'), ('', 'redis', '7.2.4'))
        self.assertEqual(CW.image_repo_tag('docker.io/binhex/arch-sabnzbd'), ('', 'binhex/arch-sabnzbd', 'latest'))
        self.assertEqual(CW.image_repo_tag('localhost:5000/foo:1.0'), ('localhost:5000', 'foo', '1.0'))
        self.assertEqual(CW.image_repo_tag('ghcr.io/x/y:1.2@sha256:abc'), ('ghcr.io', 'x/y', '1.2'))
        self.assertEqual(CW.unraid_status_key('redis:7.2.4'), 'library/redis:7.2.4')
        self.assertEqual(CW.unraid_status_key('linuxserver/sonarr'), 'linuxserver/sonarr:latest')
        self.assertEqual(CW.unraid_status_key('lscr.io/linuxserver/radarr'), 'lscr.io/linuxserver/radarr:latest')

    def test_parse_iso(self):
        self.assertEqual(CW.parse_iso('2026-10-01T02:00:34.88731859Z'),
                         dt.datetime(2026, 10, 1, 2, 0, 34, 887318, tzinfo=dt.timezone.utc))
        self.assertEqual(CW.parse_iso('2026-10-01T04:00:00+0200'), dt.datetime(2026, 10, 1, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(CW.parse_iso(0), dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc))
        self.assertIsNone(CW.parse_iso('soon'))
        self.assertIsNone(CW.parse_iso(''))

    def test_installed_version_sources(self):
        inv = CW.build_inventory(open(INV).read())
        by = {c['name']: c for c in inv['containers']}
        local = CW.Local(False)
        app = lambda key: next(a for a in CW.APPS if a['key'] == key)
        self.assertEqual(CW.installed_version(app('radarr'), by['radarr'], inv, local), ('5.8.3.8933', 'label'))
        self.assertEqual(CW.installed_version(app('sabnzbd'), by['binhex-sabnzbd'], inv, local), ('4.3.2', 'history'))
        self.assertEqual(CW.installed_version(app('homeassistant'), by['HomeAssistant_inabox'], inv, local),
                         ('2026.9.4', 'mdns:ha'))
        self.assertEqual(CW.installed_version(app('redis'), by['paperless_redis'], inv, local),
                         ('7.2.4', 'env:REDIS_VERSION'))
        self.assertEqual(CW.installed_version(app('emby'), by['EmbyServer'], inv, local), (None, None))
        # HA's vpat rejects a non-calendar version even if a label has one
        c = dict(by['HomeAssistant_inabox'], img={'labels': {'org.opencontainers.image.version': '1.2.3'}})
        self.assertEqual(CW.installed_version(app('homeassistant'), c, dict(inv, mdns=[]), local), (None, None))
        self.assertEqual(CW.label_version({'labels': {'build_version': 'Linuxserver.io version:- 1.6.2-ls99 Build-date:- x'}}),
                         '1.6.2-ls99')

    def test_inventory_parsing(self):
        inv = CW.build_inventory(open(INV).read())
        names = [c['name'] for c in inv['containers']]
        self.assertIn('claude-bot', names)
        self.assertEqual(inv['mdns'][0]['version'], '2026.9.4')
        self.assertEqual(inv['mdns'][0]['ip'], '198.51.100.50')
        self.assertEqual(inv['unraid_status']['lscr.io/linuxserver/radarr:latest']['status'], 'false')
        self.assertTrue(inv['autoupdate']['containers']['radarr']['update'])
        self.assertEqual(inv['meta']['now'], '2026-10-14T17:13:00Z')
        self.assertEqual(next(c for c in inv['containers'] if c['name'] == 'unifi-controller')['env'],
                         {'APP_VERSION': '9.0.114'})


# ================================================================ advisory matching
class Ranges(unittest.TestCase):
    def test_parse_range(self):
        k = CW.vkey
        self.assertEqual(CW.parse_range('< 1.2.3'), [[('<', k('1.2.3'))]])
        self.assertEqual(CW.parse_range('>= 1.0.0, < 1.2.3'), [[('>=', k('1.0.0')), ('<', k('1.2.3'))]])
        self.assertEqual(CW.parse_range('>=1.0.0 <2.0.0'), [[('>=', k('1.0.0')), ('<', k('2.0.0'))]])
        self.assertEqual(CW.parse_range('1.0.0 - 2.0.0'), [[('>=', k('1.0.0')), ('<=', k('2.0.0'))]])
        self.assertEqual(len(CW.parse_range('< 2.0 || >= 3.0, < 3.1.2')), 2)
        self.assertEqual(CW.parse_range('= 1.4.0'), [[('=', k('1.4.0'))]])
        for bad in ('All', 'TBD', 'before 2024-01-01', '', None):
            self.assertIsNone(CW.parse_range(bad), bad)

    def test_in_range_bounds(self):
        r = CW.parse_range('>= 1.0.0, < 1.2.3')
        for v, want in (('0.9.9', False), ('1.0.0', True), ('1.2.2', True), ('1.2.3', False), ('1.2.3-beta.1', True)):
            self.assertEqual(CW.in_range(CW.vkey(v), r), want, v)
        r = CW.parse_range('<= 1.2.3')
        self.assertTrue(CW.in_range(CW.vkey('1.2.3-ls12'), r))
        self.assertFalse(CW.in_range(CW.vkey('1.2.4'), r))
        self.assertTrue(CW.upper_bounded(r))
        self.assertFalse(CW.upper_bounded(CW.parse_range('>= 7.0.0')))

    def test_is_patched_per_branch(self):
        p = ['6.2.16', '7.2.6', '7.4.1']
        for v, want in (('7.2.7', True), ('7.2.5', False), ('6.2.16', True), ('7.3.0', False), ('8.0.0', True)):
            self.assertEqual(CW.is_patched(CW.vkey(v), p), want, v)
        self.assertFalse(CW.is_patched(CW.vkey('1.0'), []))

    def g(self, installed, rng, patched, **kw):
        return CW.ghsa_affects(installed, dict(vulns=[dict(range=rng, patched=patched)],
                                               published=kw.pop('pub', '2026-09-01T00:00:00Z')), **kw)

    def test_affected_with_fix(self):
        self.assertEqual(self.g('1.2.0', '< 1.2.3', '1.2.3'), (True, '1.2.3', ''))
        self.assertEqual(self.g('5.8.3.8933', '< 5.10.0.9100', '5.10.0.9100')[:2], (True, '5.10.0.9100'))
        self.assertEqual(self.g('7.2.4', '>= 7.0.0, < 7.2.6', '6.2.16, 7.2.6, 7.4.1')[:2], (True, '7.2.6'))
        self.assertEqual(self.g('6.2.10', '< 6.2.16 || >= 7.0.0, < 7.2.6', '6.2.16, 7.2.6')[:2], (True, '6.2.16'))

    def test_fixed_and_unaffected(self):
        self.assertFalse(self.g('1.2.3', '< 1.2.3', '1.2.3')[0])                      # exactly the fix
        self.assertFalse(self.g('1.2.3-ls40', '< 1.2.3', '1.2.3')[0])                 # linuxserver build of the fix
        self.assertFalse(self.g('0.9.0', '>= 1.0.0, < 1.2.3', '1.2.3')[0])            # below the range
        self.assertFalse(self.g('7.2.7', '>= 7.0.0', '7.2.6, 7.4.1')[0])              # open range, own-branch fix
        self.assertFalse(self.g('2.0.0', 'All', '1.9.9')[0])                          # unparseable range, patched

    def test_unparseable_or_open_ended(self):
        self.assertTrue(self.g('1.0.0', 'All', '1.9.9')[0])
        self.assertIsNone(self.g('1.0.0', 'All', '')[0])
        self.assertIsNone(self.g('1.0.0', '>= 0.5.0', '', installed_date='2026-08-01T00:00:00Z')[0])
        hit, fix, note = self.g('1.0.0', '>= 0.5.0', '', installed_date='2026-10-01T00:00:00Z')
        self.assertIs(hit, False)
        self.assertIn('assumed fixed', note)
        self.assertEqual(CW.ghsa_affects('1.0.0', dict(vulns=[], published=None))[0], None)

    def test_fix_only_in_prerelease(self):
        hit, fix, note = self.g('1.9.0', '< 2.0.0-beta.3', '2.0.0-beta.3', stable_versions=['1.9.0', '2.0.0', '2.0.1'])
        self.assertEqual((hit, fix), (True, '2.0.0'))                                  # first stable after the beta
        hit, fix, note = self.g('1.9.0', '< 2.0.0-beta.3', '2.0.0-beta.3', stable_versions=['1.9.0'])
        self.assertEqual((hit, fix), (True, None))
        self.assertIn('fix only in pre-release 2.0.0-beta.3', note)

    def test_nvd_affects(self):
        cpe = 'cpe:2.3:a:ui:unifi_network_application:*:*:*:*:*:*:*:*'
        vp = 'ui:unifi_network_application'
        n = lambda v, **m: CW.nvd_affects(v, dict(matches=[dict(criteria=m.pop('criteria', cpe), **m)]), vp)
        self.assertEqual(n('9.0.114', versionEndExcluding='9.0.118'), (True, '9.0.118'))
        self.assertEqual(n('9.0.118', versionEndExcluding='9.0.118'), (False, None))
        self.assertEqual(n('9.0.118', versionEndIncluding='9.0.118'), (True, None))
        self.assertEqual(n('7.9.0', versionStartIncluding='8.0.0', versionEndExcluding='9.0.118'), (False, None))
        self.assertEqual(n('8.0.0', versionStartExcluding='8.0.0'), (False, None))
        self.assertEqual(n('9.0.114', criteria='cpe:2.3:a:ui:unifi_network_application:9.0.114:*:*:*:*:*:*:*'),
                         (True, None))
        self.assertEqual(n('9.0.115', criteria='cpe:2.3:a:ui:unifi_network_application:9.0.114:*:*:*:*:*:*:*'),
                         (False, None))
        self.assertEqual(n('1.0.0', criteria='cpe:2.3:a:ui:unifi_protect:*:*:*:*:*:*:*:*'), (False, None))
        self.assertEqual(CW.nvd_short('An attacker could exploit a remote code execution vulnerability.'),
                         'remote code execution')

    def test_sec_snippet(self):
        self.assertIn('CVE-2026-1', CW.sec_snippet('- New: x\n- Fixed: **auth** bypass (CVE-2026-1234)'))
        self.assertIsNotNone(CW.sec_snippet('Security: fix an issue in the API key check'))
        self.assertIsNone(CW.sec_snippet('- Added SECURITY.md\n- security-opt docs'))
        self.assertIsNone(CW.sec_snippet('- New: calendar view'))


# ================================================================ evaluate(): which findings become alerts
class Evaluate(unittest.TestCase):
    def test_severity_and_grace(self):
        _, items, _ = evaluate([adv('GHSA-low', 'low', '< 5.9.1.9070', '5.9.1.9070')])
        self.assertEqual(items, [])                                                    # LOW never alerts
        row, items, _ = evaluate([adv('GHSA-med', 'moderate', '< 5.9.1.9070', '5.9.1.9070', cve='CVE-2026-1')])
        self.assertEqual([(i['kind'], i['key'], i['fixed'], i['ids']) for i in items],
                         [('advisory', 'adv:radarr:GHSA-med', '5.9.1.9070', ['CVE-2026-1'])])
        self.assertEqual(row['installed'], '5.8.3.8933')
        fresh = adv('GHSA-new', 'high', '< 5.10.0.9100', '5.10.0.9100')               # fix released 1 day ago
        self.assertEqual(evaluate([fresh])[1], [])
        self.assertEqual(len(evaluate([fresh], grace=0)[1]), 1)

    def test_unfixed_only_high(self):
        hi = adv('GHSA-hi', 'critical', '<= 5.10.0.9100', '')
        self.assertEqual([i['kind'] for i in evaluate([hi])[1]], ['unfixed'])
        self.assertEqual(evaluate([adv('GHSA-md', 'medium', '<= 5.10.0.9100', '')])[1], [])

    def test_unknown_withdrawn_unaffected_stay_silent(self):
        advs = [adv('GHSA-open', 'critical', '>= 5.0.0', '', pub='2026-10-10T00:00:00Z'),
                adv('GHSA-gone', 'critical', '< 9.0.0', '9.0.0', withdrawn='2026-10-01T00:00:00Z'),
                adv('GHSA-old', 'high', '< 5.0.0', '5.0.0'),
                adv('GHSA-same', 'high', '>= 5.0.0', '', pub='2026-08-01T00:00:00Z')]
        row, items, _ = evaluate(advs)
        self.assertEqual(items, [])
        self.assertEqual(row['unknown_range'], ['GHSA-open'])
        self.assertEqual(row['assumed_fixed'], ['GHSA-same'])
        self.assertEqual(row['advisories_total'], 3)

    def test_security_release(self):
        rels = RADARR_RELS[:1] + [rel('v5.9.1.9070', '2026-09-20T10:00:00Z', '- Fixed: XSS (CVE-2026-1234)'),
                                  rel('v5.9.2.9080', '2026-10-05T10:00:00Z', '## Security\n- hardening'),
                                  rel('v5.10.0.9100', '2026-10-13T10:00:00Z', 'security fix')]
        row, items, _ = evaluate(rels=rels)
        self.assertEqual(len(items), 1)
        i = items[0]
        self.assertEqual((i['kind'], i['key'], i['count'], i['fixed']),
                         ('security-release', 'rel:radarr:5.9.2.9080', 2, '5.9.2.9080'))
        self.assertIn('2 newer releases mention security fixes (5.9.1.9070 … 5.9.2.9080)', i['why'])
        self.assertEqual(len(row['newer_security']), 3)                                # the 1-day-old one waits
        quiet = RADARR_RELS[:1] + [rel('v5.9.1.9070', '2026-09-20T10:00:00Z', '- Added SECURITY.md')]
        self.assertEqual(evaluate(rels=quiet)[1], [])

    def test_stale_update(self):
        key = 'lscr.io/linuxserver/radarr:latest'
        rels = RADARR_RELS[:1] + [rel(f'v5.9.{n}.9000', '2026-09-0%dT00:00:00Z' % n) for n in (1, 2, 3)]
        st = {'update_avail_since': {key: '2026-09-01T00:00:00+00:00'}}
        _, items, _ = evaluate(rels=rels, status='false', state=st)
        self.assertEqual([(i['kind'], i['key']) for i in items], [('stale', 'stale:radarr:2026-09-01')])
        st = {'update_avail_since': {key: '2026-09-20T00:00:00+00:00'}}
        self.assertEqual(evaluate(rels=rels, status='false', state=st)[1], [])        # < 30 days
        _, _, st = evaluate(rels=rels, status=None, state={})
        self.assertEqual(st['update_avail_since'], {})
        _, _, st = evaluate(rels=rels, status='false', state={})
        self.assertEqual(st['update_avail_since'], {key: NOW.isoformat(timespec='seconds')})
        _, _, st = evaluate(rels=rels, status='true', state=st)
        self.assertEqual(st['update_avail_since'], {})

    def test_self_and_unknown_containers_not_judged(self):
        inv = CW.build_inventory(open(INV).read())
        up = FakeUp(releases={}, advisories={})
        rows, items = CW.evaluate(inv, CW.Local(False), up, NOW, 3, {})
        names = [r['container'] for r in rows]
        self.assertNotIn('claude-bot', names)
        self.assertIsNone(next(r for r in rows if r['container'] == 'tdarr')['app'])
        self.assertIn('installed version unknown', next(r for r in rows if r['container'] == 'EmbyServer')['notes'])

    def test_ghsa_and_nvd_same_cve_counted_once(self):
        up = FakeUp(releases={'transmission/transmission': [rel('4.0.5', '2026-01-01T00:00:00Z')]},
                    advisories={'transmission/transmission': [adv('GHSA-t1', 'high', '< 4.0.6', '4.0.6',
                                                                   cve='CVE-2026-7')]},
                    nvd={'transmissionbt:transmission': [dict(
                        id='CVE-2026-7', published='2026-09-01T00:00:00', status='Analyzed', severity='high',
                        score=8.0, desc='x', matches=[dict(criteria='cpe:2.3:a:transmissionbt:transmission:*:*:*:*:*:*:*:*',
                                                           versionEndExcluding='4.0.6')])]})
        inv = mini_inv(image='linuxserver/transmission', version='4.0.5-r0-ls200', name='transmission')
        rows, items = CW.evaluate(inv, CW.Local(False), up, NOW, 3, {})
        self.assertEqual(len(rows[0]['affecting']), 1)
        self.assertEqual(len(items), 1)

    def test_pinned_hub_tag_same_flavour(self):
        up = FakeUp(releases={'redis/redis': []}, hub={'redis': [
            {'name': n} for n in ('latest', '8.0.0', '7.4.1-alpine', '7.4.2', '7.2.6-alpine')]})
        inv = mini_inv(image='redis:7.2.4-alpine', version=None, name='redis', env={'REDIS_VERSION': '7.2.4'})
        rows, _ = CW.evaluate(inv, CW.Local(False), up, NOW, 3, {})
        self.assertTrue(rows[0]['pinned'])
        self.assertEqual(rows[0]['newest_tag'], '7.4.1-alpine')


# ================================================================ bugs found while writing these tests
class Bugs(unittest.TestCase):
    def test_prerelease_kinds_are_ordered(self):
        """BUG 1 (fixed 2026-10-14): vkey ranked every pre-release by its number only, so 2.0.0-alpha.5 sorted
        above 2.0.0-beta.1 and an installed 2.0.0-beta.3 looked newer than 2.0.0-rc.1, so it was 'not affected'
        by an advisory '< 2.0.0-rc.1'."""
        order = ['2.0.0.dev1', '2.0.0-alpha.5', '2.0.0-beta.1', '2.0.0-rc.1', '2.0.0']
        for a, b in zip(order, order[1:]):
            self.assertEqual(CW.kcmp(CW.vkey(a), CW.vkey(b)), -1, f'{a} < {b}')
        self.assertEqual(CW.kcmp(CW.vkey('2.0.0b2'), CW.vkey('2.0.0rc1')), -1)
        self.assertEqual(CW.kcmp(CW.vkey('2.0.0a9'), CW.vkey('2.0.0b1')), -1)
        hit = CW.ghsa_affects('2.0.0-beta.3', dict(vulns=[dict(range='< 2.0.0-rc.1', patched='2.0.0-rc.1')],
                                                   published='2026-09-01T00:00:00Z'))
        self.assertIs(hit[0], True)

    def test_pinned_linuxserver_tag_finds_newer_build(self):
        """BUG 2 (fixed 2026-10-14): for a pinned linuxserver tag like 5.8.3.8933-ls230 the Docker Hub check
        only accepted tags ending in the very same -ls230 build, so it never reported a newer tag."""
        up = FakeUp(releases={'Radarr/Radarr': RADARR_RELS}, hub={'linuxserver/radarr': [
            {'name': n} for n in ('latest', '5.10.0.9100-ls236', '5.10.0.9100-ls235', '5.8.3.8933-ls230', 'develop')]})
        inv = mini_inv(image='linuxserver/radarr:5.8.3.8933-ls230')
        rows, _ = CW.evaluate(inv, CW.Local(False), up, NOW, 3, {})
        self.assertEqual(rows[0]['newest_tag'], '5.10.0.9100-ls236')


# ================================================================ upstream fetching, cache, error paths
class UpstreamTests(Base):
    def fetch(self, net, max_age=0, offline=False, url='https://api.github.com/repos/Radarr/Radarr/releases?per_page=100'):
        up = CW.Upstream(self.cache, max_age, offline)
        with mock.patch('urllib.request.urlopen', net):
            return up, up.get(url, 'gh', CW.Upstream._t_releases)

    def test_trims(self):
        with open(os.path.join(FX, 'up/affected/nvd/ui__unifi_network_application.json')) as f:
            nvd = CW.Upstream._t_nvd(json.load(f))
        self.assertEqual((nvd[0]['id'], nvd[0]['severity'], nvd[0]['score']), ('CVE-2026-50001', 'critical', 10.0))
        self.assertEqual(nvd[0]['matches'][0]['versionEndExcluding'], '9.0.118')
        with open(os.path.join(FX, 'up/affected/gh/Radarr__Radarr.advisories.json')) as f:
            a = CW.Upstream._t_advisories(json.load(f))[0]
        self.assertEqual((a['id'], a['cve'], a['severity'], a['score']), ('GHSA-r4d1-2x3c-9v8b', 'CVE-2026-41001', 'high', 8.1))
        self.assertEqual(a['vulns'][0]['range'], '< 5.10.0.9100')
        self.assertIsNone(CW.Upstream._t_releases({'message': 'Not Found'}))

    def test_cache_hit_and_etag_revalidation(self):
        net = FakeNet('base', mode='304')
        up, data = self.fetch(net)
        self.assertEqual(data[0]['tag'], 'v5.11.0.9300')
        self.assertFalse(net.calls[0][1].has_header('If-none-match'))
        up2, again = self.fetch(net, max_age=3600)                                   # fresh: no request at all
        self.assertEqual((again, up2.net_calls, len(net.calls)), (data, 0, 1))
        up3, again = self.fetch(net, max_age=0)                                      # stale: If-None-Match -> 304
        self.assertEqual(again, data)
        self.assertTrue(net.calls[1][1].has_header('If-none-match'))
        self.assertEqual(up3.errors, [])

    def test_404_cached_as_none(self):
        net = FakeNet('base')
        url = 'https://api.github.com/repos/nobody/nothing/releases?per_page=100'
        e404 = lambda req, **k: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 404, 'nf', _hdrs({}), None))
        up, data = self.fetch(e404, url=url)
        self.assertIsNone(data)
        self.assertEqual(up.errors, [])
        up, data = self.fetch(net, max_age=3600, url=url)
        self.assertEqual(len(net.calls), 0)

    def test_api_down_without_and_with_cache(self):
        up, data = self.fetch(FakeNet(mode='down'))
        self.assertIsNone(data)
        self.assertEqual(up.errors, ['api.github.com/repos/Radarr/Radarr/releases: URLError'])
        _, good = self.fetch(FakeNet('base'))
        up, data = self.fetch(FakeNet(mode='down'))
        self.assertEqual(data, good)                                                   # stale cache beats nothing

    def test_offline(self):
        up, data = self.fetch(FakeNet('base'), offline=True)
        self.assertIsNone(data)
        self.assertIn('no cached data for', up.errors[0])

    def test_nvd_throttle(self):
        up = CW.Upstream(self.cache, 0, False)
        with mock.patch('urllib.request.urlopen', FakeNet('affected', 'base')), mock.patch('time.sleep') as sl:
            a = up.nvd('ui:unifi_network_application', '9.0.114')
            up.nvd('ui:unifi_network_application', '9.0.118')
        self.assertEqual(a[0]['id'], 'CVE-2026-50001')
        self.assertTrue(sl.called and 0 < sl.call_args[0][0] <= 6.5)


# ================================================================ main(): alerting, dedup, silence, errors
class Notify(Base):
    def test_alerts_once(self):
        net = FakeNet('affected', 'base')
        rc, out, err = self.run_main('--notify', '--max-age', '1d', net=net)
        self.assertEqual((rc, err), (1, ''))
        msgs = self.sent()
        self.assertEqual(len(msgs), 1, msgs)
        m = msgs[0]
        self.assertLessEqual(len(m), CW.TG_MAX)
        self.assertTrue(m.startswith('🛡️ Container security check (Testsrv)'))
        self.assertIn('unifi-controller 9.0.114 → 9.0.118', m)                        # critical first
        self.assertLess(m.index('unifi-controller'), m.index('radarr'))
        self.assertLess(m.index('radarr'), m.index('paperless_redis'))
        for s in ('CVE-2026-50001', 'CVE-2026-41001', 'radarr 5.8.3.8933 → 5.10.0.9100'):
            self.assertIn(s, m)
        for quiet in ('sonarr', 'paperless-ngx', 'HomeAssistant', 'sabnzbd', 'claude-bot', 'claude-vault',
                      'Emby', 'tdarr'):
            self.assertNotIn(quiet, m)
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        note = open(os.path.join(self.inbox, notes[0])).read()
        self.assertIn("don't resend", note)
        self.assertIn('CVE-2026-31000', note)                                          # full details incl. redis
        self.assertIn('pinned tag 7.2.4', note)
        self.assertEqual(self.error_notes(), [])
        st = self.statej()
        keys = {k for k, v in st['items'].items() if v['alerted']}
        self.assertEqual(keys, {'adv:radarr:GHSA-r4d1-2x3c-9v8b', 'rel:radarr:5.10.0.9100',
                                'adv:paperless_redis:GHSA-r3d1-2x3c-9v8b', 'adv:unifi-controller:CVE-2026-50001'})
        self.assertIn('sent=True', open(os.path.join(self.state, 'runs.log')).read())
        # next week: same findings -> nothing sent, nothing new in the inbox, but still listed on stdout
        rc, out, err = self.run_main('--notify', '--max-age', '1d', net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(len(self.notes()), 1)
        self.assertIn('[already alerted]', out)
        self.assertNotIn('[new]', out)
        # a later week brings one new advisory: only that container is texted
        rc, out, err = self.run_main('--notify', net=FakeNet('sonarr-new', 'affected', 'base'), cache=self.cache + '2')
        msgs = self.sent()
        self.assertEqual(len(msgs), 2)
        self.assertIn('sonarr 4.0.20.3014 → 4.0.21.3100', msgs[1])
        self.assertIn('CRITICAL', msgs[1])
        for old in ('radarr', 'unifi', 'paperless_redis'):
            self.assertNotIn(old, msgs[1])

    def test_silent_when_nothing_affected(self):
        rc, out, err = self.run_main('--notify', '--max-age', '1d', net=FakeNet('base'))
        self.assertEqual((rc, out, err), (0, '', ''))
        self.assertEqual(self.sent(), [])
        self.assertEqual(os.listdir(self.inbox) if os.path.isdir(self.inbox) else [], [])
        st = self.statej()
        self.assertEqual(st['items'], {})
        last = json.load(open(os.path.join(self.state, 'last.json')))
        rows = {r['container']: r for r in last['rows']}
        self.assertEqual(last['actionable'], [])
        self.assertEqual(rows['radarr']['latest'], '5.10.0.9100')                     # nightly pre-release ignored
        self.assertEqual(rows['HomeAssistant_inabox']['behind'], 2)
        self.assertEqual(rows['paperless-ngx']['newer_security'][0]['tag'], '2.19.0')  # inside the 3-day grace
        self.assertEqual(rows['unifi-controller']['newest_tag'], '9.0.118-unraid')
        self.assertEqual(rows['paperless_redis']['newest_tag'], '7.4.1')
        self.assertEqual(rows['sonarr']['assumed_fixed'], ['GHSA-s0n2-2x3c-9v8b'])

    def test_grace_period_then_alert(self):
        later = NOW + dt.timedelta(days=3)                                             # paperless 2.19.0 + sabnzbd 4.3.3 now due
        rc, out, err = self.run_main('--notify', net=FakeNet('base'), now=later)
        self.assertEqual(rc, 1)
        m = self.sent()[0]
        self.assertIn('paperless-ngx 2.18.4 → 2.19.0', m)
        self.assertIn('binhex-sabnzbd 4.3.2 → 4.3.3', m)

    def test_tg_failure_retries_next_run(self):
        os.environ['CW_TG_EXIT'] = '1'
        rc, out, err = self.run_main('--notify', net=FakeNet('affected', 'base'))
        self.assertIn('tg send failed; will retry next run', err)
        self.assertEqual(self.notes(), [])
        self.assertFalse(any(v['alerted'] for v in self.statej()['items'].values()))
        os.environ['CW_TG_EXIT'] = '0'
        self.run_main('--notify', net=FakeNet('affected', 'base'))
        self.assertEqual(len(self.notes()), 1)
        self.assertEqual(len(self.sent()), 2)                                          # the failed try + the retry

    def test_ack_then_notify_sends_nothing(self):
        rc, out, err = self.run_main('--ack', net=FakeNet('affected', 'base'))
        self.assertIn('marked 4 item(s) as already alerted', err)
        self.assertTrue(all('(ack)' in v['alerted'] for v in self.statej()['items'].values()))
        self.run_main('--notify', net=FakeNet('affected', 'base'))
        self.assertEqual(self.sent(), [])

    def test_dry_run_changes_nothing(self):
        rc, out, err = self.run_main('--notify', '--dry-run', net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 1)
        self.assertIn('[new]', out)
        self.assertEqual(self.sent(), [])
        self.assertFalse(os.path.exists(os.path.join(self.state, 'state.json')))
        self.assertFalse(os.path.exists(self.inbox))

    def test_without_notify_never_sends(self):
        rc, out, err = self.run_main(net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 1)
        self.assertEqual(self.sent(), [])
        self.assertIn('[new]', out)

    def test_report_and_json(self):
        rc, out, err = self.run_main('--report', net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 0)
        for name in ('radarr', 'sonarr', 'unifi-controller', 'tdarr'):
            self.assertIn(name, out)
        rc, out, err = self.run_main('--json', net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)['actionable']), 4)
        self.assertEqual(self.sent(), [])

    def test_message_budget(self):
        items = []
        for n in range(12):
            items.append(dict(container=f'container-{n:02d}', app='X', installed='1.0.0', latest='1.0.9',
                              pinned=False, tag='latest', newest_tag=None, kind='advisory', key=f'k{n}', sev=3 + n % 2,
                              ids=[f'CVE-2026-{1000 + n}'], why='A fairly long explanation of the vulnerability ' * 3,
                              url=f'https://github.com/x/y/security/advisories/GHSA-{n:04d}-aaaa-bbbb', fixed='1.0.9'))
        msg = CW.compose_message(CW.group_items(items))
        self.assertLessEqual(len(msg), CW.TG_MAX)
        self.assertIn('more:', msg)
        self.assertTrue(msg.endswith('tell me if you want an update plan.'))
        self.assertIn('container-01', msg.split('\n')[1])                              # a CRITICAL one leads


class ErrorPaths(Base):
    def test_api_down_cold_cache_is_silent(self):
        rc, out, err = self.run_main('--notify', net=FakeNet(mode='down'))
        self.assertEqual((rc, out, err), (0, '', ''))
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.notes(), [])
        notes = self.error_notes()
        self.assertEqual(len(notes), 1)                                                 # inbox only, never Telegram
        self.assertIn('several upstream sources failed', open(os.path.join(self.inbox, notes[0])).read())
        rows = {r['container']: r for r in json.load(open(os.path.join(self.state, 'last.json')))['rows']}
        self.assertIn('upstream releases unavailable', rows['radarr']['notes'])
        self.assertIn('NVD unavailable', rows['unifi-controller']['notes'])

    def test_api_down_warm_cache_no_repeat(self):
        self.run_main('--notify', net=FakeNet('affected', 'base'))
        self.assertEqual(len(self.sent()), 1)
        rc, out, err = self.run_main('--notify', '--max-age', '0', net=FakeNet(mode='down'))
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sent()), 1)
        self.assertIn('[already alerted]', out)
        last = json.load(open(os.path.join(self.state, 'last.json')))
        self.assertEqual(len(last['actionable']), 4)                                   # judged from the cache
        self.assertTrue(any('URLError' in e for e in last['errors']))

    def test_github_rate_limit(self):
        for mode in ('ratelimit', '429'):
            with self.subTest(mode=mode):
                net = FakeNet('base', mode=mode)
                rc, out, err = self.run_main('--notify', net=net, cache=os.path.join(self.d, 'c-' + mode),
                                             state=os.path.join(self.d, 's-' + mode))
                self.assertEqual(len(net.host_calls('api.github.com')), 1)             # stops asking after the first
                self.assertEqual((rc, out), (0, ''))
                self.assertEqual(self.sent(), [])
                last = json.load(open(os.path.join(self.d, 's-' + mode, 'last.json')))
                self.assertIn('GitHub rate limit reached: using cached data', last['errors'])

    def test_rate_limit_with_cache_keeps_judging(self):
        self.run_main('--notify', net=FakeNet('affected', 'base'))
        net = FakeNet('base', mode='ratelimit')
        rc, out, err = self.run_main('--notify', '--max-age', '0', net=net)
        self.assertEqual(rc, 1)
        self.assertEqual(len(net.host_calls('api.github.com')), 1)
        self.assertEqual(len(self.sent()), 1)

    def test_nvd_down(self):
        rc, out, err = self.run_main('--notify', net=FakeNet('affected', 'base', mode='nvd-down'))
        m = self.sent()[0]
        self.assertNotIn('unifi', m)
        self.assertIn('radarr', m)
        last = json.load(open(os.path.join(self.state, 'last.json')))
        self.assertTrue(any('HTTP 503' in e for e in last['errors']))


# ================================================================ collection over (fake) ssh
class Collect(Base):
    def test_root_mode_runs_collector_over_ssh(self):
        rc, out, err = self.run_main('--notify', '--max-age', '1d', input_file=None, net=FakeNet('base'))
        self.assertEqual((rc, out, err), (0, '', ''))
        log = open(self.sshlog).read()
        self.assertIn('unraid-ssh mode', log)
        self.assertIn('ssh -o BatchMode=yes -o ConnectTimeout=10 unraid bash -s', log)
        self.assertIn('ssh -G unraid', log)                                            # Local's host-IP lookup
        self.assertEqual(open(self.env['CW_SSH_STDIN']).read().strip(), CW.COLLECTOR.strip())
        self.assertEqual(open(os.path.join(self.state, 'last-collect.txt')).read(), open(INV).read())

    def test_lockdown_verbs_mode(self):
        os.environ['CW_LOCKDOWN_MODE'] = 'verbs'
        rc, out, err = self.run_main(input_file=None, net=FakeNet('base'))
        self.assertEqual(rc, 0)
        log = open(self.sshlog).read()
        self.assertIn('unraid-ssh --host unraid run containers', log)
        self.assertNotIn('bash -s', log)

    def test_ssh_failure(self):
        os.environ['CW_SSH_FAIL'] = '1'
        rc, out, err = self.run_main('--notify', input_file=None, net=FakeNet('affected', 'base'))
        self.assertEqual((rc, out), (2, ''))
        self.assertIn('could not collect from unraid', err)
        self.assertIn('ssh exit 255', err)
        self.assertIn('collect-failed RuntimeError', open(os.path.join(self.state, 'runs.log')).read())
        self.assertEqual(self.sent(), [])
        self.assertFalse(os.path.exists(os.path.join(self.state, 'state.json')))
        self.assertEqual(self.net.calls, [])

    def test_missing_unraid_ssh_falls_back_to_root(self):
        with mock.patch.object(CW, 'UNRAID_SSH', os.path.join(self.d, 'does-not-exist')):
            self.assertEqual(CW.lockdown_mode(), 'root')

    def test_empty_inventory(self):
        p = os.path.join(self.d, 'empty.txt')
        open(p, 'w').write('@@meta\n@@containers\n@@end\n')
        rc, out, err = self.run_main('--notify', input_file=p)
        self.assertEqual(rc, 2)
        self.assertIn('no containers', err)
        self.assertEqual(self.sent(), [])

    def test_concurrent_run_refused(self):
        os.makedirs(self.state)
        with open(os.path.join(self.state, 'lock'), 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc, out, err = self.run_main('--notify', net=FakeNet('affected', 'base'))
        self.assertEqual(rc, 2)
        self.assertIn('another run is in progress', err)
        self.assertEqual(self.sent(), [])

    def test_collector_is_read_only(self):
        rc, out, err = self.run_main('--show-collector')
        self.assertEqual(out.strip(), CW.COLLECTOR.strip())
        self.assertIsNone(re.search(r'docker\s+(pull|run|exec|rm|rmi|stop|start|restart|update|kill|create|cp)\b', out))
        self.assertIn("claude-vault", out)                                              # vault env/history excluded
        self.assertNotRegex(out, r'(?m)^\s*(rm|mv|cp|tee|dd)\s')


# ================================================================ the cron entrypoint (subprocess, sockets blocked)
def cron_args():
    with open(CRONTAB) as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith('#') and 'bin/container-watch' in s:
                return s.split('bin/container-watch', 1)[1].split('>>')[0].split()
    raise AssertionError('no container-watch line in ~/crontab')


class Cron(Base):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.d, 'home')
        self.netlog = os.path.join(self.d, 'net.log')
        os.makedirs(self.home)

    def warm_cache(self, *layers):
        """The cron run must not need the network: fill $HOME/.cache/container-watch from the fixtures."""
        self.run_main('--dry-run', '--max-age', '0', net=FakeNet(*layers),
                      cache=os.path.join(self.home, '.cache/container-watch'), state=os.path.join(self.d, 'warm'))

    def cron(self):
        env = dict(os.environ, HOME=self.home, CW_BIN=BIN, CW_NET_LOG=self.netlog, CW_TEST_NOW=NOW.isoformat())
        r = subprocess.run([sys.executable, '-I', BOOT] + cron_args(), env=env, capture_output=True, text=True,
                           timeout=120)
        self.assertFalse(os.path.exists(self.netlog), 'cron run tried the network: ' +
                         (open(self.netlog).read() if os.path.exists(self.netlog) else ''))
        return r

    def home_inbox(self):
        p = os.path.join(self.home, 'inbox')
        return sorted(os.listdir(p)) if os.path.isdir(p) else []

    def test_cron_line(self):
        a = cron_args()
        self.assertIn('--notify', a)
        self.assertNotIn('--dry-run', a)
        self.assertNotIn('--input', a)

    def test_cron_quiet_when_nothing_affected(self):
        self.warm_cache('base')
        r = self.cron()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.home_inbox(), [])
        runs = open(os.path.join(self.home, '.local/state/container-watch/runs.log')).read()
        self.assertIn('actionable=0 new=0 sent=None net=0', runs)

    def test_cron_alerts_once(self):
        self.warm_cache('affected', 'base')
        r = self.cron()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stderr, '')
        self.assertIn('[new]', r.stdout)                                                # goes to cron.log only
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(len(self.home_inbox()), 1)
        r = self.cron()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(len(self.home_inbox()), 1)

    def test_cron_ssh_down_is_quiet(self):
        self.warm_cache('affected', 'base')
        with mock.patch.dict(os.environ, {'CW_SSH_FAIL': '1'}):
            r = self.cron()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, '')
        self.assertIn('could not collect', r.stderr)                                   # one line in cron.log
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.home_inbox(), [])


if __name__ == '__main__':
    unittest.main(verbosity=1)
