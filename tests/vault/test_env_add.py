#!/usr/bin/python3
"""vault admin env-add (+ init's merge): allowlist and value shapes, the server-side merge script (run here with sh),
and the CLI end to end through a fake ssh. The .env here holds a fake token hash; it must never reach any output.

Run: python3 test_env_add.py   (VAULT_CLI=path/to/vault to test a copy; TEST_AWK_DIRS=dir1:dir2 to repeat the merge
tests with other awk builds, e.g. a gawk next to the system's mawk; TEST_SHELLS=sh,bash)."""
import importlib.machinery
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = os.environ.get('VAULT_CLI') or os.path.expanduser('~/bin/vault')
FAKE_HASH = 'f' * 16 + '0123456789abcdef' * 3          # stands in for VAULT_TOKEN_SHA256
SHELLS = [s for s in os.environ.get('TEST_SHELLS', 'sh,bash').split(',') if s]
AWK_DIRS = [''] + [d for d in os.environ.get('TEST_AWK_DIRS', '').split(':') if d]


def load():
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader('vault_cli', CLI)
    spec = importlib.util.spec_from_loader('vault_cli', loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


V = load()
ENV0 = ('# written by `vault admin init`: sha256 of the API token\n'
        f'VAULT_TOKEN_SHA256={FAKE_HASH}\n'
        '# VAULT_CPUS=99 (a comment stays)\n'
        'VAULT_CPUS=4\n'
        'export VAULT_CPUSET=1-2\n'
        'VAULT_CPUS=6\n'
        'OTHER=keep me')                                     # no trailing newline


class Box:
    """A temp 'server': <root>/app/.env, the backups land in <root>/app-env-backups."""
    def __init__(self, env=ENV0, mode=0o640):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        self.env = self.app / '.env'
        self.bak = self.root / ('app' + V.ENV_BACKUPS_SUFFIX)
        if env is not None:
            self.env.write_text(env)
            self.env.chmod(mode)

    def run(self, pairs, shell='sh', awkdir='', **kw):
        script = V.env_merge_script(pairs, remote=str(self.app), **kw)
        path = (awkdir + ':' if awkdir else '') + os.environ.get('PATH', '/usr/bin:/bin')
        p = subprocess.run([shell, '-s'], input=script, text=True, capture_output=True, timeout=60,
                           env={'PATH': path, 'LC_ALL': 'C'})
        return p.returncode, p.stdout, p.stderr

    def close(self):
        self.tmp.cleanup()


class PairsTests(unittest.TestCase):
    def bad(self, *args, expect=''):
        p = subprocess.run([sys.executable, '-I', CLI, 'admin', 'env-add', *args], capture_output=True, text=True,
                           timeout=60, env={'PATH': '/nonexistent', 'HOME': tempfile.gettempdir(),
                                            'UNRAID_SSH_BIN': '/nonexistent/unraid-ssh'})
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertEqual(p.stdout, '')
        self.assertIn(expect, p.stderr)
        self.assertNotIn(FAKE_HASH, p.stderr)
        for a in args:                                   # values (and odd arguments) are never echoed
            v = a.partition('=')[2] if '=' in a else a
            if len(v) >= 3:
                self.assertNotIn(v, p.stderr)
        return p.stderr

    def test_allowlist_and_shapes(self):
        self.bad(expect='give KEY=VALUE')
        self.bad(f'VAULT_TOKEN_SHA256={FAKE_HASH}', expect='not on the allowlist')
        self.bad('PATH=/tmp/x', expect='not on the allowlist')
        self.bad(FAKE_HASH, expect='argument 1 is not KEY=VALUE')
        self.bad('VAULT_CPUS=8', f'lower{FAKE_HASH}=x', expect='argument 2 is not KEY=VALUE')
        self.bad('VAULT_CPUS=8', 'VAULT_CPUS=9', expect='VAULT_CPUS given twice')
        for kv in ('VAULT_CPUS=0', 'VAULT_CPUS=abc', 'VAULT_CPUS=1e3', 'VAULT_CPUSET=0-3;id', 'VAULT_CPUSET=0-3 ',
                   'VAULT_MEM_LIMIT=22gb', 'VAULT_MEM_LIMIT=$(id)', 'VAULT_HA_DEVICE=Pixel-8',
                   'VAULT_HA_DEVICE=a\'b', 'VAULT_MODELS_DIR=/srv/../etc', 'VAULT_MODELS_DIR=/srv/my models',
                   'VAULT_MODELS_DIR=/srv/x;y', 'VAULT_MODELS_DIR=', f'VAULT_HA_DEVICE={"a" * 65}',
                   'VAULT_CPUSET=0-3\nVAULT_TOKEN_SHA256=x'):
            self.bad(kv, expect='not a valid value')

    def test_good_values_parse(self):
        pairs = V.env_add_pairs(['VAULT_HA_DEVICE=pixel_8', 'VAULT_MODELS_DIR=/srv/llm-models', 'VAULT_CPUS=7.5',
                                 'VAULT_CPUSET=0-5,8,9,12-15', 'VAULT_MEM_LIMIT=22g'])
        self.assertEqual([k for k, _ in pairs], ['VAULT_HA_DEVICE', 'VAULT_MODELS_DIR', 'VAULT_CPUS', 'VAULT_CPUSET',
                                                 'VAULT_MEM_LIMIT'])
        self.assertEqual(V.env_add_pairs(['VAULT_HA_DEVICE=']), [('VAULT_HA_DEVICE', '')])
        self.assertEqual(V.env_add_pairs(['VAULT_MODELS_DIR=./models']), [('VAULT_MODELS_DIR', './models')])

    def test_allowlist_is_exactly_the_deploy_values(self):
        self.assertEqual(set(V.ENV_ADD_KEYS), {'VAULT_HA_DEVICE', 'VAULT_MODELS_DIR', 'VAULT_CPUS', 'VAULT_CPUSET',
                                               'VAULT_MEM_LIMIT'})


class MergeScriptTests(unittest.TestCase):
    PAIRS = [('VAULT_CPUS', '12'), ('VAULT_CPUSET', '0-5,8'), ('VAULT_HA_DEVICE', ''), ('VAULT_MEM_LIMIT', '22g')]

    def each(self):
        for sh in SHELLS:
            for awk in AWK_DIRS:
                with self.subTest(shell=sh, awk=awk or 'system'):
                    yield sh, awk

    def test_merge_backup_audit_and_rerun(self):
        for sh, awk in self.each():
            b = Box()
            try:
                st0 = b.env.stat()
                rc, out, err = b.run(self.PAIRS, sh, awk, ts='20991231-235959')
                self.assertEqual(rc, 0, err)
                self.assertEqual(out, 'backup .env.20991231-235959\nupdated VAULT_CPUS\nupdated VAULT_CPUSET\n'
                                      'added VAULT_HA_DEVICE\nadded VAULT_MEM_LIMIT\n')
                self.assertEqual(err, '')
                self.assertEqual(b.env.read_text(), ENV0.replace('VAULT_CPUS=4', 'VAULT_CPUS=12')
                                 .replace('export VAULT_CPUSET=1-2', 'VAULT_CPUSET=0-5,8').replace('VAULT_CPUS=6\n', '')
                                 + '\nVAULT_HA_DEVICE=\nVAULT_MEM_LIMIT=22g\n')
                st1 = b.env.stat()
                self.assertEqual(stat.S_IMODE(st1.st_mode), 0o640)               # mode of the old file kept
                self.assertNotEqual(st1.st_ino, st0.st_ino)                      # replaced by rename, not rewritten
                bk = b.bak / '.env.20991231-235959'
                self.assertEqual(bk.read_text(), ENV0)
                self.assertEqual(stat.S_IMODE(bk.stat().st_mode), 0o640)
                self.assertEqual(stat.S_IMODE(b.bak.stat().st_mode), 0o700)
                audit = (b.bak / 'audit.log').read_text()
                self.assertRegex(audit, r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4} env-add updated VAULT_CPUS '
                                        r'updated VAULT_CPUSET added VAULT_HA_DEVICE added VAULT_MEM_LIMIT  ?'
                                        r'backup=\.env\.20991231-235959\n$')
                self.assertNotIn('22g', audit)
                self.assertEqual(sorted(os.listdir(b.app)), ['.env'])                # no temp files left
                rc, out, err = b.run(self.PAIRS, sh, awk, ts='20991231-235960')     # again: nothing to do
                self.assertEqual((rc, err), (0, ''))
                self.assertEqual(out, ''.join(f'unchanged {k}\n' for k, _ in self.PAIRS))
                self.assertEqual(b.env.stat().st_ino, st1.st_ino)
                self.assertEqual(sorted(os.listdir(b.bak)), ['.env.20991231-235959', 'audit.log'])
                rc, out, err = b.run([('VAULT_CPUS', '8')], sh, awk, ts='20991231-235959')   # same stamp: no clobber
                self.assertEqual(rc, 0, err)
                self.assertRegex(out, r'^backup \.env\.20991231-235959\.\d+\nupdated VAULT_CPUS\n$')
                self.assertEqual(bk.read_text(), ENV0)
                for o in (out, err, audit):
                    self.assertNotIn(FAKE_HASH, o)
                    self.assertNotIn('keep me', o)
            finally:
                b.close()

    def test_missing_symlink_and_create(self):
        for sh, awk in self.each():
            b = Box(env=None)
            try:
                rc, out, err = b.run(self.PAIRS, sh, awk)
                self.assertEqual((rc, out), (4, ''))
                self.assertIn('env-add: no .env on the server', err)
                self.assertFalse(b.env.exists())
                self.assertFalse(b.bak.exists())
                rc, out, err = b.run([('VAULT_TOKEN_SHA256', FAKE_HASH)], sh, awk, create=True, tag='init')
                self.assertEqual(rc, 0, err)
                self.assertRegex(out, r'^backup \.env\.[0-9-]+\nadded VAULT_TOKEN_SHA256\n$')
                self.assertEqual(b.env.read_text(), f'VAULT_TOKEN_SHA256={FAKE_HASH}\n')
                self.assertEqual(stat.S_IMODE(b.env.stat().st_mode), 0o600)
                real = b.root / 'real.env'
                b.env.rename(real)
                b.env.symlink_to(real)
                rc, out, err = b.run(self.PAIRS, sh, awk)
                self.assertEqual((rc, out), (4, ''))
                self.assertIn('not a regular file', err)
                self.assertTrue(b.env.is_symlink())
                self.assertEqual(real.read_text(), f'VAULT_TOKEN_SHA256={FAKE_HASH}\n')
            finally:
                b.close()

    def test_init_keeps_deploy_values(self):
        for sh, awk in self.each():
            b = Box(env=f'VAULT_TOKEN_SHA256={"0" * 64}\nVAULT_CPUS=12\nVAULT_CPUSET=0-5\n', mode=0o600)
            try:
                rc, out, err = b.run([('VAULT_TOKEN_SHA256', FAKE_HASH)], sh, awk, create=True, tag='init')
                self.assertEqual(rc, 0, err)
                self.assertTrue(out.endswith('updated VAULT_TOKEN_SHA256\n'))
                self.assertEqual(b.env.read_text(), f'VAULT_TOKEN_SHA256={FAKE_HASH}\nVAULT_CPUS=12\nVAULT_CPUSET=0-5\n')
                self.assertNotIn(FAKE_HASH, out + err)
            finally:
                b.close()


FAKE_SSH = '''#!/bin/sh
# fake ssh: logs the call, runs the remote command here with the server path mapped into the test box
echo "$*" >> "$FAKE_LOG"
[ -z "$FAKE_SSH_FAIL" ] || exit 255
for last; do :; done
case "$last" in
  "sh -s") sed "s#$FAKE_REMOTE_FROM#$FAKE_REMOTE_TO#g" | sh -s; rc=$?
           [ -z "$FAKE_SSH_LEAK" ] || cat "$FAKE_REMOTE_TO/.env"; exit $rc ;;
  *) exit 0 ;;
esac
'''


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.box = Box()
        (t / 'bin').mkdir()
        (t / 'home' / '.secrets').mkdir(parents=True)
        (t / 'bin' / 'ssh').write_text(FAKE_SSH)
        (t / 'bin' / 'unraid-ssh').write_text('#!/bin/sh\necho "${FAKE_MODE:-root}"\n')
        for f in ('ssh', 'unraid-ssh'):
            (t / 'bin' / f).chmod(0o755)
        self.t, self.log = t, t / 'ssh.log'
        self.envv = {'PATH': f'{t}/bin:/usr/bin:/bin', 'HOME': str(t / 'home'), 'UNRAID_SSH_BIN': str(t / 'bin' /
                     'unraid-ssh'), 'FAKE_LOG': str(self.log), 'FAKE_REMOTE_FROM': V.REMOTE,
                     'FAKE_REMOTE_TO': str(self.box.app), 'LC_ALL': 'C'}

    def tearDown(self):
        self.box.close()
        self.tmp.cleanup()

    def cli(self, *args, **env):
        p = subprocess.run([sys.executable, '-I', CLI, 'admin', *args], capture_output=True, text=True, timeout=90,
                           env={**self.envv, **env})
        return p.returncode, p.stdout, p.stderr

    def test_env_add_end_to_end(self):
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12', 'VAULT_MODELS_DIR=/srv/llm-models')
        self.assertEqual((rc, out), (0, 'updated VAULT_CPUS\nadded VAULT_MODELS_DIR\n'), err)
        self.assertIn('backup .env.', err)
        self.assertIn('VAULT_MODELS_DIR=/srv/llm-models\n', self.box.env.read_text())
        self.assertIn(f'VAULT_TOKEN_SHA256={FAKE_HASH}\n', self.box.env.read_text())
        audit = (self.t / 'home' / '.local' / 'state' / 'vault' / 'admin-audit.log')
        self.assertRegex(audit.read_text(), r'env-add unraid updated VAULT_CPUS added VAULT_MODELS_DIR backup=\.env\.')
        self.assertEqual(stat.S_IMODE(audit.stat().st_mode), 0o600)
        self.assertEqual(self.log.read_text().splitlines(), ['-o BatchMode=yes unraid sh -s'])
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12')
        self.assertEqual((rc, out), (0, 'unchanged VAULT_CPUS\n'), err)
        self.assertIn('nothing changed', err)
        for o in (out, err, audit.read_text()):
            self.assertNotIn(FAKE_HASH, o)

    def test_output_is_filtered(self):
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12', FAKE_SSH_LEAK='1')   # a server that prints the file
        self.assertEqual((rc, out), (0, 'updated VAULT_CPUS\n'), err)
        self.assertNotIn(FAKE_HASH, out + err)

    def test_failures(self):
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12', FAKE_SSH_FAIL='1')
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('was not changed or the result is unclear (exit 255)', err)
        self.box.env.unlink()
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12')
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('no .env on the server (vault admin init first)', err)

    def test_lockdown_and_usage_before_network(self):
        rc, out, err = self.cli('env-add', 'VAULT_CPUS=12', FAKE_MODE='verbs')
        self.assertEqual((rc, out), (2, ''))
        self.assertIn('not mine any more since the SSH lockdown', err)
        rc, out, err = self.cli('env-add', f'VAULT_TOKEN_SHA256={FAKE_HASH}')
        self.assertEqual(rc, 2)
        self.assertFalse(self.log.exists())                      # neither reached ssh
        self.assertEqual(self.box.env.read_text(), ENV0)

    def test_init_merges(self):
        tok = self.t / 'home' / '.secrets' / 'vault-token'
        tok.write_text('tok\n')
        tok.chmod(0o600)
        rc, out, err = self.cli('init')
        self.assertEqual(rc, 0, err)
        self.assertIn('token hash written to unraid:', out)
        txt = self.box.env.read_text()
        self.assertRegex(txt, r'(?m)^VAULT_TOKEN_SHA256=[0-9a-f]{64}$')
        self.assertNotIn(FAKE_HASH, txt)
        self.assertIn('VAULT_CPUS=4\n', txt)                                      # the rest stays
        self.assertIn('OTHER=keep me', txt)


if __name__ == '__main__':
    unittest.main(verbosity=1)
