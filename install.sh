#!/usr/bin/env bash
# Help Buddy installer: checks dependencies, runs the tests, then links the `helpbuddy`
# command, adds the app entry + global shortcut, and starts Help Buddy in the background.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
REPO=$PWD

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] ||
  echo "warning: Help Buddy is built for GNOME on Wayland; other desktops are untested." >&2

# 1. System packages (Fedora names).
pkgs=(python3-gobject gtk4 libadwaita gstreamer1-plugins-base gstreamer1-plugins-good
      pipewire-gstreamer xdg-desktop-portal-gnome)
missing=()
if command -v rpm >/dev/null; then
  for p in "${pkgs[@]}"; do rpm -q "$p" >/dev/null 2>&1 || missing+=("$p"); done
fi
if ((${#missing[@]})); then
  say "Missing packages: ${missing[*]}"
  if command -v dnf >/dev/null && [[ -t 0 ]]; then
    read -rp "Install them now with 'sudo dnf install'? [y/N] " answer
    [[ $answer == [yY]* ]] || fail "Install them, then run ./install.sh again."
    sudo dnf install -y "${missing[@]}"
  else
    fail "Install them with your package manager, then run ./install.sh again."
  fi
fi

# 2. Python bindings actually load.
/usr/bin/python3 - <<'EOF' || fail "PyGObject / GTK 4 / libadwaita / GStreamer bindings are not usable."
import gi
for ns, ver in (("Gtk", "4.0"), ("Adw", "1"), ("Gst", "1.0"), ("GdkPixbuf", "2.0")):
    gi.require_version(ns, ver)
EOF

# 3. Claude Code CLI (Help Buddy uses your Claude subscription through it).
if ! command -v claude >/dev/null && [[ ! -x "$HOME/.local/bin/claude" ]]; then
  fail "Claude Code CLI not found. Install it (https://code.claude.com/docs), run 'claude' once to log in, then run ./install.sh again."
fi

# 4. Tests.
say "Running tests…"
/usr/bin/python3 -m unittest discover -s tests -q 2>&1 | tail -n 3
/usr/bin/python3 -m unittest discover -s tests -q >/dev/null 2>&1 || fail "Tests failed; not installing."

# 5. Install: command link, app entry, global shortcut, Blur My Shell whitelist.
say "Installing…"
"$REPO/bin/helpbuddy" --install

# 6. Start in the background (no window); the shortcut opens it after the first capture.
gdbus call --session --dest local.claude.HelpBuddy --object-path /local/claude/HelpBuddy \
  --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1 || true
sleep 1
setsid "$HOME/.local/bin/helpbuddy" --background >/dev/null 2>&1 < /dev/null &

key=$(/usr/bin/python3 -c "import sys; sys.path.insert(0, '$REPO'); from helpbuddy import config, system; print(system.pretty_accel(config.load_settings().shortcut))" 2>/dev/null || echo "your shortcut")
say "Done. Press $key anywhere to get help with your screen."
echo "The first no-flash capture asks once which screen to share — pick yours and allow it."
