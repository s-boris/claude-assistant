#!/usr/bin/python3 -I
"""PreToolUse hook logic for the Agent tool (wrapper: subagent-rules.sh; tests: test_subagent_rules.py; 2026-10-15).

Two defaults for every subagent launch, so I can't forget them in a prompt:
  1. appends RULES (after a blank line) when the prompt lacks the MARKER line "[subagent-rules]";
  2. sets effort "xhigh" when the call has none, unless the agent's own definition sets an effort
     (~/.claude/agents/*.md or <cwd>/.claude/agents/*.md frontmatter, e.g. idea-scan: high):
     in CC 2.1.300 a call-level effort overrides the definition's.
Skips subagent_type "fork" (a fork inherits my context and effort) and every other tool.

Output: hookSpecificOutput.updatedInput = the FULL tool input with the two changes (CC validates it against the
Agent input schema as a replacement, not a merge) and permissionDecision "allow". Nothing to change = no output.
FAILS OPEN: any problem prints nothing and exits 0, so the Agent call runs unchanged.
"""
import json
import os
import re
import sys
from pathlib import Path

MARKER = '[subagent-rules]'
RULES = """[subagent-rules]
- Never launch an interactive `claude` (one hung 30 min). Never send Telegram messages.
- Read files in parts (grep, head, offsets); never dump whole large files or transcripts.
- No waits over 4 min. If the task passes ~100 tool calls, stop and report so it can be split.
- Edit live ~/bin scripts on a copy; install with an atomic `mv` after tests pass.
- Never print config/env/template files holding secrets: grep key names only or pipe through `secretscan --redact`.
- Bash `rm` whose target Claude Code can't resolve (all variables or a variable glob: `rm -rf $D/$x`, `rm -f $T/*`; or a relative glob after `cd`: `cd X; rm -rf out/*`) waits for an approval nobody gives: the call hangs forever. Use literal absolute paths (`rm -rf ~/jobs/x/out/*`), `"${D:?}"/"$x"`, or `find /abs/dir -mindepth 1 -delete`.
- Bound every wait loop (`timeout 230 sh -c '...'` or a counter). Never `pgrep -f PATTERN` with PATTERN in your own command (it matches the loop's own shell and never ends): use `pgrep -f '[p]attern'`.
- Private data: ask the vault questions (vault ask, cards); never --text/--content. If vault-guard or risk-check blocks something, report it; never work around it, not even by switching tools."""
DEFAULT_EFFORT = 'xhigh'
AGENT_DIRS = [Path(os.environ.get('SUBAGENT_RULES_AGENTS_DIR', '/home/node/.claude/agents'))]
NAME_OK = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')


def frontmatter(path):
    """{key: value} of a markdown file's leading --- block (first 60 lines; flat keys only)."""
    out = {}
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = [next(f, '') for _ in range(60)]
    if not lines or lines[0].strip() != '---':
        return out
    for line in lines[1:]:
        if line.strip() == '---':
            break
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$', line)
        if m:
            out[m.group(1)] = m.group(2).strip('\'"')
    return out


def definition_sets_effort(agent_type, cwd):
    """True when a user/project agent definition named agent_type has an effort: line."""
    if not isinstance(agent_type, str) or not NAME_OK.match(agent_type):
        return False
    dirs = list(AGENT_DIRS)
    if isinstance(cwd, str) and cwd.startswith('/'):
        dirs.append(Path(cwd) / '.claude' / 'agents')
    for d in dirs:
        try:
            files = sorted(d.glob('*.md'))
        except OSError:
            continue
        for p in files:
            try:
                fm = frontmatter(p)
            except OSError:
                continue
            if (fm.get('name') or p.stem) == agent_type and fm.get('effort'):
                return True
    return False


def decide(payload):
    """The hook's JSON output as a dict, or None for no change."""
    if not isinstance(payload, dict) or payload.get('tool_name') != 'Agent':
        return None
    ti = payload.get('tool_input')
    if not isinstance(ti, dict):
        return None
    prompt = ti.get('prompt')
    if not isinstance(prompt, str):
        return None
    agent_type = ti.get('subagent_type')
    if agent_type == 'fork':
        return None
    new = dict(ti)
    did = []
    if MARKER not in prompt:
        new['prompt'] = (prompt.rstrip('\n') + '\n\n' + RULES) if prompt.strip() else RULES
        did.append('rules appended')
    if ti.get('effort') in (None, ''):
        try:
            own = definition_sets_effort(agent_type, payload.get('cwd'))
        except Exception:
            own = True          # unsure: leave effort alone
        if not own:
            new['effort'] = DEFAULT_EFFORT
            did.append('effort ' + DEFAULT_EFFORT)
    if not did:
        return None
    return {'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow',
        'permissionDecisionReason': 'subagent-rules: ' + ', '.join(did),
        'updatedInput': new,
    }}


def main():
    try:
        out = decide(json.loads(sys.stdin.read()))
    except Exception:
        return 0
    if out is not None:
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
