# Help Me

Press **Super+Shift+H** anywhere: the screen (or a preset area) is captured, saved, and
sent to Claude Sonnet through `claude -p` (your Claude subscription, no API key). The
answer appears in the frosted **Help Me** window. Keep that window on another desktop; a
key press never pops it up or takes focus.

- Install: `~/helpme/bin/helpme --install` (adds the command link, app entry, shortcut and blur)
- Uninstall: `helpme --uninstall` (keeps settings, history, log and screenshots)
- Open the window: "Help Me" in the launcher/app grid, or `helpme`
- Settings: the gear in the window (capture area, flash, shortcut, model, prompt, darkness…)
- Files: `~/.config/helpme/settings.json`, `~/.local/share/helpme/history.json`,
  `~/.local/state/helpme/helpme.log`, screenshots in `<Pictures>/Screenshots/HelpMe`
- Tests: `cd ~/helpme && python3 -m unittest discover -s tests`

Needs: GNOME (Wayland), python3-gobject, GTK 4, libadwaita, GStreamer + pipewire-gstreamer,
and a logged-in `claude` CLI. Without "Screen flash", the first capture asks once which
screen to share (that is what makes it silent).
