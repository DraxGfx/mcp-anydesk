# MCP AnyDesk Server v3 — Architecture Decision Record & Implementation Spec

> **Purpose:** Single source of truth for implementing v3. Contains fixes from v2 testing, performance optimizations, and new features. All decisions are finalized.
>
> **Prerequisites:** Read `MCP_ANYDESK_SPEC.md` (v1) and `MCP_ANYDESK_SPEC_V2.md` (v2) first. This document builds incrementally — unchanged modules are not repeated.

---

## 0. v2 Test Results Summary (What Drives v3)

| # | Test | Result | Issue Identified |
|---|---|---|---|
| 1 | OCR básico (`hostname`) | ✅ conf 0.34–0.44 | OCR confidence too low for production |
| 2 | Base64 mode (`hostname`) | ⚠️ Partial | Bootstrap multilínea bloqueado en PS5 |
| 3 | Screenshot (`capture_screenshot`) | ✅ | PNG base64 consumes excessive tokens |
| 4 | Session history | ✅ | Works but accumulates token usage |
| 5 | Write wrap=True (`Get-Date`) | ❌ Focus drift | hwnd 67072→460980, 16s injection too long |
| 6 | Write wrap=False (`Get-Date`) | ✅ | Short commands work fine |

**Root causes:**
1. **Focus drift** — Wrapped commands are ~150+ chars. At 80ms/char = 12-16 seconds of continuous injection. Windows drops foreground lock.
2. **Token waste** — PNG screenshots, verbose system prompt, full session history, garbled OCR noise all consume context window.
3. **PS5 bootstrap blocked** — Multiline script can't be injected as-is through single-line keystroke injection. The bootstrap script is multiline but `write_to_anydesk` rejects newlines.
4. **OCR unreliable** — 0.34 confidence means >60% of characters are wrong. Not usable for command output verification.

**Critical constraint reconfirmed:** Clipboard injection is NOT possible in nested AnyDesk (AnyDesk → AnyDesk). Clipboard redirection breaks at the second hop. All injection must remain keystroke-based.

---

## 1. v3 Scope — What Ships

### 1.1. MUST HAVE (Blocks Production Use)

| # | Feature | Fixes |
|---|---|---|
| M1 | Chunked injection with inter-chunk re-focus | Focus drift (test #5) |
| M2 | Token optimization (JPEG, scale, prompt compaction) | Context window exhaustion |
| M3 | Bootstrap PS5 fix via EncodedCommand | Base64 mode blocked (test #2) |
| M4 | Wrapper compression (PS aliases) | Reduces injection time ~30% |
| M5 | Keystroke cancel (Ctrl+C injection) | No way to abort hung commands |
| M6 | Startup wizard (`initialize_session`) | 4 manual steps → 1 tool call |
| M7 | Destructive command blocklist (server-side) | Safety: prompt-only guard insufficient |

### 1.2. NICE TO HAVE (Improves Experience)

| # | Feature | Value |
|---|---|---|
| N1 | Quick diagnostic mode | Skip approval for read-only commands |
| N2 | Adaptive OCR (auto-retry with zoom) | Improve 0.34 → 0.6+ confidence |
| N3 | Session recording / export report | Auditoría post-sesión |
| N4 | Named sessions + multi-window | Compare servers side by side |
| N5 | Output pagination detection | Handle commands that return 200+ lines |
| N6 | Dead session detection | Detect AnyDesk "connecting..." screen |
| N7 | Health heartbeat | Periodic sanitizer + window alive check |
| N8 | Command recipes | Pre-built sequences for common tasks |

### 1.3. EXPLICITLY OUT OF SCOPE

- Mouse/GUI automation (decided in v1 — still valid)
- Clipboard injection (impossible in nested AnyDesk)
- Agent installation on remote (client restriction)
- Multi-hop automation (automating the AnyDesk → AnyDesk jump)

---

## 2. Architecture Changes

### 2.1. Updated Project Structure

```
mcp_anydesk_server/
├── server.py                 # MCP entry point (UPDATED: new tools, startup wizard)
├── startup_checks.py         # Preflight (UPDATED: richer diagnostics)
├── config.py                 # Defaults (UPDATED: v3 constants)
├── window_manager.py         # Window management (UPDATED: named sessions, multi-window)
├── keyboard_injector.py      # REWRITTEN: chunked injection + Ctrl+C + SendInput option
├── screen_reader.py          # UPDATED: JPEG output, adaptive OCR, auto-crop
├── screenshot.py             # UPDATED: JPEG default, differential capture
├── sanitizer.py              # PII regex layer 2 (unchanged)
├── command_templates.py      # UPDATED: compressed wrapper, EncodedCommand bootstrap
├── session_state.py          # UPDATED: session export, named sessions
├── safety.py                 # NEW: destructive command blocklist
├── recipes.py                # NEW: command recipes for common tasks
├── requirements.txt          # (unchanged — no new dependencies)
├── MCP_ANYDESK_SPEC.md       # v1 spec (reference)
├── MCP_ANYDESK_SPEC_V2.md    # v2 spec (reference)
└── MCP_ANYDESK_SPEC_V3.md    # This document
```

### 2.2. New config.py Constants

Append to existing `config.py`:

```python
# --- v3 additions ---

# Chunked injection
CHUNK_SIZE = 30                     # Characters per chunk before re-focus
CHUNK_REFOCUS_DELAY_MS = 200        # Delay between chunks for re-focus check
CHUNK_MAX_RETRIES = 2               # Re-focus attempts per chunk before aborting

# Token optimization
SCREENSHOT_FORMAT = "jpeg"          # "jpeg" (default v3) or "png"
SCREENSHOT_JPEG_QUALITY = 60        # 1-100, lower = smaller + fewer tokens
SCREENSHOT_DEFAULT_SCALE = 50       # Default downscale percentage
MAX_SESSION_HISTORY_RESPONSE = 5    # Max steps returned in get_session_history

# Keystroke cancel
CANCEL_KEY_COMBO = "ctrl+c"         # Key combo for abort injection
CANCEL_FOCUS_DELAY_MS = 300         # Focus delay before sending cancel

# Quick diagnostic mode
DIAGNOSTIC_SAFE_COMMANDS = [
    "hostname",
    "whoami",
    "Get-Date",
    "Get-VM",
    "Get-VMHost",
    "Get-Service",
    "Get-Process",
    "Get-Volume",
    "Get-NetAdapter",
    "Get-HotFix | Select -First 10",
    "$PSVersionTable",
    "Get-EventLog -LogName System -Newest 10",
    "Get-WindowsFeature | Where Installed",
    "Get-VMSwitch",
]

# Destructive command patterns (server-side blocklist)
BLOCKED_DESTRUCTIVE_PATTERNS = [
    r"Remove-Item\s.*-Recurse",
    r"Remove-VM\b",
    r"Remove-Snapshot\b",
    r"Format-Volume\b",
    r"Format-Disk\b",
    r"Clear-Content\b",
    r"Clear-Disk\b",
    r"Stop-VM\s.*-Force",
    r"Stop-Computer\b",
    r"Restart-Computer\b",
    r"Remove-WindowsFeature\b",
    r"Uninstall-WindowsFeature\b",
    r"Initialize-Disk\b",
    r"Remove-Partition\b",
    r"Remove-VHD\b",
    r"Reset-VM\b",
    r"Remove-VMSwitch\b",
    r"Remove-NetAdapter\b",
    r"del\s+/[sS]",            # CMD recursive delete
    r"rmdir\s+/[sS]",          # CMD recursive rmdir
    r"rd\s+/[sS]",             # CMD recursive rd
]

# Session recording
SESSION_RECORDING_DIR = "~/.mcp_anydesk/recordings"
SESSION_RECORDING_ENABLED = False   # Off by default, operator enables

# Health heartbeat
HEARTBEAT_INTERVAL_MINUTES = 5
HEARTBEAT_ENABLED = False           # Off by default

# Dead session detection
ANYDESK_DISCONNECT_PATTERNS = [
    "connecting",
    "waiting for",
    "session interrupted",
    "connection closed",
    "not connected",
]
```

---

## 3. MUST HAVE Features — Detailed Specs

### 3.1. M1 — Chunked Injection with Inter-Chunk Re-Focus

**Problem:** Injecting 150+ chars takes 12-16 seconds. Windows drops foreground lock and keystrokes go to wrong window.

**Solution:** Split injection into chunks of `CHUNK_SIZE` characters (default 30). Between each chunk, re-verify and re-acquire focus. If re-focus fails after `CHUNK_MAX_RETRIES`, abort and report exactly how many characters were successfully injected.

**Updated `keyboard_injector.py`:**

```python
"""Keyboard injection with chunked re-focus for long commands.

Characters are injected in chunks of CHUNK_SIZE. Between each chunk,
the window focus is re-verified and re-acquired if lost. This prevents
focus drift during long injections (the primary failure mode in v2).
"""

from __future__ import annotations

import time
from pynput.keyboard import Controller as KbController, Key

from command_templates import wrap_command, wrap_command_base64
from config import (
    DEFAULT_KEYSTROKE_DELAY_MS,
    MIN_KEYSTROKE_DELAY_MS,
    DEFAULT_FOCUS_DELAY_MS,
    CHUNK_SIZE,
    CHUNK_REFOCUS_DELAY_MS,
    CHUNK_MAX_RETRIES,
    CANCEL_FOCUS_DELAY_MS,
)
from window_manager import focus_and_verify, verify_focus_post, get_pinned_hwnd
from safety import check_command_safety

_keyboard = KbController()


def _inject_chunk(text: str, delay_s: float) -> int:
    """Inject a chunk of text character by character.

    Args:
        text: Characters to inject.
        delay_s: Delay between each character in seconds.

    Returns:
        Number of characters successfully injected.
    """
    for i, char in enumerate(text):
        _keyboard.type(char)
        time.sleep(delay_s)
    return len(text)


def _refocus_with_retry(focus_delay_ms: int) -> bool:
    """Attempt to re-acquire focus with retries.

    Returns:
        True if focus was re-acquired, False if all retries failed.
    """
    for attempt in range(CHUNK_MAX_RETRIES + 1):
        try:
            focus_and_verify(focus_delay_ms)
            return True
        except RuntimeError:
            if attempt < CHUNK_MAX_RETRIES:
                time.sleep(CHUNK_REFOCUS_DELAY_MS / 1000.0)
            continue
    return False


def write_to_anydesk(
    command: str,
    keystroke_delay_ms: int = DEFAULT_KEYSTROKE_DELAY_MS,
    focus_delay_ms: int = DEFAULT_FOCUS_DELAY_MS,
    wrap: bool = True,
    base64_output: bool = False,
) -> dict:
    """Focus the pinned AnyDesk window and inject keystrokes in chunks.

    Characters are injected in chunks of CHUNK_SIZE (default 30).
    Between each chunk, focus is re-verified and re-acquired if needed.

    Args:
        command: Text to inject. Must NOT contain newlines.
        keystroke_delay_ms: Delay between each character (ms). Floor: 30ms.
        focus_delay_ms: Delay after focusing window before typing (ms).
        wrap: If True, auto-wrap with sanitizer check before injecting.
        base64_output: If True, use base64 wrapper (wrap must be True).

    Returns:
        Dict with status, char_count, chars_injected, original_command,
        wrapped, elapsed_ms, chunks_used, and note.
    """
    original_command = command

    # --- Reject multi-line input ---
    if any(ch in command for ch in ("\n", "\r")):
        return {
            "status": "error",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": "Multi-line input rejected. Split into separate steps.",
        }

    # --- Strip stray newline sequences ---
    command = command.replace("\n", "").replace("\r", "")
    command = command.replace("`n", "").replace("`r", "")

    if not command:
        return {
            "status": "error",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": "Empty command after sanitization.",
        }

    # --- Safety check (server-side blocklist) ---
    safety_result = check_command_safety(original_command)
    if not safety_result["allowed"]:
        return {
            "status": "blocked",
            "char_count": 0,
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": False,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": safety_result["reason"],
        }

    # --- Auto-wrap with sanitizer check ---
    was_wrapped = False
    if wrap:
        if base64_output:
            command = wrap_command_base64(command)
        else:
            command = wrap_command(command)
        was_wrapped = True

    # --- Enforce minimum delay ---
    keystroke_delay_ms = max(keystroke_delay_ms, MIN_KEYSTROKE_DELAY_MS)
    delay_s = keystroke_delay_ms / 1000.0

    # --- Initial focus ---
    try:
        focus_and_verify(focus_delay_ms)
    except RuntimeError as exc:
        return {
            "status": "error",
            "char_count": len(command),
            "chars_injected": 0,
            "original_command": original_command,
            "wrapped": was_wrapped,
            "elapsed_ms": 0,
            "chunks_used": 0,
            "note": f"Initial focus failed: {exc}",
        }

    # --- Chunked injection ---
    start = time.perf_counter()
    total_injected = 0
    chunks_used = 0

    # Split command into chunks
    chunks = [
        command[i : i + CHUNK_SIZE]
        for i in range(0, len(command), CHUNK_SIZE)
    ]

    for chunk_idx, chunk in enumerate(chunks):
        # Re-verify focus before each chunk (except the first, already verified)
        if chunk_idx > 0:
            try:
                verify_focus_post()
            except RuntimeError:
                # Focus lost — try to re-acquire
                if not _refocus_with_retry(focus_delay_ms):
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    return {
                        "status": "error",
                        "char_count": len(command),
                        "chars_injected": total_injected,
                        "original_command": original_command,
                        "wrapped": was_wrapped,
                        "elapsed_ms": elapsed_ms,
                        "chunks_used": chunks_used,
                        "note": (
                            f"Focus lost after chunk {chunk_idx} "
                            f"({total_injected}/{len(command)} chars injected). "
                            "Re-focus failed after retries. "
                            "PARTIAL INJECTION — command is incomplete in the terminal."
                        ),
                    }

        # Inject this chunk
        injected = _inject_chunk(chunk, delay_s)
        total_injected += injected
        chunks_used += 1

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # --- Post-injection focus verification ---
    focus_warning = ""
    try:
        verify_focus_post()
    except RuntimeError as exc:
        focus_warning = f" WARNING: {exc}"

    return {
        "status": "injected",
        "char_count": len(command),
        "chars_injected": total_injected,
        "original_command": original_command,
        "wrapped": was_wrapped,
        "elapsed_ms": elapsed_ms,
        "chunks_used": chunks_used,
        "note": f"Enter NOT pressed — awaiting operator confirmation.{focus_warning}",
    }


def send_cancel() -> dict:
    """Send Ctrl+C to the pinned AnyDesk window to abort a running command.

    Returns:
        Dict with status and note.
    """
    hwnd = get_pinned_hwnd()
    if hwnd is None:
        return {
            "status": "error",
            "note": "No AnyDesk window pinned.",
        }

    try:
        focus_and_verify(CANCEL_FOCUS_DELAY_MS)
    except RuntimeError as exc:
        return {
            "status": "error",
            "note": f"Could not focus window for cancel: {exc}",
        }

    # Send Ctrl+C
    with _keyboard.pressed(Key.ctrl):
        _keyboard.press("c")
        _keyboard.release("c")

    return {
        "status": "ok",
        "note": "Ctrl+C sent. The remote command should be interrupted.",
    }
```

**Key behaviors:**
- Commands <= 30 chars: injected in 1 chunk (no overhead vs v2)
- Commands 31-60 chars: 2 chunks with 1 re-focus check
- Wrapped commands ~150 chars: 5 chunks, focus verified every ~2.4 seconds
- If focus is lost mid-injection: 2 retry attempts, then abort with partial injection report
- New `chars_injected` field tells exactly how much was typed (critical for debugging partial injections)

---

### 3.2. M2 — Token Optimization

**Problem:** Context window exhausts after 8-12 interactions. Major consumers: PNG screenshots (~400KB base64), verbose system prompt (5.5KB), session history, OCR noise.

**Fix 1: JPEG screenshots with quality control**

Update `screenshot.py`:

```python
def capture_screenshot(
    region_x: int = 0,
    region_y: int = 0,
    region_w: int = 0,
    region_h: int = 0,
    scale_percent: int = SCREENSHOT_DEFAULT_SCALE,  # Changed: 50% default
    quality: int = SCREENSHOT_JPEG_QUALITY,          # NEW: JPEG quality
    format: str = SCREENSHOT_FORMAT,                 # NEW: "jpeg" or "png"
) -> dict:
```

Encoding changes:

```python
if format == "jpeg":
    success, buf = cv2.imencode(
        ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    mime_type = "image/jpeg"
else:
    success, buf = cv2.imencode(".png", img)
    mime_type = "image/png"
```

**Token impact:**
| Config | Approx base64 size | Visual tokens |
|---|---|---|
| v2: PNG 100% | ~400KB | ~1500 |
| v3: JPEG 60% at 50% scale | ~30-50KB | ~750 |
| Savings | **~85-90%** | **~50%** |

**Fix 2: Compact system prompt**

Reduce from 5.5KB to ~1.8KB by removing verbose explanations and keeping only the rules. See section 5 for the complete compacted system prompt.

**Fix 3: Session history truncation**

`get_session_history` default returns last 5 steps (was 10). Output text is truncated to first 80 chars in the response (full text remains in audit log on disk).

```python
def get_history(self, last_n: int = MAX_SESSION_HISTORY_RESPONSE) -> list[dict]:
    records = [asdict(c) for c in self.commands[-last_n:]]
    # Truncate output_text for token efficiency
    for r in records:
        if r.get("output_text") and len(r["output_text"]) > 80:
            r["output_text"] = r["output_text"][:80] + "...[truncated]"
    return records
```

**Fix 4: OCR noise reduction**

When OCR confidence is below 0.4, strip lines that are clearly garbled (>50% non-alphanumeric characters) before returning to the LLM:

```python
def _strip_garbled_lines(text: str, threshold: float = 0.5) -> str:
    """Remove lines where more than `threshold` of chars are non-alphanumeric."""
    clean_lines = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        alnum = sum(1 for c in line if c.isalnum() or c.isspace())
        ratio = alnum / max(len(line), 1)
        if ratio >= threshold:
            clean_lines.append(line)
    return "\n".join(clean_lines)
```

---

### 3.3. M3 — Bootstrap PS5 Fix via EncodedCommand

**Problem:** The bootstrap script is multiline. `write_to_anydesk` rejects newlines. In PS5, `iex` on a multiline here-string requires actual newlines. The v2 bootstrap was designed for direct injection but can't be injected through our single-line constraint.

**Solution:** Encode the entire bootstrap script as a base64 string and inject it using PowerShell's `-EncodedCommand` parameter. This converts any multiline script into a single-line invocation.

**Updated `command_templates.py`:**

```python
import base64

def bootstrap() -> str:
    """Return a single-line bootstrap command using EncodedCommand.

    The multiline sanitizer script is base64-encoded and executed via
    powershell -EncodedCommand, which accepts the encoded form of any
    script regardless of newlines, quotes, or special characters.
    """
    # The raw script to create the sanitizer file
    raw_script = (
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
        '$s = @"\n'
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n'
        'function S($c){\n'
        '  try {\n'
        '    $r = iex $c | Out-String\n'
        "    $r = $r -replace '(?<!\\d)(10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})(?!\\d)','[IP-X]'\n"
        "    $r = $r -replace '(?<!\\d)(172\\.(1[6-9]|2\\d|3[01])\\.\\d{1,3}\\.\\d{1,3})(?!\\d)','[IP-X]'\n"
        "    $r = $r -replace '(?<!\\d)(192\\.168\\.\\d{1,3}\\.\\d{1,3})(?!\\d)','[IP-X]'\n"
        "    $r = $r -replace '(?i)fe80:[0-9a-f:]+(%\\w+)?','[IPv6-X]'\n"
        "    $r = $r -replace '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}','[EMAIL-X]'\n"
        "    $r = $r -replace '(?i)[A-Z0-9_-]+\\\\[A-Z0-9_.-]+','[ACCT-X]'\n"
        "    $r = $r -replace '(?i)[a-z0-9._-]+@[a-z0-9.-]+\\.local','[UPN-X]'\n"
        "    $r = $r -replace 'S-1-\\d+-\\d+(-\\d+){1,}','[SID-X]'\n"
        "    $r = $r -replace '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}','[MAC-X]'\n"
        "    $r = $r -replace '\\\\\\\\[A-Za-z0-9_.-]+\\\\','\\\\[SRV-X]\\\\'\n"
        '    $r.TrimEnd()\n'
        '  } catch {\n'
        '    "[ERROR] $($_.Exception.Message)"\n'
        '  }\n'
        '}\n'
        '"@\n'
        f'Set-Content -Path "$env:TEMP\\{BOOTSTRAP_FILENAME}" -Value $s -Encoding UTF8; '
        f'"[BOOTSTRAP-OK] Sanitizer written to $env:TEMP\\{BOOTSTRAP_FILENAME}"'
    )

    # Encode as UTF-16LE (required by -EncodedCommand)
    encoded_bytes = raw_script.encode("utf-16-le")
    encoded_b64 = base64.b64encode(encoded_bytes).decode("ascii")

    # Return the single-line invocation
    return f"powershell -EncodedCommand {encoded_b64}"
```

**Why this works:**
- `-EncodedCommand` accepts a base64-encoded UTF-16LE string
- The encoded string contains the complete multiline script
- PowerShell decodes and executes it as if it were a normal script
- It's a single line with no special characters — perfect for keystroke injection
- Works identically on PS 5.1 and PS 7+

**Caveat:** The encoded command is ~800-1000 characters long. At 30 chars/chunk with re-focus, that's ~33 chunks. At 80ms/char this takes ~80 seconds. This is acceptable because bootstrap runs ONCE per session. We could also temporarily increase `CHUNK_SIZE` to 50 for bootstrap-only injection to reduce to ~20 chunks (~50 seconds).

**Alternative considered:** Split the script into multiple `Add-Content` calls (3-4 separate injections, each single-line). Rejected because it requires 4 inject->Enter->verify cycles instead of 1, and any failure mid-sequence leaves a corrupt bootstrap file.

---

### 3.4. M4 — Wrapper Compression

**Problem:** The sanitizer wrapper adds ~120 characters of overhead per command. With chunked injection this is ~4 extra chunks of re-focus overhead.

**Solution:** Use PowerShell aliases and abbreviations:

```python
# v2 wrapper (~120 chars overhead):
# if(!(Test-Path "$env:TEMP\s.ps1")){"[SANITIZER-MISSING]"}else{iex (Get-Content "$env:TEMP\s.ps1" -Raw); S('Get-Date')}

# v3 wrapper (~80 chars overhead):
_COMMAND_WRAPPER = (
    'if(!(Test-Path $env:TEMP\\{f})){{"{s}"}}'
    'else{{.{{gc $env:TEMP\\{f} -Raw|iex;S(\'{c}\')}}}}'
)
```

**Aliases used:**
- `gc` = `Get-Content` (built-in alias, works on PS 5.1 and 7+)
- `$env:TEMP\\{f}` shortened from `"$env:TEMP\\{filename}"`
- Piping `gc ... -Raw|iex` instead of `iex (gc ... -Raw)` saves parens
- Script block `.{ }` instead of separate statements

**Savings:** ~40 characters per command = ~1.2 fewer chunks per injection = ~3 fewer seconds per command.

---

### 3.5. M5 — Keystroke Cancel (Ctrl+C)

**Problem:** If a command hangs (e.g., `Test-Connection` to unreachable host), there's no way to interrupt it. The operator has to manually focus the window and press Ctrl+C.

**Solution:** New tool `send_cancel` that focuses the window and sends Ctrl+C.

**Tool signature:**

```python
send_cancel() -> dict
```

**Returns:**

```python
{
    "status": "ok",
    "note": "Ctrl+C sent. The remote command should be interrupted."
}
```

**Implementation:** Already included in the `keyboard_injector.py` rewrite (section 3.1). Uses pynput's `Key.ctrl` context manager.

**System prompt guidance:** The LLM should use this when:
- The operator says "it's stuck" or "cancel" or "abort"
- OCR shows no new output after 30+ seconds
- The operator explicitly requests interrupt

---

### 3.6. M6 — Startup Wizard (`initialize_session`)

**Problem:** Every session requires 4 manual tool calls in sequence:
1. `get_system_status`
2. `select_anydesk_window` (list)
3. `select_anydesk_window` (pin)
4. `bootstrap_sanitizer`

This wastes 4 LLM turns + 4 tool responses worth of tokens before any real work begins.

**Solution:** A single `initialize_session` tool that does all setup steps internally and returns a consolidated status report.

**Tool signature:**

```python
initialize_session(
    window_index: int = 0,      # Which window to pin if multiple found (0 = first)
    skip_bootstrap: bool = False # Skip sanitizer bootstrap (if already done)
) -> dict
```

**Returns:**

```python
{
    "status": "ok",  # or "partial" or "error"
    "system_status": {
        "write_capable": True,
        "read_capable": True,
        "ocr_capable": True,
        "tesseract_version": "5.5.0"
    },
    "window": {
        "hwnd": 722844,
        "title": "1 744 460 937 - AnyDesk",
        "total_windows_found": 1
    },
    "sanitizer": {
        "bootstrapped": True,  # or False if skip_bootstrap
        "note": "Sanitizer injected. Operator must press Enter to activate."
    },
    "session_id": "20260224_190500",
    "note": "Session initialized. Press Enter in the remote terminal to activate the sanitizer, then tell me what you need."
}
```

**Internal sequence:**
1. Run preflight -> verify deps
2. Enumerate AnyDesk windows -> if 0 found, return error; if 1, auto-pin; if multiple, pin `window_index`
3. If not `skip_bootstrap`: inject bootstrap via EncodedCommand
4. Store all state in session singleton
5. Return consolidated report

**The LLM still needs 1 interaction from the operator:** pressing Enter after bootstrap injection. This is by design (never press Enter automatically).

---

### 3.7. M7 — Destructive Command Blocklist (`safety.py`)

**Problem:** The v2 system prompt warns the LLM not to execute destructive commands, but the server itself happily injects them if the LLM ignores the prompt. Defense in depth requires server-side enforcement.

**Implementation:**

```python
"""Server-side safety checks for destructive commands.

This is a defense-in-depth layer. The system prompt instructs the LLM
to warn before destructive commands, but if the LLM ignores the prompt
(jailbreak, prompt injection, or simple mistake), this layer catches it.

Commands matching any pattern in BLOCKED_DESTRUCTIVE_PATTERNS are rejected
unless force_dangerous=True is explicitly passed.
"""

from __future__ import annotations

import re
from config import BLOCKED_DESTRUCTIVE_PATTERNS


def check_command_safety(command: str, force_dangerous: bool = False) -> dict:
    """Check if a command matches any destructive patterns.

    Args:
        command: The raw command (before wrapping).
        force_dangerous: If True, bypass the blocklist. This flag must
            be explicitly set by the LLM after the operator confirms.

    Returns:
        Dict with 'allowed' (bool) and 'reason' (str).
    """
    if force_dangerous:
        return {
            "allowed": True,
            "reason": "Blocklist bypassed with force_dangerous=True.",
            "matched_pattern": None,
        }

    for pattern in BLOCKED_DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return {
                "allowed": False,
                "reason": (
                    f"BLOCKED: Command matches destructive pattern '{pattern}'. "
                    "This command could cause irreversible data loss. "
                    "If the operator explicitly confirms, re-send with "
                    "force_dangerous=True."
                ),
                "matched_pattern": pattern,
            }

    return {
        "allowed": True,
        "reason": "Command passed safety check.",
        "matched_pattern": None,
    }
```

**Updated `write_to_anydesk` signature:**

```python
def write_to_anydesk(
    command: str,
    keystroke_delay_ms: int = 80,
    focus_delay_ms: int = 500,
    wrap: bool = True,
    base64_output: bool = False,
    force_dangerous: bool = False,    # NEW: bypass blocklist
) -> dict:
```

**The safety check runs on the ORIGINAL command** (before wrapping), so patterns like `Remove-VM` are caught regardless of the sanitizer wrapper around them.

---

## 4. NICE TO HAVE Features — Specs

### 4.1. N1 — Quick Diagnostic Mode

**Concept:** The operator says "modo diagnostico" or "diagnostic mode" and the LLM enters a mode where it can execute read-only commands from a whitelist without the full approval cycle.

**This is a system prompt behavior change, not a code change.** The server doesn't enforce approval — that's the LLM's job. The system prompt gains a new section:

```
## DIAGNOSTIC MODE

If the operator says "diagnostic mode" or "modo diagnostico", you may
execute commands from this whitelist WITHOUT asking for approval:

  hostname, whoami, Get-Date, Get-VM, Get-VMHost, Get-Service,
  Get-Process, Get-Volume, Get-NetAdapter, $PSVersionTable,
  Get-EventLog -LogName System -Newest 10,
  Get-WindowsFeature | Where Installed, Get-VMSwitch,
  Get-HotFix | Select -First 10

Rules in diagnostic mode:
- Still inject via write_to_anydesk (chunked, wrapped)
- Still say "Injected. Press Enter." (operator must still press Enter)
- Still read output after confirmation
- NEVER execute write/modify commands without approval
- Exit diagnostic mode if operator says "normal mode" or "modo normal"
```

---

### 4.2. N2 — Adaptive OCR

**Concept:** If OCR confidence is below 0.4, automatically retry with a zoomed screenshot of the detected console region.

**Updated flow in `read_from_anydesk`:**

```python
def read_from_anydesk(mode="ocr", region=None, ocr_available=True):
    result = _ocr_capture(region)

    if result["confidence"] < 0.4 and mode == "ocr":
        # Retry with 2x zoom on the detected console region
        console_region = _detect_console_region()
        if console_region:
            retry = _ocr_capture(console_region, upscale=4)  # Extra zoom
            if retry["confidence"] > result["confidence"]:
                result = retry
                result["warnings"].append(
                    f"Auto-retried with zoom. Confidence improved: "
                    f"{result['confidence']:.2f}"
                )

    # Strip garbled lines if still low confidence
    if result["confidence"] < 0.5:
        result["text"] = _strip_garbled_lines(result["text"])
        result["warnings"].append("Garbled lines stripped from low-confidence OCR.")

    return result
```

---

### 4.3. N3 — Session Recording & Export

**Concept:** Optional recording of the entire session to a directory for post-session audit.

**`session_state.py` addition:**

```python
def export_session_report(self) -> str:
    """Generate a markdown report of the entire session.

    Returns:
        Path to the exported markdown file.
    """
    report_dir = Path(os.path.expanduser(SESSION_RECORDING_DIR))
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"session_report_{ts}.md"

    lines = [
        f"# Session Report — {ts}",
        f"",
        f"**Window:** {self.pinned_title} (hwnd: {self.pinned_hwnd})",
        f"**PS Version:** {self.ps_version}",
        f"**Total Steps:** {self.step_counter}",
        f"",
        f"---",
        f"",
    ]

    for cmd in self.commands:
        status = "✅" if cmd.success else "❌" if cmd.success is False else "⏳"
        lines.append(f"### Step {cmd.step_number} {status}")
        lines.append(f"**Time:** {cmd.timestamp}")
        lines.append(f"**Command:** `{cmd.command_original}`")
        lines.append(f"**Wrapped:** {'Yes' if cmd.was_wrapped else 'No'}")
        lines.append(f"**Chars:** {cmd.char_count}")
        if cmd.output_text:
            lines.append(f"**Output:**")
            lines.append(f"```")
            lines.append(cmd.output_text[:500])
            lines.append(f"```")
        if cmd.output_confidence is not None:
            lines.append(f"**OCR Confidence:** {cmd.output_confidence:.2f}")
        if cmd.notes:
            lines.append(f"**Notes:** {cmd.notes}")
        lines.append(f"")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)
```

**New tool: `export_session`**

```python
@mcp.tool()
def export_session() -> dict:
    """Export the current session as a markdown report."""
    path = session.export_session_report()
    return {"status": "ok", "path": path, "note": f"Report saved to {path}"}
```

---

### 4.4. N4 — Named Sessions & Multi-Window

**Concept:** Support multiple pinned windows with human-readable names.

**Updated `window_manager.py`:**

```python
# Module-level: dict of named sessions
_sessions: dict[str, dict] = {}   # name -> {"hwnd": int, "title": str}
_active_session: str | None = None

def pin_session(hwnd: int, name: str) -> dict:
    """Pin a window with a human-readable name."""
    global _active_session
    title = win32gui.GetWindowText(hwnd)
    _sessions[name] = {"hwnd": hwnd, "title": title}
    _active_session = name
    return {"status": "pinned", "name": name, "hwnd": hwnd, "title": title}

def switch_session(name: str) -> dict:
    """Switch the active session by name."""
    global _active_session
    if name not in _sessions:
        return {"status": "error", "note": f"Session '{name}' not found."}
    _active_session = name
    return {"status": "switched", "name": name, **_sessions[name]}

def get_active_hwnd() -> int | None:
    """Return the hwnd of the active named session."""
    if _active_session and _active_session in _sessions:
        return _sessions[_active_session]["hwnd"]
    return _pinned_hwnd  # Fallback to v2 single-pin behavior
```

**New tools:**

```python
@mcp.tool()
def pin_session(hwnd: int, name: str) -> dict:
    """Pin an AnyDesk window with a human-readable name.
    
    Args:
        hwnd: Window handle to pin.
        name: Human-readable name (e.g., "DC01", "HyperV-Host").
    """

@mcp.tool()
def switch_session(name: str) -> dict:
    """Switch active session to a named window.
    
    Args:
        name: Name of a previously pinned session.
    """

@mcp.tool()
def list_sessions() -> dict:
    """List all named sessions with their hwnd and title."""
```

---

### 4.5. N5 — Output Pagination Detection

**Concept:** If a command returns more output than fits on screen, OCR only captures what's visible. Detect this and suggest pagination.

**Heuristic:** If OCR text ends without a command prompt (no `PS C:\>` or `C:\Users\>` at the end), there's probably more output below.

```python
def _detect_incomplete_output(text: str) -> bool:
    """Check if the output appears truncated (no prompt at the end)."""
    last_lines = text.strip().split("\n")[-3:]
    prompt_patterns = [r"PS [A-Z]:\\", r"[A-Z]:\\.*>"]
    for line in last_lines:
        for pattern in prompt_patterns:
            if re.search(pattern, line):
                return False  # Prompt found, output is complete
    return True  # No prompt — probably more output below
```

When detected, the tool adds a warning:

```python
if _detect_incomplete_output(result["text"]):
    result["warnings"].append(
        "Output appears truncated (no prompt detected at bottom). "
        "Consider re-running with: | Select -First 20"
    )
```

---

### 4.6. N6 — Dead Session Detection

**Concept:** Before injecting, check if the AnyDesk window shows a "connecting..." or "session interrupted" screen instead of an active session.

**Implementation:** Take a quick OCR read of the window title bar area and check for disconnect keywords.

```python
def check_session_alive() -> dict:
    """Quick check if the AnyDesk session appears connected.

    Uses OCR on the title bar region to detect disconnect indicators.
    """
    # Capture just the top 50px of the window (title bar area)
    hwnd = get_pinned_hwnd()
    x, y, w, h = _get_window_rect(hwnd)
    region = (x, y, w, min(50, h))

    text = _quick_ocr(region).lower()

    for pattern in ANYDESK_DISCONNECT_PATTERNS:
        if pattern in text:
            return {
                "status": "disconnected",
                "detected": pattern,
                "note": "AnyDesk session appears disconnected. Reconnect before proceeding.",
            }

    return {"status": "connected", "note": "Session appears active."}
```

---

### 4.7. N7 — Health Heartbeat

**Concept:** Periodic background check (every N minutes) that verifies the window is alive and the sanitizer file still exists.

**Implementation consideration:** MCP tools are request/response, not background tasks. The heartbeat would need to be triggered by the LLM periodically. The system prompt instructs Claude to call `check_health` every few interactions:

```
## HEALTH CHECKS

Every 5 interactions (approximately), call check_health to verify:
- The pinned window is still valid
- The AnyDesk session is still connected
- The sanitizer file still exists

If any check fails, STOP and report to the operator.
```

**Tool:**

```python
@mcp.tool()
def check_health() -> dict:
    """Verify window, session, and sanitizer are still healthy."""
    checks = {}

    # Window valid?
    hwnd = get_pinned_hwnd()
    checks["window_valid"] = hwnd is not None and win32gui.IsWindow(hwnd)

    # Session alive?
    if checks["window_valid"]:
        alive = check_session_alive()
        checks["session_connected"] = alive["status"] == "connected"
    else:
        checks["session_connected"] = False

    # Sanitizer present? (can't check remotely without injecting)
    checks["sanitizer_bootstrapped"] = session.sanitizer_bootstrapped

    all_ok = all(checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "note": "All systems go." if all_ok else "One or more checks failed. Review before continuing.",
    }
```

---

### 4.8. N8 — Command Recipes

**Concept:** Pre-built command sequences for common sysadmin tasks.

**`recipes.py`:**

```python
"""Pre-built command sequences for common sysadmin tasks.

Each recipe is a list of commands that the LLM executes sequentially,
following the standard inject -> Enter -> read cycle for each one.
"""

RECIPES = {
    "health_check": {
        "description": "General server health: hostname, uptime, CPU, memory, disk",
        "commands": [
            "hostname",
            "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime",
            "Get-Process | Measure-Object WorkingSet64 -Sum | Select @{N='TotalMemGB';E={[math]::Round($_.Sum/1GB,2)}}",
            "Get-Volume | Where DriveLetter | Select DriveLetter,FileSystemLabel,@{N='SizeGB';E={[math]::Round($_.Size/1GB)}},@{N='FreeGB';E={[math]::Round($_.SizeRemaining/1GB)}}",
        ],
    },
    "vm_status": {
        "description": "Hyper-V VM inventory: all VMs with state, CPU, memory",
        "commands": [
            "Get-VM | Select Name,State,@{N='CPU';E={$_.ProcessorCount}},@{N='MemGB';E={[math]::Round($_.MemoryAssigned/1GB,1)}},Uptime | Format-Table -Auto",
        ],
    },
    "network_check": {
        "description": "Network adapters, IPs, DNS, and connectivity",
        "commands": [
            "Get-NetAdapter | Where Status -eq Up | Select Name,InterfaceDescription,LinkSpeed",
            "Get-NetIPAddress -AddressFamily IPv4 | Where IPAddress -notlike '127.*' | Select InterfaceAlias,IPAddress",
            "Get-DnsClientServerAddress -AddressFamily IPv4 | Select InterfaceAlias,ServerAddresses",
        ],
    },
    "service_check": {
        "description": "Critical services status",
        "commands": [
            "Get-Service | Where {$_.StartType -eq 'Automatic' -and $_.Status -ne 'Running'} | Select Name,DisplayName,Status",
        ],
    },
    "event_errors": {
        "description": "Recent error events from System and Application logs",
        "commands": [
            "Get-EventLog -LogName System -EntryType Error -Newest 10 | Select TimeGenerated,Source,Message | Format-List",
        ],
    },
    "vmware_status": {
        "description": "VMware PowerCLI: VM inventory (requires active VIServer connection)",
        "commands": [
            "Get-VM | Select Name,PowerState,NumCpu,MemoryGB,VMHost | Format-Table -Auto",
            "Get-Datastore | Select Name,@{N='FreeGB';E={[math]::Round($_.FreeSpaceGB)}},@{N='CapGB';E={[math]::Round($_.CapacityGB)}} | Format-Table -Auto",
        ],
    },
}


def get_recipe(name: str) -> dict | None:
    """Return a recipe by name, or None if not found."""
    return RECIPES.get(name)


def list_recipes() -> list[dict]:
    """List all available recipes."""
    return [
        {"name": name, "description": r["description"], "steps": len(r["commands"])}
        for name, r in RECIPES.items()
    ]
```

**New tools:**

```python
@mcp.tool()
def list_recipes() -> dict:
    """List all available command recipes."""
    return {"recipes": list_recipes()}

@mcp.tool()
def get_recipe(name: str) -> dict:
    """Get a recipe's commands by name. Execute each command sequentially."""
    recipe = get_recipe(name)
    if recipe is None:
        available = [r["name"] for r in list_recipes()]
        return {"status": "error", "note": f"Recipe not found. Available: {available}"}
    return {"status": "ok", **recipe}
```

---

## 5. Compacted System Prompt (v3)

```
Sysadmin copilot for double-hop AnyDesk remote sessions (AnyDesk->AnyDesk->Server).
Strict security and latency constraints.

TOOLS: initialize_session, write_to_anydesk, read_from_anydesk, capture_screenshot,
send_cancel, get_session_history, log_step_result, check_health, export_session,
list_recipes, get_recipe, pin_session, switch_session, list_sessions.

RULES:
1. ONE command per step. No chaining with ; or &&. Max 2-stage pipeline.
2. SEQUENCE: explain->approve->inject->"Press Enter"->confirm->read->log->next.
3. NEVER: inject newlines, skip reading output, assume success, inject during execution.
4. DESTRUCTIVE commands (Remove-*, Format-*, Stop-*-Force): blocked server-side.
   Operator must explicitly confirm, then re-send with force_dangerous=True.
5. SANITIZATION: [IP-REDACTED] etc. = don't ask for redacted values. Ask operator to type manually.
6. SCREENSHOTS: describe CONTEXT ("3 VMs listed") never DATA (no IPs, names, accounts).
7. ERRORS: stop, explain expected vs actual, propose diagnostic (not fix).
8. FORMAT: **Step N — [desc]** / Risk: low|med|high / Command: `...` / Approve?
9. SESSION START: call initialize_session once. Then begin task.
10. [SANITIZER-MISSING] -> STOP. Re-bootstrap.
11. GUI MODE: screenshot->identify->PowerShell equivalent preferred->guide GUI if no cmdlet.
12. wrap=True default. wrap=False only for bootstrap/PS version/non-PS.
13. send_cancel when operator says abort or command appears hung.
14. check_health every ~5 interactions.

DIAGNOSTIC MODE (operator says "diagnostic mode"):
Skip approval for: hostname, whoami, Get-Date, Get-VM, Get-VMHost, Get-Service,
Get-Process, Get-Volume, Get-NetAdapter, $PSVersionTable.
Still inject, still require Enter, still read output. No write/modify without approval.

PS REFERENCE:
Hyper-V: Get-VM|Get-VMHost|Get-VMSwitch|Start-VM|Stop-VM|Checkpoint-VM|Export-VM|Move-VM
VMware:  Connect-VIServer|Get-VM|Get-VMHost|Get-Datastore|Start-VM|Stop-VM|New-Snapshot|Move-VM
General: Get-Service|Get-Process|Get-EventLog|Get-Volume|Get-NetAdapter|Get-HotFix|Test-Connection
```

**Size:** ~1.8KB (down from 5.5KB in v2 = **67% reduction**).

---

## 6. Updated Tool Registry

| Tool | Condition | New/Changed in v3? |
|---|---|---|
| `get_system_status` | ALWAYS | No |
| `initialize_session` | write_capable | **NEW (M6)** |
| `select_anydesk_window` | pywin32 | No (kept for manual use) |
| `write_to_anydesk` | write_capable | **CHANGED (M1, M4, M7)** |
| `read_from_anydesk` | read_capable | **CHANGED (M2, N2, N5)** |
| `capture_screenshot` | read_capable | **CHANGED (M2 — JPEG)** |
| `send_cancel` | write_capable | **NEW (M5)** |
| `bootstrap_sanitizer` | write_capable | **CHANGED (M3 — EncodedCommand)** |
| `get_session_history` | ALWAYS | **CHANGED (M2 — truncation)** |
| `log_step_result` | ALWAYS | No |
| `check_health` | read_capable | **NEW (N7)** |
| `export_session` | ALWAYS | **NEW (N3)** |
| `list_recipes` | ALWAYS | **NEW (N8)** |
| `get_recipe` | ALWAYS | **NEW (N8)** |
| `pin_session` | pywin32 | **NEW (N4)** |
| `switch_session` | pywin32 | **NEW (N4)** |
| `list_sessions` | pywin32 | **NEW (N4)** |

---

## 7. Implementation Order

Since v2 is working (with known bugs), implementation is incremental:

### Phase 1 — Critical Fixes (unblocks production use)

1. **`safety.py`** — new file, standalone, unit-testable
2. **`keyboard_injector.py`** — REWRITE with chunked injection + send_cancel + safety check
3. **`command_templates.py`** — EncodedCommand bootstrap + compressed wrapper
4. **Test:** inject a wrapped `Get-Date` — must complete without focus drift

### Phase 2 — Token Optimization (extends session duration)

5. **`screenshot.py`** — JPEG output, default 50% scale
6. **`screen_reader.py`** — garbled line stripping, adaptive OCR retry
7. **`session_state.py`** — truncated history response, session export
8. **`config.py`** — all v3 constants
9. **`SYSTEM_PROMPT.md`** — compacted v3 prompt
10. **Test:** run 15+ interactions without exhausting context

### Phase 3 — New Features

11. **`server.py`** — register all new tools, `initialize_session` tool
12. **`recipes.py`** — new file, command recipes
13. **`window_manager.py`** — named sessions, multi-window support
14. **Test:** full session: initialize -> recipe -> multi-command -> export

### Phase 4 — Polish (nice to have)

15. Dead session detection in `screen_reader.py`
16. Output pagination detection
17. Health heartbeat in system prompt
18. **Integration test:** full end-to-end session with all features

---

## 8. Updated Security Guardrails

| Risk | v2 Mitigation | v3 Improvement |
|---|---|---|
| Focus drift during injection | AttachThreadInput (first call only) | Chunked re-focus every 30 chars |
| Destructive commands | System prompt warning only | **Server-side blocklist** (safety.py) |
| PII in screenshots | System prompt instruction | Unchanged (sufficient) |
| Sanitizer bypass | wrap=True default | Unchanged (sufficient) |
| Command hangs indefinitely | Manual Ctrl+C by operator | **send_cancel tool** |
| Session disconnects silently | Not detected | **Dead session detection** |
| Audit trail | Append-only log file | **+ Session export to markdown** |
| Partial injection (new risk) | N/A | **chars_injected field** reports exact progress |

---

## 9. Migration from v2 to v3

1. **No files deleted** — v3 adds/modifies only.
2. New files: `safety.py`, `recipes.py`.
3. Rewritten: `keyboard_injector.py` (biggest change).
4. Updated: `command_templates.py`, `screenshot.py`, `screen_reader.py`, `session_state.py`, `config.py`, `server.py`, `window_manager.py`, `SYSTEM_PROMPT.md`.
5. `requirements.txt` unchanged — no new dependencies.
6. `claude_desktop_config.json` unchanged — same entry point.
7. Restart Claude Desktop after all changes.

---

## 10. Testing Checklist

### Phase 1 — Critical
- [ ] `check_command_safety("Remove-VM -Name X")` returns blocked
- [ ] `check_command_safety("Get-VM")` returns allowed
- [ ] `check_command_safety("Remove-VM", force_dangerous=True)` returns allowed
- [ ] `write_to_anydesk("Get-Date", wrap=True)` completes without focus drift
- [ ] Chunked injection logs correct `chunks_used` count
- [ ] Partial injection (simulate focus loss) reports exact `chars_injected`
- [ ] `send_cancel()` sends Ctrl+C to the correct window
- [ ] `bootstrap_sanitizer()` injects EncodedCommand (single line, no newlines)
- [ ] Bootstrap works on PS 5.1
- [ ] Bootstrap works on PS 7+
- [ ] Compressed wrapper produces valid PowerShell

### Phase 2 — Token Optimization
- [ ] `capture_screenshot()` returns JPEG by default (not PNG)
- [ ] JPEG at quality 60, scale 50% is < 50KB base64
- [ ] Garbled OCR lines are stripped when confidence < 0.5
- [ ] Adaptive OCR retries with zoom when confidence < 0.4
- [ ] Session history truncates output_text to 80 chars
- [ ] System prompt is <= 2KB
- [ ] 15+ interaction session doesn't exhaust context

### Phase 3 — New Features
- [ ] `initialize_session()` completes all 4 setup steps in 1 tool call
- [ ] `list_recipes()` returns available recipes
- [ ] `get_recipe("health_check")` returns commands
- [ ] `pin_session(hwnd, "DC01")` works
- [ ] `switch_session("DC01")` changes active window
- [ ] `export_session()` generates readable markdown report

### Phase 4 — Polish
- [ ] Dead session detection identifies "connecting..." screen
- [ ] Output pagination warning appears when prompt not detected
- [ ] `check_health()` returns all checks green on healthy session
