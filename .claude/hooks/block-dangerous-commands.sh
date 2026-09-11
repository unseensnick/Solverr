#!/usr/bin/env bash
# Blocks dangerous shell commands: push to protected branches, force push,
# destructive operations. PreToolUse hook for Bash and PowerShell operations.
#
# Both tools must be matched in settings.json. The PowerShell tool is a
# separate tool from Bash, so a matcher of "Bash" alone leaves every guard
# here bypassable by rewriting the command in PowerShell. Both tools carry the
# command in .tool_input.command, so the parsing below is shared, but the
# destructive spellings are not: see the PowerShell section.
#
# Exit 2 = block. Exit 0 = allow.
#
# Configurable via env:
#   CLAUDE_PROTECTED_BRANCHES  comma list (default: derived from git + main,master)

set -uo pipefail

emit_deny() {
  # Emit a JSON deny decision and exit 2.
  local reason="${1//\"/\\\"}"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$reason"
  exit 2
}

if ! command -v jq >/dev/null 2>&1; then
  emit_deny "jq is required for command protection hooks but is not installed."
fi

INPUT=$(cat)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
[ -z "$COMMAND" ] && exit 0

# ── Protected branch list ────────────────────────────────────────────────
DEFAULT_BRANCHES="main,master"
if GIT_DEFAULT=$(git config --get init.defaultBranch 2>/dev/null) && [ -n "$GIT_DEFAULT" ]; then
  DEFAULT_BRANCHES="$DEFAULT_BRANCHES,$GIT_DEFAULT"
fi
PROTECTED_BRANCHES="${CLAUDE_PROTECTED_BRANCHES:-$DEFAULT_BRANCHES}"
# Build a regex alternation: main|master|develop|...
BR_REGEX=$(printf '%s' "$PROTECTED_BRANCHES" | tr ',' '\n' | awk 'NF{printf "%s%s",sep,$0; sep="|"}')

contains_cmd() { printf '%s' "$COMMAND" | grep -qE "$1"; }
contains_icmd() { printf '%s' "$COMMAND" | grep -qiE "$1"; }

# ── Git push protections ────────────────────────────────────────────────
# The session's cwd, which is where the command actually runs. The hook's own cwd is always the
# project dir, so without this a push from a worktree is judged by the main tree's branch.
SESSION_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
if contains_cmd '(^|[;&|()]+[[:space:]]*)git[[:space:]]+push'; then
  # Explicit refspec to a protected branch (origin main, :main, HEAD:main, remote branch)
  if contains_cmd "git[[:space:]]+push[[:space:]]+[^[:space:]]+[[:space:]]+([^[:space:]]*:)?($BR_REGEX)(\$|[[:space:]])"; then
    MATCHED_BRANCH=$(printf '%s' "$COMMAND" | grep -oE "($BR_REGEX)(\$|[[:space:]])" | head -1 | tr -d '[:space:]')
    emit_deny "Blocked: push to protected branch '${MATCHED_BRANCH:-main}'. Use a feature branch and open a PR."
  fi
  if contains_cmd "git[[:space:]]+push.*:($BR_REGEX)(\$|[[:space:]])"; then
    MATCHED_BRANCH=$(printf '%s' "$COMMAND" | grep -oE ":($BR_REGEX)(\$|[[:space:]])" | head -1 | tr -d ': [:space:]')
    emit_deny "Blocked: push to protected branch '${MATCHED_BRANCH:-main}' via refspec. Use a feature branch and open a PR."
  fi
  # Bare `git push` while on protected branch
  if contains_cmd 'git[[:space:]]+push[[:space:]]*($|[;&|])'; then
    CURRENT=$(git -C "${SESSION_CWD:-.}" branch --show-current 2>/dev/null || git branch --show-current 2>/dev/null || true)
    if [ -n "$CURRENT" ] && printf '%s' ",$PROTECTED_BRANCHES," | grep -q ",$CURRENT,"; then
      emit_deny "Blocked: you are on '$CURRENT' (a protected branch). Switch to a feature branch."
    fi
  fi
  # Force push (but allow --force-with-lease)
  if contains_cmd 'git[[:space:]]+push([[:space:]]+[^[:space:]]+)*[[:space:]]+(-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]=]|$)' \
     && ! contains_cmd '\-\-force-with-lease'; then
    emit_deny "Blocked: force push is not allowed. Use --force-with-lease if you must overwrite remote."
  fi
fi

# ── Merging is never the agent's call ───────────────────────────────────
# Every PR is merged by a person, with a merge commit. To GitHub a PR merge is a legitimate
# action, so no ruleset can express this; the matcher here is the only guard.
if contains_cmd '(^|[;&|()]+[[:space:]]*)gh[[:space:]]+pr[[:space:]]+merge'; then
  emit_deny "Blocked: merging a PR is the owner's call. Open the PR and stop."
fi
if contains_cmd 'gh[[:space:]]+api[^;&|]*pulls/[0-9]+/merge'; then
  emit_deny "Blocked: merging a PR through the API is the owner's call. Open the PR and stop."
fi

# ── Destructive filesystem operations ───────────────────────────────────
# rm -rf targeting root, home, $HOME, $VAR (any unresolved expansion), or parent traversal.
# We normalise quotes before matching so "my folder", '$HOME/trash', etc. Are all inspected.
CMD_NOQUOTE=$(printf '%s' "$COMMAND" | tr -d "'\"")
if printf '%s' "$CMD_NOQUOTE" | grep -qE 'rm[[:space:]]+(-[a-zA-Z]*[[:space:]]+)*-?[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*[[:space:]]+(/([[:space:]]|\*|$)|~|\$HOME|\$[A-Za-z_][A-Za-z0-9_]*|\.\./\.\.)' ; then
  emit_deny "Blocked: recursive force-delete on /, ~, \$HOME, an unresolved \$VAR, or .../.. Path. Specify a concrete safe target."
fi
# rm -rf /usr, /etc, /var, /bin, etc.
if printf '%s' "$CMD_NOQUOTE" | grep -qE 'rm[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*-?[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*[[:space:]]+/(usr|etc|var|bin|sbin|lib|opt|root|boot)([[:space:]/]|$)'; then
  emit_deny "Blocked: recursive delete targeting a system directory."
fi

# ── Secret files are never read through the shell ───────────────────────
# The permissions deny list covers the Read, Write and Edit tools only, and auto mode routes file
# reads through the shell instead, so `cat .env` walks straight past it. Only this hook sees the
# command text. The reader must sit in command position (line start or after an operator):
# several reader names are ordinary English words, and matching them mid-sentence rejected
# commit messages.
SECRET_READERS='cat|head|tail|sed|awk|grep|rg|less|more|strings|xxd|od|base64|cp|mv|scp|curl|type|gc|Get-Content|Select-String|Copy-Item'
SECRET_TARGETS='(^|[[:space:]=/\\])\.env([[:space:]./]|$)|\.(pem|key|p12|pfx|jks|keystore)([[:space:]]|$)|(^|[[:space:]=/\\])secrets[/\\]|id_rsa|deny-names\.local'
if printf '%s' "$CMD_NOQUOTE" | grep -qiE "(^|[;&|(])[[:space:]]*($SECRET_READERS)[[:space:]]" \
   && printf '%s' "$CMD_NOQUOTE" | grep -qE "$SECRET_TARGETS"; then
  emit_deny "Blocked: that reads a secret file (a .env, a key or certificate, secrets/, or the local deny list). Open it yourself if you need its contents."
fi

# ── PowerShell destructive operations ───────────────────────────────────
# PowerShell has its own spelling for everything above and the POSIX patterns
# see none of it. Parameter names may be truncated (-Recurse accepts -Rec), so
# the recurse switch is matched loosely.
PS_DEL='(^|[;&|(){}[:space:]])(Remove-Item|Remove-ItemProperty|ri|rmdir|rd|del|erase)[[:space:]]'
PS_ROOT='([A-Za-z]:\\?([[:space:]*]|$)|~([[:space:]\\/]|$)|\$HOME|\$env:USERPROFILE|\$env:HOMEDRIVE|\$env:SystemRoot|\$[A-Za-z_][A-Za-z0-9_]*([[:space:]]|$)|\.\.[\\/]\.\.)'
PS_SYSDIR='[A-Za-z]:\\(Windows|Program Files|Program Files \(x86\)|Users|ProgramData)([[:space:]\\]|$)'

if printf '%s' "$CMD_NOQUOTE" | grep -qiE "$PS_DEL"; then
  if printf '%s' "$CMD_NOQUOTE" | grep -qiE -- '-Rec(u|ur|urs|urse)?([[:space:]]|$)' \
     && printf '%s' "$CMD_NOQUOTE" | grep -qiE "$PS_ROOT"; then
    emit_deny "Blocked: recursive PowerShell delete on a drive root, home, or unresolved \$variable. Specify a concrete safe target."
  fi
  if printf '%s' "$CMD_NOQUOTE" | grep -qiE "$PS_SYSDIR"; then
    emit_deny "Blocked: PowerShell delete targeting a system directory."
  fi
fi

# Disk and volume destruction.
if contains_icmd '(^|[;&|(){}[:space:]])(Format-Volume|Clear-Disk|Remove-Partition|Initialize-Disk|Set-Partition)([[:space:]]|$)'; then
  emit_deny "Blocked: PowerShell disk or volume operation. Irreversible data loss."
fi

# Download piped into execution, the iwr | iex form of curl | bash.
if contains_icmd '(Invoke-WebRequest|Invoke-RestMethod|iwr|irm|curl|wget)[^|]*\|[[:space:]]*(Invoke-Expression|iex)([[:space:]]|$)'; then
  emit_deny "Blocked: piping downloaded content into Invoke-Expression is dangerous."
fi

# Security settings and machine state.
if contains_icmd '(^|[;&|(){}[:space:]])Set-ExecutionPolicy([[:space:]]|$)'; then
  emit_deny "Blocked: changing the PowerShell execution policy is a security settings change."
fi
if contains_icmd '(^|[;&|(){}[:space:]])(Stop-Computer|Restart-Computer)([[:space:]]|$)'; then
  emit_deny "Blocked: shutting down or restarting the machine."
fi
# Registry writes under HKLM, which is machine-wide configuration.
if contains_icmd '(Remove-Item|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty)[^|;]*HKLM:'; then
  emit_deny "Blocked: writing to HKLM is a machine-wide system settings change."
fi

# ── Dangerous database operations ───────────────────────────────────────
# DROP TABLE|DATABASE|SCHEMA
if contains_icmd 'DROP[[:space:]]+(TABLE|DATABASE|SCHEMA)[[:space:]]+'; then
  emit_deny "Blocked: DROP TABLE/DATABASE/SCHEMA detected. Run manually if intended."
fi
# DELETE FROM without a WHERE on the SAME statement.
# Split on ';' so multi-statement inputs are analysed per-statement.
if printf '%s\n' "$COMMAND" | awk '
  BEGIN { IGNORECASE=1; RS=";" }
  /DELETE[[:space:]]+FROM[[:space:]]+[A-Za-z_][A-Za-z0-9_.]*/ {
    if ($0 !~ /WHERE/) { print "BAD"; exit }
  }
' | grep -q BAD; then
  emit_deny "Blocked: DELETE FROM without a WHERE clause. Add a WHERE or run manually."
fi
if contains_icmd 'TRUNCATE[[:space:]]+TABLE'; then
  emit_deny "Blocked: TRUNCATE TABLE detected. Run manually if intended."
fi

# ── Dangerous system commands ───────────────────────────────────────────
# chmod: any world-writable/universal mode (0?777 or a+rwx)
if contains_cmd 'chmod([[:space:]]+-[a-zA-Z]+)*[[:space:]]+0?777([[:space:]]|$)' \
  || contains_cmd 'chmod([[:space:]]+-[a-zA-Z]+)*[[:space:]]+a\+rwx([[:space:]]|$)'; then
  emit_deny "Blocked: chmod 777 / a+rwx grants everyone full access. Use restrictive perms."
fi

# curl/wget piped to a shell
if contains_cmd '(curl|wget)[[:space:]].*\|[[:space:]]*(sudo[[:space:]]+)?(bash|sh|zsh|ksh|fish|dash|csh)([[:space:]]|$)'; then
  emit_deny "Blocked: piping downloaded content directly to a shell is dangerous."
fi

# Disk / partition. Note: only REDIRECTIONS to /dev/ are destructive. `2>/dev/null` is not.
# Pattern matches: `>[ ]*/dev/<something>` but NOT `2>/dev/null` or `&>/dev/null` style for fd-null.
# Strategy: delete the harmless redirects first, then match `>` followed by /dev/<name> on what is
# left. Excluding them on the whole command failed both ways: `>/dev/null;` read as unsafe because
# the exclusion wanted whitespace after it, and one safe redirect cleared a dangerous one later on.
CMD_SANS_SAFE=$(printf '%s' "$COMMAND" | sed -E 's#>[[:space:]]*/dev/(null|stdout|stderr|tty|zero|random|urandom)##g')
if printf '%s' "$CMD_SANS_SAFE" | grep -qE '(^|[^0-9&])>[[:space:]]*/dev/[a-zA-Z][a-zA-Z0-9]*' ; then
  emit_deny "Blocked: redirection into a raw device file can destroy data."
fi
if contains_cmd '(^|[;&|[:space:]])(mkfs|mkfs\.[a-z0-9]+)([[:space:]]|$)' \
  || contains_cmd '(^|[;&|[:space:]])dd[[:space:]]+[^|]*(if|of)=/dev/[a-zA-Z]' ; then
  emit_deny "Blocked: mkfs/dd against a device node. Irreversible data loss."
fi

# ── Destructive git ─────────────────────────────────────────────────────
if contains_cmd 'git[[:space:]]+reset[[:space:]]+--hard'; then
  emit_deny "Blocked: git reset --hard discards uncommitted changes permanently."
fi
if contains_cmd 'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'; then
  emit_deny "Blocked: git clean -f permanently deletes untracked files."
fi

# ── Accidental package publishing ───────────────────────────────────────
# Allow --dry-run variants (npm publish --dry-run is safe and common in CI).
publish_patterns=(
  '(npm|yarn|pnpm|bun)[[:space:]]+publish'
  'cargo[[:space:]]+publish'
  'gem[[:space:]]+push'
  'twine[[:space:]]+upload'
)
for pat in "${publish_patterns[@]}"; do
  if contains_cmd "$pat" && ! contains_cmd '(^|[[:space:]])(--dry-run|-n)([[:space:]=]|$)'; then
    emit_deny "Blocked: publishing packages should run in CI or manually, not via Claude."
  fi
done

exit 0
