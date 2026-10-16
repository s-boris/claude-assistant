#!/usr/bin/python3 -I
"""vault-guard: PreToolUse tripwire for the privacy vault (level 1).

The owner's raw private data lives in the separate container claude-vault; this session only calls its API
(`vault ...`). This hook blocks tool calls that would reach around it:
  - tgread's local data or secrets (~/.local/share/tgread, ~/.secrets/tgread*) and `tgread|vault tg ... --raw`
  - the vault's data: `docker exec|cp|export|commit|attach claude-vault`, the volume claude-vault_data,
    `docker compose ... exec|cp|run` in the vault project, ssh/scp/rsync to /mnt/user/appdata/claude-vault,
    /var/lib/docker/volumes/claude-vault*, /proc/*/root, nsenter; also indirectly (indirect_vault_path): a command
    whose shell runs ssh/scp/rsync/sftp/sshfs and that reads the path out of ~/bin/vault or builds it from pieces
  - with VAULT_MODE on (env, else ~/.config/vault/mode): direct paperless access (port 8000 on his server: name and
    address from the private profile, network.server_name / server_host; port 8000 on ANY host if the profile is
    missing or broken, so a config failure can only add blocks;
    ~/.secrets/paperless.json and its retired copy); the `paperless` CLI itself goes through the vault then.
    One exact command (PL_RETIRE) may move the dead token into ~/attic/secrets-retired at 0600
  - the raw-release code channel: the bot token file (~/.claude/channels/telegram/.env) and Bot API calls that
    return or steal message contents (forwardMessage, copyMessage, editMessage*, getUpdates)
  - in a subagent (the hook payload carries agent_id): `vault|tgread|paperless ... --text|--content` (privacy plan A,
    2026-10-14: subagents get answers, never message text or document content; rule vault-text-subagent)
It only tightens what tools may do (permissionDecision "deny" + reason); it never allows anything. It is a
tripwire, not a sandbox: a script or obfuscated command can get around it, the vault's own boundary (separate
container, token hash, owner codes) is what actually protects the data. Blocks are logged in
~/.local/state/vault-guard/ by rule name only (never the command text).
Fails closed (2026-10-12): malformed input or an internal error blocks the call (exit 2, reason on stderr, logged
in errors.log); vault-guard.sh turns a crash or a hang into exit 2, and settings.json's "onFailure": "block" covers
a wrapper that can't start or times out. So a broken guard blocks EVERY guarded call, including the edit that would
fix it: change these files on a copy, run the tests there, then install the copy.
"""
import datetime as dt
import json
import os
import re
import sys

HOME = os.environ.get('VAULT_GUARD_HOME') or '/home/node'
STATE = os.path.join(HOME, '.local', 'state', 'vault-guard')
TG_DATA = os.path.join(HOME, '.local', 'share', 'tgread')
TG_ENV = os.path.join(HOME, '.claude', 'channels', 'telegram', '.env')
PL_SECRET = os.path.join(HOME, '.secrets', 'paperless.json')

SEP = r'(?:[;&|\n()`]|\$\()'                         # shell command separators

# ---- vault-host-path-indirect (2026-10-16): the vault's server dir reached without its literal path -------------
# where a command starts: after a separator (not open( in code), a keyword (then/do/!/find -exec/command, not
# command -v: a lookup) or a wrapper (xargs/sudo/env/timeout ... with its options), after variable assignments
_CMD_START = (r'(?:(?:^|[;&|\n`{]|(?<![\w.])\(|\$\()\s*|(?<![\w./-])(?:then|do|else|if|while|until|!|-exec|-execdir|'
              r'command(?:\s+-p)?)\s+|(?<![\w./-])(?:xargs|sudo|doas|env|nice|ionice|nohup|setsid|stdbuf|time|'
              r'timeout|exec|chronic|parallel|watch)(?:\s+(?:-[ugI]\s+(?!-)\S+|-(?![ugI]\s+(?!-)\S)\S*|\d\S*|\{\}))*\s+)'
              r'(?:\w+=\S*\s+)*')    # option forms are mutually exclusive: no exponential backtracking
REMOTE_CMD = re.compile(_CMD_START + r'(?:\w+=)?(?:[^\s;&|()`]*/)?(?:ssh|scp|rsync|sftp|sshfs|autossh)(?![\w.-])')
VAULT_SCRIPT = re.compile(r'(?<![\w.-])(?:[^\s;&|()`<>]*/)?bin/vault(?![\w.-])')        # ~/bin/vault, any mention
VAULT_SCRIPT_RUN = re.compile(r'(?:' + _CMD_START + r'|\s-c\s+)(?:[^\s;&|()`]*/)?bin/vault(?![\w.-])')  # run it
# the pieces as path parts: /appdata(/), appdata/..., A=appdata; /claude-vault(/), claude-vault/..., V=...claude-vault
# (never ~/opt/claude-vault, the local repo); a bare claude-vault counts after cd .../appdata or with appdata/$var
_ASSIGN = (r'(?:(?:^|[;&|\n`{]|(?<![\w.])\(|\$\()\s*(?:(?:export|local|readonly|declare|typeset)(?:\s+-\w+)*\s+)?'
           r'(?:\w+=\S*\s+)*\w+=)')
APPDATA_PATH = re.compile(r'(?<=/)appdata(?=[/\s;&|()`]|$)|(?<![\w./-])appdata/|' + _ASSIGN + r'appdata(?![\w.-])')
VAULT_PATH = re.compile(r'(?<=/)(?<!opt/)claude-vault(?=[/\s;&|()`*]|$)|(?<![\w./-])claude-vault/|' + _ASSIGN +
                        r'\S*(?<![\w.-])(?<!opt/)claude-vault(?![\w.-])')
VAULT_WORD = re.compile(r'(?<![\w./-])claude-vault(?![\w.-])')
APPDATA_CWD = re.compile(r'(?<![\w.-])(?:cd|pushd)\s+\S*appdata/?(?=[\s;&|)]|$)|appdata/\$')

_HEREDOC = re.compile(r'<<(-?)[ \t]*(?:\'([^\'\n]*)\'|"([^"\n]*)"|\\?([A-Za-z_][\w.-]*))')
_PLAIN = re.compile(r'[^\'"\\\n<#`()]+')
_DQ_PLAIN = re.compile(r'[^"\\$`]+')
_PAYLOAD = re.compile(r'(?:(?:^|[\s;&|(`{/])(?:(?:ba|da|z|k|mk|fi|tc|c|a|r)?sh|su)(?:\s+[^\s;&|()]+)*?\s+-[a-zA-Z]*c'
                      r'|(?:^|[\s;&|(`{])eval(?:\s+[^\s;&|()]+)*?)\s*$')


class _Shell:
    """shell_level's reader: just enough of bash's quoting for a tripwire (not a parser). Anything it can't follow
    stays in (too deep: the raw rest), so a misread only adds text to match."""
    MAX_DEPTH = 12

    def __init__(self, c):
        self.c, self.n = c, len(c)

    def scan(self, i, stop=None, depth=0):
        """Shell text from c[i] to the end, or to the `)` / backtick (stop) that closes a command substitution:
        (shell-level text, index after it)."""
        c, n, out, pend, par = self.c, self.n, [], [], 0
        if depth > self.MAX_DEPTH:
            return c[i:], n
        while i < n:
            m = _PLAIN.match(c, i)
            if m:
                out.append(m.group())
                i = m.end()
                continue
            ch = c[i]
            if (ch == ')' and stop == ')' and par == 0) or (ch == '`' and stop == '`'):
                return ''.join(out), i + 1
            if ch in '()':
                par = par + 1 if ch == '(' else max(par - 1, 0)
                out.append(ch)
                i += 1
            elif ch == '`':
                sub, i = self.scan(i + 1, '`', depth + 1)
                out.append(' `' + sub + '` ')
            elif ch == '\\':
                out.append(' ')
                i += 2
            elif ch == "'":
                j = c.find("'", i + 1)
                j = n if j < 0 else j
                out.append(self.word(c[i + 1:j], '', out, depth))
                i = j + 1
            elif ch == '"':
                kept, raw, i = self.dquote(i + 1, depth)
                out.append(self.word(raw, kept, out, depth))
            elif ch == '#' and (i == 0 or c[i - 1] in ' \t\n;&|()'):
                j = c.find('\n', i)                                   # a comment
                i = n if j < 0 else j
            elif ch == '<' and c.startswith('<<<', i):
                out.append('<<<')
                i += 3
            elif ch == '<' and _HEREDOC.match(c, i):
                hd = _HEREDOC.match(c, i)
                word = next(g for g in (hd.group(2), hd.group(3), hd.group(4), '') if g is not None)
                pend.append((hd.group(1) == '-', word, hd.group(4) is None))
                out.append(' ')
                i = hd.end()
            elif ch == '\n':
                out.append(ch)
                i += 1
                for strip, word, quoted in pend:
                    i = self.heredoc(i, strip, word, quoted, out, depth)
                pend = []
            else:
                out.append(ch)
                i += 1
        return ''.join(out), n

    def word(self, raw, kept, out, depth):
        """A quoted word: the payload of sh/bash -c or eval is shell again; otherwise only its command
        substitutions (kept) run here, the rest is data (a grep pattern, a message, code)."""
        if _PAYLOAD.search(''.join(out[-40:])[-300:]):
            return ' ' + _Shell(raw).scan(0, None, depth + 1)[0] + ' '
        return ' ' + kept + ' '

    def dquote(self, i, depth, closing=True):
        """Inside "..." (or an unquoted here-document: closing False): (its command substitutions, the text with
        the escapes removed, index after the closing quote)."""
        c, n, kept, raw = self.c, self.n, [], []
        while i < n:
            m = _DQ_PLAIN.match(c, i)
            if m:
                raw.append(m.group())
                i = m.end()
                continue
            ch, j = c[i], i
            if ch == '"' and closing:
                return ' '.join(kept), ''.join(raw), i + 1
            if ch == '\\':
                nx = c[i + 1:i + 2]
                raw.append(('' if nx == '\n' else nx) if nx and nx in '$`"\\\n' else '\\' + nx)
                i += 2
            elif c.startswith('$(', i) or ch == '`':
                sub, i = self.scan(i + (2 if ch == '$' else 1), ')' if ch == '$' else '`', depth + 1)
                kept.append('$(' + sub + ')')
                raw.append(c[j:i])
            else:
                raw.append(ch)
                i += 1
        return ' '.join(kept), ''.join(raw), n

    def heredoc(self, i, strip, word, quoted, out, depth):
        """Skips a here-document body from c[i] to its end line; an unquoted one keeps its command substitutions.
        No end line: the body runs to the end (as in bash). Returns the index after it."""
        c, n, start, end = self.c, self.n, i, None
        while i < n:
            j = c.find('\n', i)
            line = c[i:n if j < 0 else j]
            if (line.lstrip('\t') if strip else line) == word:
                end, i = i, (n if j < 0 else j + 1)
                break
            i = n if j < 0 else j + 1
        body = c[start:n if end is None else end]
        if not quoted and body:
            out.append(' ' + _Shell(body).dquote(0, depth + 1, closing=False)[0] + ' ')
        out.append('\n')
        return i


def shell_level(cmd):
    """What the local shell runs: quoted text, comments and here-document bodies blanked (data: grep patterns,
    patches, notes), command substitutions and sh -c / eval payloads kept."""
    return _Shell(cmd).scan(0)[0]


def indirect_vault_path(cmd):
    """Rule vault-host-path-indirect: the vault's server files reached without the literal path, in a command
    whose shell runs ssh/scp/rsync/sftp/sshfs: the path read out of ~/bin/vault (which holds it: grep/sed/cat/
    python on the script; running `vault ...` is fine), or put together from pieces (appdata + claude-vault as path
    parts: variables, globs, cd + a relative path). A lexer failure falls back to the whole text (stricter)."""
    try:
        sh = norm_command(shell_level(cmd))
    except Exception:
        sh = norm_command(cmd)
    if not REMOTE_CMD.search(sh):
        return False
    s = norm_command(cmd)
    runs = {m.end() for m in VAULT_SCRIPT_RUN.finditer(s)}
    if any(m.end() not in runs for m in VAULT_SCRIPT.finditer(s)):
        return True
    return bool(APPDATA_PATH.search(s) and (VAULT_PATH.search(s) or (VAULT_WORD.search(s) and APPDATA_CWD.search(s))))


BASH_RULES = [
    ('tgread-data', re.compile(r'\.local/share/tgread'),
     'tgread\'s data lives in the privacy vault now; use `vault tg ...` (filtered) instead'),
    ('tgread-secrets', re.compile(r'\.secrets/tgread'),
     'tgread\'s session and credentials live in the privacy vault only'),
    ('tgread-raw', re.compile(r'\b(?:tgread|vault\s+tg)\b[^;&|\n]*?(?<![\w-])--raw\b'),
     'raw Telegram text only through the owner\'s code: `vault raw request --source tg ...`'),
    ('vault-docker', re.compile(r'\bdocker\s+(?:container\s+)?(?:exec|cp|export|commit|attach|debug|checkpoint)\b'
                                r'[^;&|\n]*claude-vault'),
     'no shell or file access into the vault container; use the vault API (`vault ...`)'),
    ('vault-volume', re.compile(r'claude-vault_data|volumes-from[= ]\S*claude-vault|/var/lib/docker/volumes/claude-vault'),
     'the vault\'s data volume is off limits; use the vault API (`vault ...`)'),
    # compose must be the command (not a file name like docker-compose.yml) and the subcommand a standalone token
    # (not run-all.sh); docker's global flags (--context x) may sit between docker and compose (2026-10-13)
    ('vault-compose', re.compile(r'(?=.*claude-vault).*(?<![\w.-])docker(?:\s+-{1,2}[\w-]+(?:[= ]\S+)?)*'
                                 r'(?:\s+compose|-compose)(?=\s)[^;&|\n]*?(?<![\w./-])(?:exec|cp|run|attach|debug)'
                                 r'(?![\w./-])', re.S),
     'no shell or file access into the vault container; use the vault API (`vault ...`)'),
    ('vault-host-path', re.compile(r'\b(?:ssh|scp|rsync|sftp|sshfs)\b.*(?:/mnt/user/appdata/claude-vault'
                                   r'|/mnt/[\w-]+/appdata/claude-vault|/proc/[^/\n]{1,60}/root\b|\bnsenter\b)', re.S),
     'the vault\'s files on the server are off limits; manage it with `vault admin ...`'),
    # released media files (vault media release) stay on this box: no network tool may touch them (2026-10-13)
    ('vault-media-egress', re.compile(r'(?=[\s\S]*\.cache/vault/media)[\s\S]*(?<![\w./-])(?:curl|wget|scp|rsync|sftp|'
                                      r'ssh|nc|ncat|netcat|socat|gh|ftp|tftp|telnet|rclone|aws|gsutil|sendmail|mutt|'
                                      r'swaks)(?![\w./-])'),
     'released media files stay on this box (deleted after 2 h); no network tool on ~/.cache/vault/media'),
    ('telegram-token', re.compile(r'channels/telegram/\.env'),
     'the bot token stays with the Telegram plugin and `tg`; the vault\'s code messages must stay unreadable to me'),
    ('telegram-read-api', re.compile(r'api\.telegram\.org\S*?/(?:forwardMessages?|copyMessages?|editMessage\w*|'
                                     r'getUpdates|stopPoll)\b|\b(?:forwardMessages?|copyMessages?|editMessage\w*|'
                                     r'getUpdates)\b[^;&|\n]*api\.telegram\.org', re.I),
     'Bot API calls that read or forward message contents (or steal updates) are blocked: they could expose the '
     'vault\'s owner codes; use the telegram plugin tools'),
]
# matchers on the raw command (they read its quoting), evaluated last: a command another rule blocks keeps that name
INDIRECT_RULES = [
    ('vault-host-path-indirect', indirect_vault_path,
     'the vault\'s files on the server are off limits, also through a path read out of ~/bin/vault or put together '
     'from pieces (variables, globs, cd + a relative path); manage it with `vault admin ...`'),
]
# subagents (agent_id in the payload) never ask the vault for text or content (privacy plan A, 2026-10-14); --no-text
# and paths like claude-vault or vault-events don't count
SUBAGENT_RULES = [
    ('vault-text-subagent', re.compile(r'(?<![\w.-])(?:vault|tgread|paperless)(?![\w.-])[^;&|\n]*?(?<![\w-])'
                                       r'--(?:text|content)(?![\w-])'),
     'subagents never get message text or document content from the vault (privacy plan A, 14.10): ask it a question '
     'instead (`vault ask`, `tgread summarize` = a digest, `paperless get` = fields); text only in the main session, '
     'on the owner\'s explicit request and with his OK'),
]
# a recursive search whose root contains the bot token file prints its line for patterns like TOKEN/telegram
_ANCESTOR = (r'(?<![\w./~-])(?:/|/home/?|/home/node/?|/home/node/\.claude/?|/home/node/\.claude/channels/?'
             r'|/home/node/\.claude/channels/telegram/?)(?=[\s;&|)]|$)')
RECURSIVE_SEARCH = [
    ('telegram-token', re.compile(r'\b(?:grep|egrep|fgrep)\b(?=[^;&|\n]*\s(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|'
                                  r'--dereference-recursive)\b)[^;&|\n]*' + _ANCESTOR),
     'this recursive search includes the bot token file (~/.claude/channels/telegram/.env); search a narrower '
     'directory'),
    ('telegram-token', re.compile(r'\b(?:rg|ag)\b(?=[^;&|\n]*\s(?:--hidden|-[a-zA-Z]*u[a-zA-Z]*|-\.|--unrestricted)'
                                  r'(?=\s|$))[^;&|\n]*' + _ANCESTOR),
     'this search includes hidden files and the bot token file (~/.claude/channels/telegram/.env); search a '
     'narrower directory'),
]
PROFILE = os.environ.get('ASSISTANT_PROFILE') or os.path.join(HOME, '.config', 'assistant', 'profile.json')


def paperless_rules():
    """The paperless-direct rule: port 8000 on his server (profile network.server_host, and network.server_name with
    or without .local) or the local token file's name. The profile is read only here, when VAULT_MODE needs the
    rule. Without both keys (no file, a broken one, a key missing, empty or not a string) port 8000 on ANY host
    counts, so a config failure can only add blocks."""
    host = name = None
    try:
        if os.path.isfile(PROFILE) and os.path.getsize(PROFILE) <= 1_000_000:
            with open(PROFILE, encoding='utf-8') as f:
                net = json.load(f).get('network') or {}
            host, name = net.get('server_host'), net.get('server_name')
    except Exception:                                  # unreadable, not JSON, not an object: the fallback
        host = name = None
    token = re.escape(os.path.basename(PL_SECRET))
    if isinstance(host, str) and host.strip() and isinstance(name, str) and name.strip():
        rx = rf'{re.escape(host)}:8000|\b{re.escape(name.lower())}(?:\.local)?:8000|{token}'
    else:
        rx = rf':8000|{token}'
    return [('paperless-direct', re.compile(rx),
             'VAULT_MODE is on: paperless only through the vault (`paperless ...` or `vault paperless ...`)')]


# The one sanctioned way to retire the dead local paperless token (rotated; the vault has its own):
# move it, unread, into ~/attic/secrets-retired at 0600. Only this exact whole command (raw text, absolute paths,
# no quotes, escapes, variables or extra commands) is exempt from paperless-direct; nothing else changes (2026-10-13).
PL_RETIRED_DIR = os.path.join(HOME, 'attic', 'secrets-retired')
PL_RETIRED = os.path.join(PL_RETIRED_DIR, 'paperless.json')
PL_RETIRE = re.compile(re.escape(f'mkdir -p -m 700 {PL_RETIRED_DIR} && mv -n {PL_SECRET} {PL_RETIRED} '
                                 f'&& chmod 600 {PL_RETIRED}'))


def vault_mode_on():
    v = os.environ.get('VAULT_MODE')
    if v is None:
        try:
            with open(os.path.join(HOME, '.config', 'vault', 'mode'), encoding='utf-8') as f:
                v = f.read()
        except OSError:
            v = ''
    return v.strip().lower() in ('on', '1', 'true', 'yes')


def norm_command(cmd):
    s = cmd.replace('\\\n', ' ')
    s = s.replace('"', '').replace("'", '').replace('\\', '')
    s = re.sub(r'\$\{HOME\}|\$HOME\b|(?<![\w/.~-])~(?=/|\s|$)', HOME, s)
    s = re.sub(r'/{2,}', '/', s)
    for _ in range(10):
        s2 = re.sub(r'/\./', '/', s)
        s2 = re.sub(r'/(?!\.\.?/)[^/\s;&|]+/\.\./', '/', s2)
        if s2 == s:
            break
        s = s2
    return s


def norm_path(p):
    p = p.replace('${HOME}', HOME).replace('$HOME', HOME)
    if p.startswith('~'):
        p = HOME + p[1:]
    p = os.path.normpath(p if os.path.isabs(p) else os.path.join(os.getcwd(), p))
    return p, os.path.realpath(p)


def inside(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def check_bash(cmd, subagent=False):
    s = norm_command(cmd)
    retire = PL_RETIRE.fullmatch(cmd.strip()) is not None
    rules = BASH_RULES + RECURSIVE_SEARCH + (paperless_rules() if vault_mode_on() and not retire else []) + \
        (SUBAGENT_RULES if subagent else [])
    for name, rx, why in rules:
        if rx.search(s):
            return name, why
    for name, match, why in INDIRECT_RULES:
        if match(cmd):
            return name, why
    return None


def check_path(tool, p, search_root=False):
    if not isinstance(p, str) or not p:
        return None
    for cand in norm_path(p):
        if inside(cand, TG_DATA) or (search_root and inside(TG_DATA, cand) and os.path.exists(TG_DATA)
                                     and cand != '/' and inside(cand, os.path.join(HOME, '.local'))):
            return 'tgread-data', 'tgread\'s data lives in the privacy vault now; use `vault tg ...` (filtered)'
        if os.path.basename(cand).startswith('tgread') and os.path.dirname(cand) == os.path.join(HOME, '.secrets'):
            return 'tgread-secrets', 'tgread\'s session and credentials live in the privacy vault only'
        if tool in ('Read', 'Grep', 'Glob') and cand == TG_ENV:
            return 'telegram-token', 'the bot token stays with the Telegram plugin and `tg`'
        if tool == 'Grep' and search_root and inside(TG_ENV, cand):      # Grep reads hidden files too
            return 'telegram-token', ('this search would include the bot token file '
                                      '(~/.claude/channels/telegram/.env); search a narrower directory')
        if cand in (PL_SECRET, PL_RETIRED) and vault_mode_on():
            return 'paperless-direct', 'VAULT_MODE is on: paperless only through the vault'
    return None


def decide(event):
    tool = event.get('tool_name') or ''
    ti = event.get('tool_input') or {}
    if tool in ('Bash', 'Monitor'):
        return check_bash(str(ti.get('command') or ''), subagent=event.get('agent_id') is not None)
    if tool == 'Read':
        return check_path(tool, ti.get('file_path'))
    if tool in ('Grep', 'Glob'):
        for key in ('path', 'pattern', 'glob'):
            v = ti.get(key)
            if key == 'path' and v:
                r = check_path(tool, v, search_root=True)
            elif key != 'path' and isinstance(v, str) and ('/' in v or v.startswith('~')):
                r = check_bash(v)                      # absolute patterns/globs: same text rules as Bash
            else:
                r = None
            if r:
                return r
        if not ti.get('path'):                         # default root: the current directory
            return check_path(tool, os.getcwd(), search_root=True)
        return None
    if tool in ('Edit', 'Write', 'MultiEdit'):
        return check_path(tool, ti.get('file_path'))
    if tool == 'NotebookEdit':
        return check_path(tool, ti.get('notebook_path'))
    return None


def log(kind, line):
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        with open(os.path.join(STATE, kind + '.log'), 'a', encoding='utf-8') as f:
            f.write(f'{dt.datetime.now().isoformat(timespec="seconds")} {line}\n')
    except OSError:
        pass


def fail(why):
    """Fail closed: on PreToolUse, exit 2 makes Claude Code block the tool call and show stderr as the reason."""
    sys.stderr.write(f'vault-guard: {why}; tool call blocked (fail closed). If every call is blocked, the guard '
                     'itself is broken: tell the owner; fix from a container shell: brain log -- .claude/hooks, '
                     'then brain restore .claude/hooks REV.\n')
    return 2


def main():
    try:
        try:
            event = json.loads(sys.stdin.read())
        except ValueError:                             # empty, not JSON, or not UTF-8: blocked just below
            event = None
        if not (isinstance(event, dict) and isinstance(event.get('tool_name'), str) and event['tool_name']
                and isinstance(event.get('tool_input'), dict)):
            log('errors', 'malformed hook input')
            return fail('malformed hook input')
        hit = decide(event)
        if hit:
            name, why = hit
            log('blocked', f'{event["tool_name"]} rule={name}')
            print(json.dumps({'hookSpecificOutput': {
                'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': f'vault-guard ({name}): {why}. See the vault skill.'}}))
        return 0
    except Exception as e:                             # fail closed: a guard bug must not let calls through
        log('errors', f'{type(e).__name__} at line {e.__traceback__.tb_lineno if e.__traceback__ else "?"}')
        return fail(f'internal error ({type(e).__name__})')


if __name__ == '__main__':
    sys.exit(main())
