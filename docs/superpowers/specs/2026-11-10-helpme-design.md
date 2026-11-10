# Help Me — design

Date: 2026-11-10 · Status: draft for review

## Purpose

A "help me" key. Press it anywhere and the app screenshots the screen (or a preset part
of it), saves the image, and asks Sonnet about it. The answer lands in a separate
frosted-glass **Help** window that you keep on another desktop and swipe to when you want
the answer. Pressing the key never interrupts what you are doing.

## Behaviour

### The key (default Super+Shift+H)

Runs `helpme capture`. The app is single-instance (GApplication + D-Bus):

- **App already running:** the capture is handled by the running instance. Nothing
  opens, nothing is raised, focus never changes, and the window stays on whatever desktop
  you parked it on.
- **App not running:** the app starts and opens the Help window, then handles the capture.
  This is the only time a window appears by itself. GNOME on Wayland always opens a new
  window on the current desktop with focus, and that can't be avoided. You move it to
  another desktop once.
- **Closing the window** only hides it. The app keeps running in the background, so the
  key keeps working without popping anything up. You reopen the window through the
  launcher (Alt+X → "Help Me") or the app grid, and that shows the existing window.
- **Quit** lives in the window's menu and fully stops the app.

### One capture, step by step

1. Screenshot through the desktop portal (`org.freedesktop.portal.Screenshot`,
   non-interactive). Verified: the permission store already holds `screenshot: {'': yes}`,
   so there is no dialog.
2. **Preset-area mode:** crop to the saved rectangle (stored as fractions of the full
   image, so display-scale changes don't break it).
3. Save it as `~/Pictures/Screenshots/HelpMe/YYMMDD_HHMMSS.png`.
4. Create a new request entry and show it on the answer page with a "Thinking…" spinner.
5. Ask Claude in the background (see below). There is **no timeout**; a running request has
   a small × to cancel it.
6. The answer fills in on that page.

Several presses in a row run in parallel, one request each.

If the Help window is visible on the desktop being captured, it simply ends up in the
shot. Hiding and re-showing it would make GNOME move it to the current desktop and focus
it, which breaks the no-focus rule.

### Errors and retry

- A failed attempt means a non-zero exit, an error result, or an empty answer. The request
  then shows "Error — retrying in 20 s".
- After 20 s it makes **one** automatic retry.
- If the retry also fails, the request shows "Failed" with the error text, and the entry is
  written to the log (time, screenshot path, error/stderr). No more automatic retries.
- Failed requests have a manual **Retry** button.

## Claude call

- `claude -p` (Claude subscription; no API key) with the selected model (default `sonnet`).
- The screenshot is sent as a base64 image block over `--input-format stream-json`, with
  the configured prompt as the text block.
- Isolation:
  - no tools (`--tools ""`)
  - `--no-session-persistence`
  - `--strict-mcp-config` with no MCP servers
  - user setting sources skipped, so your hooks and plugins don't load or slow startup
  - own short system prompt
- **Verify first in implementation:** that the subscription login still works with the
  user settings skipped, and the exact flag combination for stream-json in and out.
- Runs through `Gio.Subprocess` (async, no threads); stdout is parsed for the final
  result text.

## Window

- libadwaita, dark, translucent. The tint is the shared glass recipe
  `rgba(37,37,37,.417) → rgba(13,13,13,.392)`, with a white 0.09 edge and a 15 px radius.
  The blur comes from Blur My Shell: the app id `local.claude.HelpMe` is added to
  `applications/whitelist`.
- Two pages in an `Adw.NavigationView`:
  - **Answer page (the default view):** always shows the **newest** request. It has:
    - the screenshot (click opens the full image)
    - the time and capture mode
    - the status (Thinking… / retrying / failed)
    - the answer, rendered from Markdown to Pango (headings, bold/italic, inline code,
      code blocks, lists)
    - Copy, Delete and (on failure) Retry buttons
  - **Library page:** reached with the **back button** at the top left. It is a list of all
    requests, newest first, each with a thumbnail, time, first line of the answer and a
    status dot. Clicking one opens it on the answer page.
- When a new capture comes in, the window switches to the answer page for that new
  request, even if you were in the library or reading an older one (setting: "Jump to
  newest request", on by default; when it is off, you stay where you are). This only changes the
  view inside the window; the window itself is never raised or focused.
- **Header:** the back button (to the Library), the title "Help Me", a **Capture now**
  button, a gear for **Settings**, and a menu (Clear history, Quit).
- History survives restarts.

## Settings page

| Setting | Default |
|---|---|
| Capture mode: Whole screen / Preset area | Whole screen |
| Pick area… (frozen full-screen shot, drag a box, Enter saves, Esc cancels) | — |
| Shortcut (written as a GNOME custom shortcut) | Super+Shift+H |
| Prompt | "Look at this screenshot, work out what I'm doing and what I probably need help with, then help — short and practical." |
| Model: Sonnet / Haiku / Opus | Sonnet |
| Screenshot folder | `~/Pictures/Screenshots/HelpMe` |
| Delete screenshots older than N days (0 = keep) | 30 |
| Start in background at login (no window) | off |
| Jump to newest request when a capture comes in (view inside the window only) | on |

## Storage

- Settings: `~/.config/helpme/settings.json`
- History: `~/.local/share/helpme/history.json` (answer, screenshot path, time, status)
- Log: `~/.local/state/helpme/helpme.log`
- Screenshots: the configured folder

## Code layout (`~/helpme`, git repo)

| File | Job |
|---|---|
| `helpme/app.py` | GApplication, single instance, `capture` command, window lifecycle |
| `helpme/window.py` | Help window: answer page + library page |
| `helpme/capture.py` | portal screenshot, crop, save |
| `helpme/ask.py` | build + run the claude command, parse result, retry policy |
| `helpme/settings.py` | settings load/save + Settings page + area picker |
| `helpme/history.py` | history load/save, screenshot cleanup |
| `helpme/markdown.py` | Markdown → Pango markup |
| `helpme/style.css` | glass look |
| `local.claude.HelpMe.desktop` | launcher entry + app id |
| `tests/` | unittest (stdlib, no new packages) |

**Install:**
- `~/.local/bin/helpme` symlink
- `.desktop` file in `~/.local/share/applications`
- GNOME custom shortcut
- BMS whitelist entry

All of it is undone by `helpme --uninstall`, which leaves the screenshots alone.

## Testing

- **Unit tests:**
  - crop math (fractions → pixels, edge clamping)
  - the claude command and stream-json payload
  - result parsing (success, error, empty)
  - the retry policy (fail → wait 20 s → retry once → log)
  - history round-trip
  - screenshot cleanup by age
  - the Markdown converter (including escaping `<`, `&`)
- **Real run:**
  - press the key with the window on another desktop and check that focus doesn't move
  - check that the answer fills in and the PNG exists
  - check that preset-area mode crops correctly
  - force an error (bad model name) and check the retry and log behaviour

## Out of scope

Follow-up questions, notifications, auto-focus/raise, API key backend, choosing a
monitor, region select per press.
