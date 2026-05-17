# Hive-Go: Tmux-Free Claude Code Orchestrator

**Date**: 2026-05-17
**Status**: Design
**Target**: `/home/chrishham/Projects/hive-go/`

## Problem

The current hive (Python/Textual) uses tmux as a process container and terminal multiplexer. This causes:
- Keyboard/escape sequence interference with Claude Code sessions
- No image paste support (tmux strips image protocol sequences)
- An external dependency that fights with interactive CLI tools
- Complex startup flow (create tmux session, run dashboard inside it, manage windows)

## Solution

Rewrite hive in Go using Bubbletea (TUI framework) with embedded PTY terminals via bubbleterm + creack/pty. The app runs directly in the user's terminal with no external multiplexer. Each Claude Code session runs in a PTY owned by the app process.

## Goals

- Feature parity with current hive (no new features, no dropped features)
- Zero tmux dependency
- Single binary distribution (hive + hive-hook)
- Drop-in compatible with existing config and hook state files
- Direct PTY access enabling future image paste support

## Non-Goals

- Image paste support (future work, not v1)
- Windows support
- Web-based UI
- Claude Code API integration beyond synopsis generation

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  hive-go (single process)                                │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Bubbletea Event Loop                               │  │
│  │                                                    │  │
│  │  ┌─ Dashboard Mode ──────────────────────────────┐ │  │
│  │  │ SessionList │ PreviewPane (synopsis)           │ │  │
│  │  └───────────────────────────────────────────────┘ │  │
│  │                                                    │  │
│  │  ┌─ Focus Mode ──────────────────────────────────┐ │  │
│  │  │ Full-screen bubbleterm (PTY ↔ claude)         │ │  │
│  │  └───────────────────────────────────────────────┘ │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ ProcessMgr   │  │ HookState│  │ SynopsisGenerator │  │
│  │ (creack/pty) │  │ (JSON)   │  │ (Anthropic API)   │  │
│  └──────────────┘  └──────────┘  └───────────────────┘  │
│                                                          │
│  ┌──────────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ Config       │  │ State    │  │ HookInstaller     │  │
│  │ (TOML)       │  │ (JSON)   │  │ (settings.json)   │  │
│  └──────────────┘  └──────────┘  └───────────────────┘  │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  hive-hook (separate binary, invoked by Claude Code)     │
│  stdin JSON → write ~/.claude/hive/state/<name>.json     │
└──────────────────────────────────────────────────────────┘
```

## Components

### 1. Process Manager (`internal/process`)

Manages Claude Code subprocess lifecycles. Each session = one PTY + one `claude` process.

**Responsibilities:**
- Spawn: `creack/pty.StartWithSize()` with `claude --dangerously-skip-permissions --name <name>`, env `HIVE_SESSION=<name>`, working directory set to project path
- Kill: SIGTERM to process group, 5s grace period, then SIGKILL
- Resize: propagate terminal size via `pty.Setsize()` when Bubbletea reports `WindowSizeMsg`
- Monitor: goroutine per process calling `cmd.Wait()`, sends `ProcessExitedMsg` to Bubbletea event loop on exit

**Interface:**
```go
type Session struct {
    Name       string
    ProjectDir string
    Cmd        *exec.Cmd
    PTY        *os.File      // PTY master fd
    Term       *bubbleterm.Model
    PID        int
}

type Manager struct {
    sessions map[string]*Session
}

func (m *Manager) Spawn(name, projectDir string, args []string, width, height int) (*Session, error)
func (m *Manager) Kill(name string) error
func (m *Manager) Resize(name string, width, height int) error
func (m *Manager) Get(name string) *Session
func (m *Manager) List() []*Session
```

**Session tracking**: by name (string key), not by tmux window index. PID stored in state.json for crash recovery.

### 2. UI — Bubbletea Model (`internal/ui`)

Single Bubbletea `tea.Model` with two modes:

#### Dashboard Mode

Default view. Two-panel layout:

- **Left panel — Session List**: scrollable list of sessions showing:
  - Status icon: `●` waiting (yellow), `◐` working (blue), `◌` bootstrapping (dim)
  - Session name (bold cyan)
  - State label (WAITING/WORKING)
  - Project path (muted)
  - Model + context bar (e.g., `opus-4.6  ctx:████░░░░░░ 40%`)
  - URL indicators (green/red dots)

- **Right panel — Preview**: synopsis text for the highlighted session (from Anthropic API), or "(generating synopsis...)" while in flight.

- **Keybindings (dashboard mode)**:
  - `n` — new session (project picker dialog)
  - `f` — free session (home directory, no project)
  - `g` — clone repo + open session
  - `k` — kill session (confirmation dialog)
  - `r` — resume session (re-spawn with `--resume`)
  - `R` — rename session
  - `u` — open detected URL in browser
  - `Enter` — switch to focus mode for highlighted session
  - `Q` — quit all (kill all sessions + exit)
  - `Up/Down` — navigate session list

#### Focus Mode

Full-screen terminal view of a single Claude Code session.

- The entire terminal is taken over by the bubbleterm widget rendering the session's PTY output
- All keyboard input is forwarded to the PTY (raw mode)
- Top status bar: `[session-name] — F1: dashboard`
- `F1` returns to dashboard mode
- Terminal resize events are propagated to the PTY

**Focus mode input handling**: in focus mode, the Bubbletea model intercepts only `F1` (and optionally `Ctrl+\` as a backup). Everything else goes to the PTY via `pty.Write()`.

#### Dialogs

Modal screens rendered as Bubbletea overlays. Keyboard-only (no mouse), same as current hive:

- **ProjectPickerScreen**: filterable project list from scan paths
- **SessionOptionsScreen**: choose new or resume existing session, set name
- **CloneScreen**: enter repo URL, clone path
- **FolderExistsScreen**: open existing / pull / cancel
- **ConfirmKillScreen**: "Kill session X?" yes/no
- **RenameScreen**: text input for new name
- **UrlPickerScreen**: pick from detected URLs
- **ErrorScreen**: display error with OK button

#### Styling

Lip Gloss for all styling. Match the current hive color scheme:
- Header: brand + session count + waiting/loading badges
- Session list items: status-colored icons, cyan names, muted paths
- Footer: keybinding hints
- Dialogs: bordered boxes with primary/error colors

### 3. State Management (`internal/config`, `internal/state`)

#### Config (`~/.config/hive/config.toml`)

Same schema as current hive:

```toml
[projects]
scan_paths = ["~/Projects"]
clone_path = "~/Projects"

[display]
refresh_interval_ms = 5000
preview_lines = 20

[tmux]  # renamed to [session] but [tmux] still parsed for compat
session_name = "hive"
```

Read with a Go TOML library (e.g., `pelletier/go-toml` or `BurntSushi/toml`).

#### Session State (`~/.config/hive/state.json`)

```json
{
  "sessions": {
    "api-server": {
      "project_path": "/home/user/Projects/api",
      "created_at": "2026-05-17T10:00:00Z",
      "last_used": "2026-05-17T12:00:00Z",
      "pid": 12345,
      "claude_session_id": "abc-123"
    }
  },
  "projects": {
    "/home/user/Projects/api": {
      "last_used": "2026-05-17T12:00:00Z"
    }
  }
}
```

**Change from current**: `tmux_window` (int) → `pid` (int). On startup, if PID is dead, re-spawn with `--continue` or `--resume <session_id>`.

#### Atomic Writes

Same safety guarantees as current hive:
- Write to temp file in same directory
- `fsync` the temp file
- `os.Rename` (atomic on same filesystem)
- Refuse symlinks in parent chain

### 4. Hook Handler (`cmd/hive-hook`)

Separate binary, invoked by Claude Code hooks. Identical behavior to current Python version:

```go
func main() {
    sessionName := os.Getenv("HIVE_SESSION")
    if sessionName == "" {
        os.Exit(0)
    }
    // Read stdin (max 64KB)
    // Parse JSON payload
    // Map hook_event_name → state
    // Write atomic JSON to ~/.claude/hive/state/<name>.json
}
```

Event mapping (unchanged):
- `UserPromptSubmit` → `working`
- `Stop` → `waiting`
- `Notification` → `waiting`
- `SessionStart` → `waiting`
- `SessionEnd` → remove state file

### 5. Hook Installer (`internal/hooks`)

Manages `~/.claude/settings.json` to register hive-hook:

- `Install()`: add hook entries for all events, idempotent
- `Uninstall()`: remove hive hook entries
- `IsInstalled()`: check if hooks are present
- File locking with `syscall.Flock`
- Cleans up legacy entries (Python-era commands)

Hook command: `command -v hive-hook >/dev/null 2>&1 && hive-hook || true`

### 6. Session State Detection (`internal/detector`)

Regex-based detection from PTY output (fallback when hooks unavailable):

**Waiting patterns** (same as current):
- `❯` prompt at end of line
- Box-drawing prompt patterns
- "Enter to confirm", "Do you want to proceed?", "(Y)es/(N)o"

**Working patterns** (same as current):
- Braille spinner characters
- `(Ns ·` timing pattern
- "esc to interrupt", "Thinking...", "Running:", "Reading:", "Editing:", "Writing:"

**Other detection**:
- Model: `(Opus|Sonnet|Haiku) \d+\.\d+` pattern
- Context %: `ctx:[█░]+ \d+%` pattern
- URLs: `localhost:\d{2,5}` pattern with TCP probe

### 7. Synopsis Generator (`internal/synopsis`)

Background goroutine that generates session summaries:

- Read JSONL from `~/.claude/projects/<encoded-path>/<session-id>.jsonl`
- Extract first + last 19 messages, cap at 4000 chars
- Call Anthropic API (Haiku model) with system prompt
- Cache result in `~/.claude/hive/synopsis/<name>.json` with mtime/size for invalidation
- Support Vertex AI, Foundry, direct API (check env vars in same order as current)
- Fallback to first 200 chars of conversation if API unavailable

### 8. Project Scanner (`internal/config`)

Scans configured directories for projects:

- Read `scan_paths` from config
- List subdirectories (non-hidden)
- Augment with `last_used` from state.json
- Return sorted list for project picker dialog

## Data Flow

### Session Creation
1. User presses `n` → project picker dialog
2. User selects project → session options dialog
3. App calls `ProcessManager.Spawn(name, projectDir, args, w, h)`
4. ProcessManager creates PTY, starts `claude` process, creates bubbleterm widget
5. State saved to `state.json`
6. Polling goroutine monitors hook state files for status updates

### Session Interaction
1. User presses `Enter` on session → UI switches to focus mode
2. All keystrokes forwarded to PTY via `pty.Write()`
3. PTY output read by bubbleterm, rendered to screen
4. `F1` returns to dashboard mode

### Session State Updates
1. Claude Code fires hook → `hive-hook` writes `~/.claude/hive/state/<name>.json`
2. Dashboard polling (every 5s) reads hook state files
3. Session list updates status icons/labels
4. If hook state unavailable, fall back to PTY output regex detection

### Startup / Session Restore
1. Load `state.json`
2. For each saved session: check if PID is alive
3. If dead: re-spawn with `--continue` or `--resume <session_id>`
4. If project directory doesn't exist: remove from state
5. Wait up to 15s for bootstrapping sessions, purge any that die

### Shutdown
1. User presses `Q`
2. Kill all managed processes (SIGTERM → wait → SIGKILL)
3. Exit

## Dependencies

```
github.com/charmbracelet/bubbletea     # TUI framework
github.com/charmbracelet/bubbles       # TUI components (viewport, list, textinput)
github.com/charmbracelet/lipgloss      # Styling
github.com/taigrr/bubbleterm           # Embedded terminal widget
github.com/creack/pty                  # PTY management
github.com/BurntSushi/toml             # TOML config parsing
github.com/anthropics/anthropic-sdk-go # Anthropic API for synopsis
```

## Project Structure

```
/home/chrishham/Projects/hive-go/
├── cmd/
│   ├── hive/
│   │   └── main.go           # CLI entry point, arg parsing
│   └── hive-hook/
│       └── main.go           # Hook handler binary
├── internal/
│   ├── config/
│   │   ├── config.go         # TOML config loading
│   │   └── state.go          # state.json management
│   ├── detector/
│   │   └── detector.go       # Regex-based state detection
│   ├── hooks/
│   │   └── installer.go      # settings.json hook management
│   ├── process/
│   │   └── manager.go        # PTY process lifecycle
│   ├── safety/
│   │   └── safety.go         # Name validation, atomic writes
│   ├── synopsis/
│   │   └── synopsis.go       # Anthropic API summary generation
│   └── ui/
│       ├── app.go            # Main Bubbletea model, mode switching
│       ├── dashboard.go      # Dashboard mode (list + preview)
│       ├── terminal.go       # Focus mode (bubbleterm wrapper)
│       ├── dialogs.go        # All modal dialog screens
│       ├── session_list.go   # Session list component
│       └── styles.go         # Lip Gloss style definitions
├── go.mod
├── go.sum
├── Makefile                  # build, install, test targets
└── README.md
```

## CLI Interface

```
hive                    # Start dashboard (runs in foreground, sessions die on exit)
hive list               # List active sessions
hive new <path>         # Create new session in directory
hive install-hooks      # Install Claude Code hooks
hive uninstall-hooks    # Remove Claude Code hooks
```

**Difference from current**: no `hive attach` command (no tmux session to attach to). `hive` just runs the dashboard directly.

## File Compatibility

| File | Current (Python) | New (Go) | Compatible? |
|------|-----------------|----------|-------------|
| `~/.config/hive/config.toml` | TOML | TOML | Yes |
| `~/.config/hive/state.json` | JSON with `tmux_window` | JSON with `pid` | Migration needed (drop `tmux_window`, add `pid`) |
| `~/.claude/hive/state/*.json` | JSON | JSON | Yes |
| `~/.claude/hive/synopsis/*.json` | JSON | JSON | Yes |
| `~/.claude/settings.json` | JSON (hook entries) | JSON (same hook entries) | Yes |

## Testing Strategy

- Unit tests for: config parsing, state management, detector regex, safety/validation, hook installer
- Integration tests for: process manager (spawn/kill claude), synopsis API calls
- Manual testing for: full TUI flow, focus mode input, dialog navigation
