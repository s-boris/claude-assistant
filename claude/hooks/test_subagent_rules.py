#!/usr/bin/python3 -I
"""Tests for the subagent-rules PreToolUse hook (subagent-rules.sh + subagent_rules.py): rules block, default effort,
fork/non-Agent passthrough, agent definitions that set their own effort, fail-open on bad input or a broken script,
the settings.json entry. Run: python3 -I test_subagent_rules.py   (log and agent dirs go to a temp dir)"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / 'subagent-rules.sh'
SETTINGS = Path('/home/node/.claude/settings.json')
MARKER = '[subagent-rules]'


def agent_call(prompt='Find the bug in foo.py and report.', **extra):
    ti = {'description': 'Find bug', 'prompt': prompt}
    ti.update(extra)
    return {'session_id': 'x', 'cwd': '/nonexistent-cwd', 'hook_event_name': 'PreToolUse',
            'tool_name': 'Agent', 'tool_input': ti, 'tool_use_id': 'toolu_1'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='subagent-rules-test-'))
        self.agents = self.tmp / 'agents'
        self.agents.mkdir()
        self.log = self.tmp / 'log'
        self.env = dict(os.environ, SUBAGENT_RULES_LOG=str(self.log), SUBAGENT_RULES_AGENTS_DIR=str(self.agents))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, stdin, hook=HOOK, env=None):
        data = stdin if isinstance(stdin, (str, bytes)) else json.dumps(stdin)
        if isinstance(data, str):
            data = data.encode()
        p = subprocess.run([str(hook)], input=data, capture_output=True, timeout=20, env=env or self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode()

    def updated(self, stdin):
        out = self.run_hook(stdin)
        self.assertTrue(out.strip(), 'expected hook output')
        o = json.loads(out)
        h = o['hookSpecificOutput']
        self.assertEqual(h['hookEventName'], 'PreToolUse')
        self.assertEqual(h['permissionDecision'], 'allow')
        self.assertIsInstance(h['updatedInput'], dict)
        return h['updatedInput']


class Rules(Base):
    def test_missing_marker_appended_after_blank_line(self):
        call = agent_call(run_in_background=True, subagent_type='general-purpose', effort='high')
        ui = self.updated(call)
        orig = call['tool_input']['prompt']
        self.assertTrue(ui['prompt'].startswith(orig + '\n\n' + MARKER + '\n'))
        self.assertIn('Never launch an interactive `claude`', ui['prompt'])
        self.assertTrue(ui['prompt'].rstrip().endswith('not even by switching tools.'))
        self.assertEqual(ui['prompt'].count(MARKER), 1)
        # full replacement input: every original key kept
        for k in ('description', 'run_in_background', 'subagent_type'):
            self.assertEqual(ui[k], call['tool_input'][k])
        self.assertEqual(ui['effort'], 'high')

    def test_trailing_newlines_normalised(self):
        ui = self.updated(agent_call('Do X.\n\n\n', effort='max'))
        self.assertTrue(ui['prompt'].startswith('Do X.\n\n' + MARKER))

    def test_marker_present_and_effort_given_no_output(self):
        p = 'Do X.\n\n' + MARKER + '\n- custom rules'
        self.assertEqual(self.run_hook(agent_call(p, effort='high')), '')

    def test_marker_present_prompt_unchanged_effort_added(self):
        p = 'Do X.\n' + MARKER + '\n- custom'
        ui = self.updated(agent_call(p))
        self.assertEqual(ui['prompt'], p)
        self.assertEqual(ui['effort'], 'xhigh')

    def test_idempotent_on_own_output(self):
        ui = self.updated(agent_call())
        again = agent_call()
        again['tool_input'] = ui
        self.assertEqual(self.run_hook(again), '')

    def test_unicode_prompt_preserved(self):
        p = 'Prüfe die Datei „Zürich“ 🎉 – bitte'
        ui = self.updated(agent_call(p, effort='low'))
        self.assertTrue(ui['prompt'].startswith(p + '\n\n'))


class Effort(Base):
    def test_no_effort_gets_xhigh(self):
        ui = self.updated(agent_call(subagent_type='Explore'))
        self.assertEqual(ui['effort'], 'xhigh')

    def test_null_or_empty_effort_gets_xhigh(self):
        for e in (None, ''):
            self.assertEqual(self.updated(agent_call(effort=e))['effort'], 'xhigh')

    def test_effort_given_kept(self):
        for e in ('low', 'medium', 'high', 'max', 'xhigh', 'lower', 'higher'):
            self.assertEqual(self.updated(agent_call(effort=e))['effort'], e)

    def test_definition_with_effort_respected(self):
        (self.agents / 'idea-scan.md').write_text('---\nname: idea-scan\nmodel: inherit\neffort: high\n---\nBody\n')
        ui = self.updated(agent_call(subagent_type='idea-scan'))
        self.assertNotIn('effort', ui)
        self.assertIn(MARKER, ui['prompt'])

    def test_definition_without_effort_gets_xhigh(self):
        (self.agents / 'plain.md').write_text('---\nname: plain\ndescription: x\n---\nBody\n')
        self.assertEqual(self.updated(agent_call(subagent_type='plain'))['effort'], 'xhigh')

    def test_definition_name_differs_from_filename(self):
        (self.agents / 'file.md').write_text('---\nname: real-name\neffort: max\n---\n')
        self.assertNotIn('effort', self.updated(agent_call(subagent_type='real-name')))

    def test_project_agents_dir_from_cwd(self):
        proj = self.tmp / 'proj'
        (proj / '.claude' / 'agents').mkdir(parents=True)
        (proj / '.claude' / 'agents' / 'p.md').write_text('---\nname: p\neffort: low\n---\n')
        call = agent_call(subagent_type='p')
        call['cwd'] = str(proj)
        self.assertNotIn('effort', self.updated(call))

    def test_marker_present_and_definition_effort_no_output(self):
        (self.agents / 'd.md').write_text('---\nname: d\neffort: high\n---\n')
        self.assertEqual(self.run_hook(agent_call('x ' + MARKER, subagent_type='d')), '')

    def test_real_agents_dir(self):
        env = dict(self.env)
        env.pop('SUBAGENT_RULES_AGENTS_DIR')
        real = Path('/home/node/.claude/agents/idea-scan.md')
        if not real.exists() or 'effort:' not in real.read_text():
            self.skipTest('idea-scan has no effort line')
        o = json.loads(self.run_hook(agent_call(subagent_type='idea-scan'), env=env))
        self.assertNotIn('effort', o['hookSpecificOutput']['updatedInput'])


class Passthrough(Base):
    def test_fork_untouched(self):
        self.assertEqual(self.run_hook(agent_call(subagent_type='fork')), '')

    def test_non_agent_tool_untouched(self):
        for tool, ti in (('Bash', {'command': 'ls', 'prompt': 'x'}), ('Task', {'prompt': 'x', 'description': 'y'}),
                         ('SendMessage', {'to': 'a', 'message': 'hi'})):
            self.assertEqual(self.run_hook({'tool_name': tool, 'tool_input': ti}), '', tool)

    def test_malformed_input_exits_0_silently(self):
        for bad in ('', 'not json', '{', '[]', '"str"', 'null', '{"tool_name":"Agent"}',
                    '{"tool_name":"Agent","tool_input":"x"}', '{"tool_name":"Agent","tool_input":{"prompt":5}}',
                    '{"tool_name":"Agent","tool_input":{"description":"no prompt"}}', b'\xff\xfe\x00garbage'):
            self.assertEqual(self.run_hook(bad), '', repr(bad))

    def test_odd_subagent_type_still_works(self):
        ui = self.updated(agent_call(subagent_type='../../etc/passwd'))
        self.assertEqual(ui['effort'], 'xhigh')


class FailOpen(Base):
    def copy_wrapper(self, py_body=None):
        d = self.tmp / 'hookdir'
        d.mkdir()
        shutil.copy(HOOK, d / 'subagent-rules.sh')
        if py_body is not None:
            (d / 'subagent_rules.py').write_text(py_body)
        return d / 'subagent-rules.sh'

    def test_missing_script(self):
        hook = self.copy_wrapper()
        self.assertEqual(self.run_hook(agent_call(), hook=hook), '')
        self.assertIn('ERROR wrapper', self.log.read_text())

    def test_crashing_script(self):
        hook = self.copy_wrapper('import sys\nprint("{partial")\nraise SystemExit(3)\n')
        self.assertEqual(self.run_hook(agent_call(), hook=hook), '')
        self.assertIn('exit 3', self.log.read_text())

    def test_hanging_script(self):
        hook = self.copy_wrapper('import time\ntime.sleep(30)\n')
        env = dict(self.env, SUBAGENT_RULES_TIMEOUT='1')
        t = time.time()
        self.assertEqual(self.run_hook(agent_call(), hook=hook, env=env), '')
        self.assertLess(time.time() - t, 5)
        self.assertIn('ERROR wrapper', self.log.read_text())

    def test_fast(self):
        t = time.time()
        for _ in range(5):
            self.run_hook(agent_call())
        self.assertLess((time.time() - t) / 5, 1.0)


class SettingsEntry(unittest.TestCase):
    def test_registered_on_agent(self):
        s = json.loads(SETTINGS.read_text())
        entries = [h for m in s['hooks']['PreToolUse'] if m.get('matcher') == 'Agent' for h in m['hooks']]
        cmds = [h for h in entries if h.get('command', '').endswith('/subagent-rules.sh')]
        self.assertEqual(len(cmds), 1, entries)
        self.assertTrue(os.path.isabs(cmds[0]['command']), cmds[0])
        self.assertEqual(Path(cmds[0]['command']).resolve(), HOOK.resolve())   # ~/.claude/hooks may be a symlink
        self.assertNotEqual(cmds[0].get('onFailure'), 'block')   # fail open
        self.assertFalse(cmds[0].get('async'))                   # must be sync to rewrite input
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == '__main__':
    unittest.main(verbosity=1)
