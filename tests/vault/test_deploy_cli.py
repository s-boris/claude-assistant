#!/usr/bin/env python3
"""`vault deploy request|status` (Telegram-approved vault deploys) against a fake vault client and a temp git repo:
the commit is resolved to its full sha and must be on origin/main, the exit codes (0 deployed, 10 under way,
1 not deployed), --wait polling, the status lines and --json.
Run: python3 test_deploy_cli.py   (VAULT_CLI=path/to/vault to test a copy)"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
CLI = os.environ.get('VAULT_CLI') or os.path.expanduser('~/bin/vault')
ld = importlib.machinery.SourceFileLoader('vaultcli_deploy', CLI)
spec = importlib.util.spec_from_loader(ld.name, ld)
V = importlib.util.module_from_spec(spec)
ld.exec_module(V)

TMP = tempfile.mkdtemp(prefix='vault-deploy-cli-')
REPO = os.path.join(TMP, 'repo')


def sh(*a):
    return subprocess.run(['git', '-C', REPO, '-c', 'user.email=t@t', '-c', 'user.name=t', *a], check=True,
                          capture_output=True, text=True).stdout.strip()


os.makedirs(REPO)
sh('init', '-q', '-b', 'main')
open(os.path.join(REPO, 'a'), 'w').write('1')
sh('add', 'a')
sh('commit', '-qm', 'one')
ON_MAIN = sh('rev-parse', 'HEAD')
sh('update-ref', 'refs/remotes/origin/main', ON_MAIN)
open(os.path.join(REPO, 'a'), 'w').write('2')
sh('commit', '-qam', 'two (not pushed)')
NOT_PUSHED = sh('rev-parse', 'HEAD')
V.SRC = REPO
RID = '0123456789abcdef' * 2


def req(state, **kw):
    r = {'request_id': RID, 'commit': ON_MAIN, 'state': state, 'created_at': '2026-01-01T00:00:00+01:00'}
    r.update(kw)
    return r


RUNNER = {'alive': True, 'seen': '2026-01-01T00:00:00+01:00', 'state': 'idle', 'job': None, 'deployed': 'e' * 40}


class FakeClient:
    def __init__(self, states):
        self.states, self.calls = list(states), []

    def call(self, method, path, body=None, timeout=60, ok_codes=(), raise_timeout=False):
        self.calls.append((method, path, body))
        if path == '/deploy/request':
            return req('reviewing')
        if path == '/deploy/status':
            return {'runner': RUNNER, 'requests': [req('deployed'), req('refused', reason='needs the User Scripts button')]}
        st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {'runner': RUNNER, 'request': req(st, review={'kind': 'update', 'commits': 2, 'files': 3,
                                                              'weights': 'changed', 'privacy': 'same', 'build': 'same',
                                                              'subject': 'a change'})}


def run(argv, states=('reviewing',)):
    c = FakeClient(states)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = V.main(['deploy'] + argv, client=c)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue(), c


class DeployCliTests(unittest.TestCase):
    def setUp(self):
        V.time.sleep = lambda s: None

    def test_request_resolves_the_full_sha_on_main(self):
        rc, out, err, c = run(['request', ON_MAIN[:7]])
        self.assertEqual(c.calls[0], ('POST', '/deploy/request', {'commit': ON_MAIN}))
        self.assertEqual(rc, 10)                                                   # under way
        self.assertIn(f'deploy request {RID[:8]} for {ON_MAIN[:7]}', out)
        self.assertEqual(c.calls[1][1], f'/deploy/status?id={RID}')

    def test_request_refuses_what_github_does_not_have(self):
        rc, out, err, c = run(['request', NOT_PUSHED])
        self.assertEqual(rc, 3)
        self.assertIn('not on GitHub main yet', err)
        self.assertEqual(c.calls, [])
        rc, out, err, c = run(['request', 'nosuchref'])
        self.assertEqual(rc, 2)
        self.assertEqual(c.calls, [])

    def test_wait_until_deployed(self):
        rc, out, err, c = run(['request', ON_MAIN, '--wait', '600'], states=('reviewing', 'awaiting', 'deploying', 'deployed'))
        self.assertEqual(rc, 0)
        self.assertIn('DEPLOYED', out)
        self.assertIn('WEIGHTS CHANGED', out)
        self.assertIn('waiting for his ✅ Deploy', err)

    def test_status_exit_codes(self):
        for state, code in (('deployed', 0), ('refused', 1), ('failed', 1), ('denied', 1), ('awaiting', 10),
                            ('deploying', 10)):
            rc, out, err, c = run(['status', RID[:8]], states=(state,))
            self.assertEqual(rc, code, state)
            self.assertIn('deploy runner: idle', out)
        rc, out, err, c = run(['status', 'xyz'])
        self.assertEqual(rc, 2)

    def test_status_overview_and_json(self):
        rc, out, err, c = run(['status'])
        self.assertEqual(rc, 0)
        self.assertIn('the vault runs eeeeeee', out)
        self.assertIn('(needs the User Scripts button)', out)
        rc, out, err, c = run(['status', '--json'])
        self.assertEqual(json.loads(out)['runner']['deployed'], 'e' * 40)

    def test_a_vault_without_deploy_routes(self):
        class Old(FakeClient):
            def call(self, method, path, body=None, timeout=60, ok_codes=(), raise_timeout=False):
                self.calls.append((method, path, body))
                if 404 in ok_codes:
                    return {'error': f'no such endpoint: {method} {path}', 'code': 'not_found'}
                raise AssertionError('a 404 must not end the CLI before it explains')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = V.main(['deploy', 'status'], client=Old(('x',)))
            except SystemExit as e:
                rc = e.code
        self.assertEqual(rc, 3)
        self.assertIn('no Telegram deploys yet', err.getvalue())
        self.assertIn('User Scripts button', err.getvalue())

    def test_runner_not_seen(self):
        self.assertIn('NOT SEEN', V.runner_line({'alive': False, 'seen': None}))

    def test_stage_has_every_file_compose_reads(self):
        src = os.path.expanduser('~/opt/claude-vault')
        if not os.path.isfile(os.path.join(src, 'vault.env')):
            self.skipTest('no vault repo here')
        old = V.SRC, V.STAGE
        V.SRC, V.STAGE = src, os.path.join(TMP, 'stage')
        try:
            st = V.stage()
            for f in ('docker-compose.yml', 'vault.env', 'Dockerfile', 'app/deploys.py'):
                self.assertTrue(os.path.isfile(os.path.join(st, f)), f)
        finally:
            V.SRC, V.STAGE = old


if __name__ == '__main__':
    unittest.main(verbosity=1)
