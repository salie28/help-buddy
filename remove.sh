#!/usr/bin/env bash
# Help Buddy uninstaller.
#   ./remove.sh          remove the command link, app entry, shortcut, autostart and blur entry
#   ./remove.sh --purge  also delete settings, history, log and saved Claude sessions
# Screenshots are never deleted.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

purge=()
case "${1:-}" in
  "") ;;
  --purge) purge=(--purge) ;;
  -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1 (use --purge or --help)" >&2; exit 2 ;;
esac

# Where screenshots live (read before --purge deletes the settings).
shots=$(/usr/bin/python3 -c "import sys; sys.path.insert(0, '.'); from helpbuddy import config; print(config.load_settings().screenshot_dir)" 2>/dev/null || true)

# Stop a running Help Buddy first.
gdbus call --session --dest local.claude.HelpBuddy --object-path /local/claude/HelpBuddy \
  --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1 || true

./bin/helpbuddy --uninstall "${purge[@]}"

if [[ -n "$shots" && -d "$shots" ]]; then echo "Screenshots were kept in: $shots"; fi
echo "Help Buddy is removed. The repo folder itself is untouched."
