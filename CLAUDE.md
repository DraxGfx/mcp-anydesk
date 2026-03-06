# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP server for remote Windows administration via nested AnyDesk sessions (Local -> AnyDesk -> AnyDesk -> Server). Injects PowerShell commands as keystrokes into AnyDesk windows and reads output via OCR (Tesseract). Designed for environments where no agents can be installed on the remote server.

**Key constraint:** Claude never presses Enter. The human operator always confirms each command manually.

## Build & Run

```bash
# Setup
python -m venv .venv
.venv\Scripts\activate
.venv\Scripts\pip.exe install -e .

# Run (stdio transport, used by Claude Desktop)
python -m mcp_anydesk_server
```

**IMPORTANTE:** Claude Desktop usa `.venv\Scripts\python.exe`. Siempre instalar con
`.venv/Scripts/pip.exe install -e .` (el pip del venv, NO el global). Si usas `pip install .`
global, los cambios no se reflejan en Claude Desktop.

Setup completo e instrucciones de Claude Desktop config: `docs/SETUP.md`

There is no automated test suite. Testing is manual via Claude Desktop using `docs/testing/TEST_PROMPT.md`.

## Architecture

The server uses FastMCP (`mcp.server.fastmcp.FastMCP`) with conditional tool registration based on preflight capability checks:

```
server.py          — Entry point. Registers MCP tools conditionally based on preflight results.
                     Tools are registered at module level (not lazily), gated by:
                     - preflight.pywin32_available  → window management tools
                     - preflight.write_capable      → keyboard injection tools
                     - preflight.read_capable       → OCR/screenshot tools

startup_checks.py  — Preflight: checks pywin32, mss, tesseract, opencv availability.
                     PreflightResult dataclass with write_capable/read_capable/ocr_capable properties.

keyboard_injector.py — Keystroke injection via SendInput KEYEVENTF_UNICODE.
                       Simple flow: focus -> type char-by-char with delay -> verify focus.
                       Layout-independent: sends Unicode codepoints directly, no VkKeyScanW needed.

window_manager.py  — AnyDesk window enumeration, pinning, named sessions, focus management.
                     Uses AttachThreadInput to bypass Windows foreground lock.
                     Auto-clicks window center after focus (FOCUS_CLICK_ENABLED).

screen_reader.py   — OCR via Tesseract+OpenCV with adaptive retry (4x zoom when confidence < 0.4).
                     Base64 mode with SHA256 checksum validation.

screenshot.py      — JPEG capture with guaranteed 15KB cap (quality reduction + emergency resize).

session_state.py   — Singleton session state: command history, audit log (append-only on disk).

command_templates.py — Sanitizer wrapper and bootstrap command sequences (9-step PII sanitizer setup).

sanitizer.py       — Post-OCR PII redaction (IPs, emails, DOMAIN\user, SIDs).

recipes.py         — Pre-built PowerShell command sequences for common admin tasks.

win32_input.py     — Shared ctypes structs (INPUT, KEYBDINPUT, MOUSEINPUT) used by keyboard_injector and window_manager.

config.py          — All tunable constants (delays, OCR params, screenshot limits).

__main__.py        — CLI entry point. Runs the server in stdio mode.
```

## Key Design Patterns

- **Conditional tool registration:** `server.py` uses `if preflight.write_capable:` blocks at module scope to register tools only when their dependencies are available. This means the server can start even if some deps are missing (e.g., Tesseract not installed = no OCR tools, but injection still works).

- **UNICODE injection:** All characters are sent via `KEYEVENTF_UNICODE` (`wVk=0, wScan=<Unicode codepoint>`). This is layout-independent — works regardless of local keyboard layout (EN-US, ES-PE, etc.). The v3.2 report of UNICODE being ignored was due to incorrect INPUT struct size (not 40 bytes). SendInput reports success even when AnyDesk drops events, so failures are invisible without visual verification.

- **Ctrl+C limitation:** `send_cancel` cannot send a real interrupt signal through AnyDesk programmatically. VK codes, UNICODE U+0003 — all get rendered as text. The tool focuses the AnyDesk window so the operator can press Ctrl+C manually.

- **Quote balance validation:** Before injection, `_check_quote_balance()` detects unbalanced quotes that would put PowerShell into `>>` continuation mode, which is unrecoverable without Ctrl+C.

- **Sanitizer is opt-in:** Bootstrap (9-step PII sanitizer setup) must be explicitly requested. Without it, `wrap=True` is silently downgraded to `wrap=False` to avoid calling a nonexistent `S()` function on the remote.

## Documentation Structure

```
docs/
  SETUP.md           — Installation and Claude Desktop configuration guide.
  SYSTEM_PROMPT.md   — System prompt for Claude Desktop sessions (rules, workflow, anti-loop).
  ORIGINAL_DESIGN.md — Reference: what worked in v2, what broke in v3, design principles.
  specs/             — Historical architecture specs (v1, v2, v3). Read-only reference.
  testing/
    TEST_PROMPT.md   — Living test prompt (paste into Claude Desktop to validate).
    *.md             — Past test results.
  changelog/         — One file per work session documenting what changed and why.
```

## After Each Work Session

1. **Changelog:** Create `docs/changelog/NNN-short-description.md` with branch, date, objective, changes per file, bugs fixed.
2. **Test prompt:** Update `docs/testing/TEST_PROMPT.md` if any tool behavior, parameters, or expected outputs changed. Update the "Ultima actualizacion" date.
3. **System prompt:** Update `docs/SYSTEM_PROMPT.md` if tools were added/removed or workflow rules changed.

## Windows-Specific Notes

- `server.py` forces `WindowsSelectorEventLoopPolicy` at import time to avoid `OSError: [Errno 22]` with ProactorEventLoop on Python 3.8+ over stdio pipes.
- Binary mode (`msvcrt.setmode`) is set on stdin/stdout for stdio transport only.

## Language

The codebase and README are primarily in Spanish (comments, commit messages, docs). Code identifiers and docstrings are in English.
