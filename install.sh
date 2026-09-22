#!/bin/sh
set -eu
WORKLOG_SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKLOG_INSTALL="$HOME/Library/Application Support/Worklog/app"
WORKLOG_SKILL="${CODEX_HOME:-$HOME/.codex}/skills/worklog"
python3 -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"'
command -v git >/dev/null
mkdir -p "$WORKLOG_INSTALL/worklog" "$WORKLOG_INSTALL/bin" "$HOME/.local/bin"
cp "$WORKLOG_SOURCE"/worklog/*.py "$WORKLOG_INSTALL/worklog/"
cp "$WORKLOG_SOURCE/bin/worklog" "$WORKLOG_INSTALL/bin/worklog"
chmod +x "$WORKLOG_INSTALL/bin/worklog"
if [ -e "$HOME/.local/bin/worklog" ] && [ ! -L "$HOME/.local/bin/worklog" ]; then
  echo 'An existing ~/.local/bin/worklog file was preserved; use the installed app/bin/worklog directly.'
else
  ln -sfn "$WORKLOG_INSTALL/bin/worklog" "$HOME/.local/bin/worklog"
fi
if [ -e "$WORKLOG_SKILL/SKILL.md" ] && ! cmp -s "$WORKLOG_SOURCE/skills/worklog/SKILL.md" "$WORKLOG_SKILL/SKILL.md"; then
  echo 'Existing worklog skill differs; preserved it. Review before updating.'
else
  mkdir -p "$WORKLOG_SKILL"
  cp "$WORKLOG_SOURCE/skills/worklog/SKILL.md" "$WORKLOG_SKILL/SKILL.md"
fi
echo 'Installed. Run ~/.local/bin/worklog --help. No project data or credentials have been uploaded.'
