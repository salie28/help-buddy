<div align="center">

# Help Me

**Press one key, get help with whatever is on your screen.**

A small GNOME app that silently screenshots your screen (or a preset area), asks
Claude Sonnet about it through your Claude subscription, and shows the answer in a
frosted-glass window you keep on another desktop. It never steals your focus.

![Platform](https://img.shields.io/badge/platform-GNOME%2050%20·%20Wayland-4a86cf)
![Python](https://img.shields.io/badge/python-3.14-3776ab)
![GTK](https://img.shields.io/badge/GTK-4%20%2B%20libadwaita-6ab04c)
![Version](https://img.shields.io/badge/version-1.0.0-informational)
![License](https://img.shields.io/badge/license-private-lightgrey)

</div>

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Usage](#usage)
- [Settings](#settings)
- [Privacy and data](#privacy-and-data)
- [Troubleshooting](#troubleshooting)
- [Uninstall](#uninstall)
- [Development](#development)
- [Roadmap](#roadmap)

## Features

- **One global key.** The default is <kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>H</kbd>, and you can record any combination in Settings.
- **Silent capture.** By default there is no screen flash: Help Me grabs one frame through the ScreenCast portal. A classic flash screenshot is a toggle away.
- **Whole screen or a preset area.** Drag a box once, and every press sends just that part.
- **Claude Sonnet 5.5** with full thinking. It runs through the `claude` CLI, so it uses your Claude subscription and needs no API key.
- **Never steals focus.**
  - While the window is open (on any desktop), a key press only updates it.
  - When the window is closed, it opens after the shot, so it isn't in the screenshot.
- **Follow-up questions** at the bottom of every answer, in the same conversation. Sonnet remembers what it said and the screenshot isn't sent again.
- **Readable answers.**
  - Markdown rendering, with a copy button on every code block and selectable text.
  - Short answers by default; Detailed is optional.
- **Library** of every request, with thumbnails, status and follow-up counts.
- **Robust.**
  - No timeout.
  - One automatic retry after 20 s, then the failure is logged.
  - Requests interrupted by quitting show up as "Interrupted" with a Retry button.
- **Usage line** under each answer, e.g. `Sonnet 5.5 · 3.5k tokens · ≈$0.02 · 5 s`.
- **Housekeeping.** Screenshots and their saved conversations are cleaned up after N days.

## How it works

```
  key press ──► gsd-media-keys ──► helpme capture ──► running Help Me instance (D-Bus)
                                                            │
             ScreenCast portal + PipeWire (1 frame) ◄───────┤  (Screenshot portal if "flash")
                                                            │
             crop to preset area, save PNG ─────────────────┤
                                                            │
             claude -p --model sonnet --effort high ◄───────┤  stream-json, no tools,
             (saved session in Help Me's own folder)        │  isolated settings
                                                            ▼
                                          frosted Help window (GTK 4 / libadwaita)
```

- **Single instance:** a `Gio.Application`. Every later `helpme capture` is forwarded to the running app.
- **Capture runs one at a time.** Decoding, cropping and saving happen off the UI thread.
- **Claude calls run through `Gio.Subprocess`:**
  - no tools
  - your user settings, hooks and plugins are not loaded
  - the working folder is an empty one
- **Follow-ups resume the saved session.** It lives in Help Me's own Claude project folder, so it never shows up in your normal `/resume` list.

## Requirements

- **Desktop:** GNOME on Wayland. Built and tested on Fedora 44 with GNOME 50.
- **Packages:** `python3-gobject`, `gtk4`, `libadwaita`, `gstreamer1-plugins-base`, `gstreamer1-plugins-good`, `pipewire-gstreamer`, `xdg-desktop-portal-gnome`. The installer checks these and offers to install them.
- **[Claude Code](https://code.claude.com/docs)**, installed and logged in. Run `claude` once.
- **Optional:** [Blur My Shell](https://extensions.gnome.org/extension/3193/blur-my-shell/) for the frosted blur. Without it the window is simply tinted.

## Install

```bash
git clone https://github.com/salie28/helpme.git ~/helpme
cd ~/helpme
./install.sh
```

The installer:
1. checks packages, the Python bindings and the `claude` CLI
2. runs the test suite
3. links `~/.local/bin/helpme`
4. adds a "Help Me" app entry and the global shortcut
5. whitelists the window in Blur My Shell
6. starts Help Me in the background

The first silent capture asks **once** which screen to share. Pick yours and allow it.

## Usage

| Action | What happens |
|---|---|
| Press the help key | A screenshot is sent to Sonnet. The answer appears in the Help window. |
| Window open (any desktop) | Nothing pops up and your focus stays where it is. |
| Window closed | It opens after the screenshot. |
| Type in **Ask a follow-up…** | Continues the same conversation about that screenshot. |
| Back arrow (top left) | Opens the Library of all requests. |
| Copy icon on a code block | Copies exactly that code. |
| Close (×) | Hides the window. The key keeps working in the background. |
| "Help Me" in the app grid or launcher | Shows the window again. |

## Settings

Open them with the gear icon in the window.

| Setting | Default |
|---|---|
| What to capture: whole screen or preset area (+ **Pick area…**) | Whole screen |
| Screen flash on capture (off = silent screen-share frame) | Off |
| Help key (**Change…** → press the keys) | <kbd>Super</kbd>+<kbd>Shift</kbd>+<kbd>H</kbd> |
| Model: Sonnet, Haiku or Opus | Sonnet |
| Answer length: Short (≈120 words) or Detailed | Short |
| Prompt sent with every screenshot | "Look at this screenshot, work out what I'm doing…" |
| Window darkness | 75 % |
| Jump to newest request | On |
| Start in background at login | Off |
| Screenshot folder | `<Pictures>/Screenshots/HelpMe` |
| Delete screenshots after N days (0 = keep) | 30 |

## Privacy and data

- **What leaves your machine:** only the screenshot and your prompt or follow-up questions, sent to Anthropic through your own `claude` login.
- **Where everything is stored:**

| What | Where |
|---|---|
| Settings | `~/.config/helpme/settings.json` |
| History | `~/.local/share/helpme/history.json` |
| Log | `~/.local/state/helpme/helpme.log` |
| Screen-share restore token | `~/.local/state/helpme/screencast_token*` |
| Saved conversations | `~/.claude/projects/-home-<you>--local-state-helpme-work/` |
| Screenshots | your screenshot folder |

- **Deleting** a request removes its screenshot and its saved conversation.

## Troubleshooting

- **The key does nothing inside a VM, remote desktop or game.** That app is holding the keyboard. Press <kbd>Super</kbd>+<kbd>Esc</kbd> (GNOME's "restore shortcuts") first, then the help key.
- **The "Share screen" dialog appears again.** The permission is stored per launch path, so it can come up once for the shortcut and once for the app grid.
- **Video in the screenshot is black.** DRM-protected video can't be captured.
- **Nothing happens at all.** Check these:
  - `~/.local/state/helpme/helpme.log`
  - `pgrep -a helpme`
  - `gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings`, which should list `.../helpme/`

## Uninstall

```bash
./remove.sh          # remove the command, app entry, shortcut, autostart and blur entry
./remove.sh --purge  # also delete settings, history, log and saved conversations
```

Screenshots are never deleted.

## Development

```
bin/helpme            launcher (works through the ~/.local/bin symlink)
helpme/app.py         single-instance app, capture → ask → window wiring
helpme/capture.py     ScreenCast / Screenshot portal, crop, save
helpme/ask.py         claude CLI call, retry, sessions, usage
helpme/window.py      answer page, follow-ups, library
helpme/settings_ui.py settings page, key recorder, area picker
helpme/system.py      shortcut, autostart, install / uninstall
helpme/markdown.py    Markdown → Pango markup
tests/                unittest suite (no extra packages)
docs/superpowers/     design spec and implementation plan
```

```bash
python3 -m unittest discover -s tests     # 92 tests, ~2 s
./bin/helpme --version
```

## Roadmap

- [ ] Help key that also works while a VM holds the keyboard: either a GNOME extension or a root key service
- [ ] RPM package
- [ ] Render Markdown tables

## License

Private project. All rights reserved for now.
