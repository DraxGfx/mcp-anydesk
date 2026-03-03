# MCP AnyDesk Server v2 — Architecture Decision Record & Implementation Spec

> **Purpose:** This document is the single source of truth for implementing v2 of the MCP AnyDesk Server. It contains bug fixes from v1 testing, new features, and architectural improvements. All decisions are finalized. Do not deviate without explicit operator instruction.
>
> **Prerequisite:** Read `MCP_ANYDESK_SPEC.md` (v1 spec) first. This document builds on top of it — unchanged modules are not repeated here.

---

## 1. Issues Found in v1 Testing

### 1.1. CRITICAL — SetForegroundWindow Fails on First Attempt

**Symptom:** `write_to_anydesk` throws `(6, 'SetForegroundWindow', 'Controlador no válido.')` on the first call. After re-listing windows and re-pinning the same hwnd, it works. This happened consistently across multiple test sessions.

**Root cause:** Windows has strict rules about which process can call `SetForegroundWindow()`. A process can only set the foreground window if:
- It is the foreground process, OR
- It was activated by the foreground process, OR
- It received the last input event

When Claude Desktop launches the MCP server as a subprocess, the Python process does NOT have foreground lock. The first call to `SetForegroundWindow()` fails with error 6 (ERROR_INVALID_HANDLE — misleading error name, it's actually a permission issue).

**Fix:** Use the `AllowSetForegroundWindow` + `AttachThreadInput` workaround:

```python
import win32gui
import win32con
import win32process
import win32api
import ctypes

def _force_foreground(hwnd: int) -> None:
    """Force a window to the foreground using thread input attachment.
    
    This bypasses Windows' foreground lock restriction by temporarily
    attaching the calling thread's input to the foreground window's thread.
    """
    # Get current foreground window's thread
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
    
    # Get our thread
    our_thread = win32api.GetCurrentThreadId()
    
    # Attach our thread input to the foreground thread
    if fg_thread != our_thread:
        ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, True)
    
    # Now we have permission to set foreground
    try:
        # Restore if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    finally:
        # Always detach
        if fg_thread != our_thread:
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, False)
```

**Where to apply:** Replace the current `focus_and_verify()` implementation in `window_manager.py`. The rest of the function (sleep + verify) remains the same.

**Testing:** This must work on the FIRST call after pinning, without needing to re-list or re-pin windows.

---

### 1.2. MEDIUM — False Positive Window Detection

**Symptom:** Windows whose title contains "anydesk" as a substring (e.g., `mcp_anydesk_server - Cursor`, `mcp_anydesk_server - Explorador de archivos`) were detected as AnyDesk sessions.

**Status:** Already fixed in v1 (post-testing patch). The fix uses a regex word-boundary check on the title and class-name matching. Verify the fix is in place:

```python
import re
is_anydesk_class = "anydesk" in class_name.lower()
is_anydesk_title = bool(re.search(r'(?<![_\w])AnyDesk(?![_\w])', title))
```

Confirm this is applied in BOTH `window_manager.py` AND `startup_checks.py`.

---

### 1.3. LOW — OCR Confidence Very Low (0.33)

**Symptom:** OCR returned heavily garbled text with 33% confidence. The taskbar, desktop icons, and non-console areas pollute the OCR.

**Fix:** Two improvements:
1. **Auto-crop to console area:** After capturing the full AnyDesk window, detect the console/terminal region (dark background rectangle) using contour detection, and OCR only that region.
2. **Expose region parameters more prominently:** The `read_from_anydesk` tool already accepts region coordinates, but the LLM doesn't know where the console is. The new `capture_screenshot` tool (see section 3.3) solves this by letting the LLM see the screen first, then target a specific region.

---

## 2. Architecture Changes for v2

### 2.1. Updated Project Structure

```
mcp_anydesk_server/
├── server.py                 # MCP entry point (updated: new tools)
├── startup_checks.py         # Preflight validation (unchanged)
├── config.py                 # Defaults (updated: new constants)
├── window_manager.py         # Window management (FIXED: SetForegroundWindow)
├── keyboard_injector.py      # write_to_anydesk (unchanged)
├── screen_reader.py          # read_from_anydesk (updated: auto-crop)
├── screenshot.py             # NEW: capture_screenshot tool
├── sanitizer.py              # PII regex layer 2 (unchanged)
├── command_templates.py      # PS templates (updated: wrap_command exposed)
├── session_state.py          # NEW: session history + audit log
├── requirements.txt          # (unchanged)
├── MCP_ANYDESK_SPEC.md       # v1 spec (kept for reference)
└── MCP_ANYDESK_SPEC_V2.md    # This document
```

### 2.2. Updated config.py

Add these new constants to the existing `config.py`:

```python
# --- v2 additions ---

# Screenshot
SCREENSHOT_DIR = "~/.mcp_anydesk/screenshots"  # Local only, never sent to API
SCREENSHOT_MAX_KEPT = 20                        # Auto-cleanup oldest

# Audit log
AUDIT_LOG_DIR = "~/.mcp_anydesk/logs"
AUDIT_LOG_ROTATE_MB = 10

# Session
SESSION_COMMAND_HISTORY_MAX = 100

# Auto-read delay (seconds to wait after operator confirms Enter)
AUTO_READ_DELAY_S = 2.0

# Console auto-crop
CONSOLE_BG_THRESHOLD = 50          # Pixels darker than this = console background
CONSOLE_MIN_AREA_RATIO = 0.15      # Region must be >= 15% of window to count
```

---

## 3. New Features

### 3.1. Exposed Tool: `wrap_command`

**Problem:** In v1, the LLM had to know about the sanitizer wrapper format and construct it manually (or the bootstrap_sanitizer tool injected the bootstrap, but individual commands were injected raw). This means commands could bypass the sanitizer if the LLM forgot to wrap them.

**Solution:** Expose `wrap_command` and `wrap_command_base64` as MCP tools. The LLM calls `wrap_command("Get-VM")` and gets back the full wrapped string ready to inject. Better yet: make `write_to_anydesk` accept a `wrapped: bool = True` parameter that automatically wraps the command before injecting.

**Tool signature (updated write_to_anydesk):**

```python
write_to_anydesk(
    command: str,
    keystroke_delay_ms: int = 80,
    focus_delay_ms: int = 500,
    wrap: bool = True,          # NEW: auto-wrap with sanitizer check
    base64_output: bool = False  # NEW: use base64 wrapper instead
) -> dict
```

**Behavior:**
- `wrap=True` (default): The server calls `wrap_command(command)` internally and injects the wrapped version. The LLM sends clean commands like `Get-VM`, the server handles the wrapping.
- `wrap=False`: Raw injection (for bootstrap, PS version detection, or non-PowerShell contexts).
- `base64_output=True`: Uses `wrap_command_base64()` instead of `wrap_command()`.

**Return value updated:**

```python
{
    "status": "injected",
    "char_count": int,           # Characters actually injected (wrapped length)
    "original_command": str,     # What the LLM sent
    "wrapped": bool,             # Whether wrapping was applied
    "elapsed_ms": int,
    "note": "Enter NOT pressed — awaiting operator confirmation"
}
```

---

### 3.2. Tool: `capture_screenshot`

**Problem:** The LLM cannot see the remote screen. It can only get OCR text, which at 33% confidence is often garbled and loses all spatial/visual context. For GUI environments (Hyper-V Manager, vSphere Client, Server Manager), OCR is useless — the LLM needs to SEE the screen.

**Solution:** A new tool that captures the AnyDesk window and returns the screenshot as a base64-encoded image that Claude can see natively (Claude has vision capabilities).

**Tool signature:**

```python
capture_screenshot(
    region_x: int = 0,
    region_y: int = 0,
    region_w: int = 0,
    region_h: int = 0,
    scale_percent: int = 100     # Downscale for token efficiency
) -> dict
```

**Returns:**

```python
{
    "status": "ok",
    "image_base64": str,          # PNG base64 for Claude's vision
    "width": int,
    "height": int,
    "saved_to": str | None,       # Local path if audit saving enabled
    "note": "Screenshot captured. This image is LOCAL ONLY and was not sent through any external API other than this conversation."
}
```

**Implementation details:**

- Capture via `mss` (same as `read_from_anydesk`).
- Convert to PNG in memory via `cv2.imencode('.png', img)`.
- Base64-encode the PNG bytes.
- Optionally downscale to reduce token usage (a 1920x1080 screenshot at 50% = 960x540, still readable).
- **PII concern:** The screenshot DOES contain raw screen content including potentially sensitive data. However, it goes to the same Claude conversation that already receives OCR text. The mitigation is:
  1. Screenshots are never saved to disk by default (only in memory).
  2. The system prompt instructs Claude to never reproduce PII it sees in screenshots.
  3. Optional: save locally for the sysadmin's own audit trail (configurable, off by default).

**Why this is different from `read_from_anydesk`:**
- `read_from_anydesk` returns TEXT (OCR-extracted). Good for console output.
- `capture_screenshot` returns an IMAGE. Good for GUIs, visual verification, and situations where OCR fails.

**The LLM uses both together:**
1. `capture_screenshot` to SEE what's on screen (understand context)
2. `read_from_anydesk` with a targeted region to extract specific text (a hostname, a VM name, an error message)

---

### 3.3. Tool: `detect_ps_version`

**Problem:** In v1, detecting the PowerShell version required the full inject -> confirm Enter -> read cycle. This is 3 tool calls + user interaction for a setup step.

**Solution:** Dedicated tool that injects `$PSVersionTable.PSVersion.Major`, but since it still requires Enter, it's more of a convenience wrapper that stores the result in session state.

**Tool signature:**

```python
detect_ps_version() -> dict
```

**Returns:**

```python
{
    "status": "injected",
    "command": "$PSVersionTable.PSVersion.Major",
    "note": "Press Enter, then tell me the result or I'll read it with read_from_anydesk."
}
```

**After reading the result, the LLM calls:**

```python
set_ps_version(major: int) -> dict   # Stores in session state
```

This is a lightweight improvement — the real value is that the session state remembers the PS version for command adaptation.

---

### 3.4. Session State & Audit Log (`session_state.py`)

**Problem:** v1 is stateless between tool calls. There's no history of what commands were executed, no audit trail, and no way for the LLM to reference previous steps.

**Solution:** A new module that maintains session state and writes an audit log.

**Data structure:**

```python
@dataclass
class CommandRecord:
    step_number: int
    timestamp: str               # ISO 8601
    command_original: str        # What the LLM intended
    command_injected: str        # What was actually typed (wrapped)
    was_wrapped: bool
    char_count: int
    injection_elapsed_ms: int
    operator_confirmed: bool     # Did the operator say they pressed Enter?
    output_text: str | None      # What read_from_anydesk returned
    output_confidence: float | None
    sanitized_counts: dict | None
    success: bool | None         # Did the command succeed? (LLM's assessment)
    notes: str | None            # LLM's analysis


@dataclass
class SessionState:
    pinned_hwnd: int | None = None
    pinned_title: str | None = None
    ps_version: int | None = None
    sanitizer_bootstrapped: bool = False
    commands: list[CommandRecord] = field(default_factory=list)
    step_counter: int = 0
    
    def next_step(self) -> int:
        self.step_counter += 1
        return self.step_counter
```

**Tool: `get_session_history`**

```python
get_session_history(last_n: int = 10) -> dict
```

Returns the last N commands executed in the session with their outputs. This lets the LLM reference previous steps ("in step 3 we saw that the VM was stopped...").

**Tool: `log_step_result`**

```python
log_step_result(
    step_number: int,
    success: bool,
    notes: str = ""
) -> dict
```

The LLM calls this after analyzing the output of a command to record whether it succeeded and any observations. This completes the audit record.

**Audit log format (written to disk):**

```
[2026-02-23T16:30:45Z] STEP 1 | hostname | WRAPPED | 87 chars | INJECTED OK
[2026-02-23T16:30:52Z] STEP 1 | OUTPUT: DESKTOP-PLBORRN | CONFIDENCE: 0.82 | SUCCESS
[2026-02-23T16:31:15Z] STEP 2 | Get-VM | Select Name,State | WRAPPED | 142 chars | INJECTED OK
[2026-02-23T16:31:28Z] STEP 2 | OUTPUT: [3 VMs listed] | CONFIDENCE: 0.71 | SUCCESS
```

**Location:** `~/.mcp_anydesk/logs/session_YYYYMMDD_HHMMSS.log`

---

### 3.5. GUI Copilot Mode (System Prompt Enhancement)

**Problem:** The sysadmin doesn't always work in terminals. Hyper-V Manager, vSphere Client (via browser), Server Manager, and other GUIs require visual guidance.

**Solution:** This is NOT a code change — it's a System Prompt enhancement. With `capture_screenshot` available, Claude can now:

1. See the screen via screenshot
2. Identify what GUI is open (Hyper-V Manager, vSphere, Server Manager, etc.)
3. Either:
   a. **Translate the GUI action to PowerShell** (preferred — auditable, reversible)
   b. **Guide the operator step-by-step through the GUI** (when no cmdlet equivalent exists)
4. After the operator performs a GUI action, take another screenshot to verify

**Claude NEVER clicks or interacts with the mouse.** All GUI actions are performed by the operator.

**PowerShell equivalents Claude should know about:**

**Hyper-V Manager:**
| GUI Action | PowerShell |
|---|---|
| List VMs | `Get-VM` |
| Start VM | `Start-VM -Name 'VMName'` |
| Stop VM | `Stop-VM -Name 'VMName'` |
| Create checkpoint | `Checkpoint-VM -Name 'VMName' -SnapshotName 'Description'` |
| View virtual switches | `Get-VMSwitch` |
| Move VM | `Move-VM -Name 'VMName' -DestinationHost 'HostName'` |
| View VM resources | `Measure-VM -Name 'VMName'` |
| Configure memory | `Set-VMMemory -VMName 'VMName' -DynamicMemoryEnabled $true` |
| View VM network adapters | `Get-VMNetworkAdapter -VMName 'VMName'` |
| Export VM | `Export-VM -Name 'VMName' -Path 'D:\Exports'` |

**VMware vSphere (requires PowerCLI module):**
| GUI Action | PowerCLI |
|---|---|
| Connect to vCenter | `Connect-VIServer -Server 'vcenter.local'` |
| List VMs | `Get-VM` |
| Start/Stop VM | `Start-VM`, `Stop-VM` |
| Create snapshot | `New-Snapshot -VM 'VMName' -Name 'Description'` |
| List snapshots | `Get-Snapshot -VM 'VMName'` |
| List ESXi hosts | `Get-VMHost` |
| List datastores | `Get-Datastore` |
| vMotion | `Move-VM -VM 'VMName' -Destination 'ESXi02'` |
| View clusters | `Get-Cluster` |
| VM resources | `Get-VM 'VMName' \| Select NumCpu,MemoryGB` |

**Windows Server Manager:**
| GUI Action | PowerShell |
|---|---|
| List roles | `Get-WindowsFeature \| Where Installed` |
| Add role | `Install-WindowsFeature -Name 'Web-Server'` |
| List services | `Get-Service` |
| View event log | `Get-EventLog -LogName System -Newest 20` |
| Check disk | `Get-Volume` |
| View network | `Get-NetAdapter` |
| Check updates | `Get-HotFix \| Sort InstalledOn -Desc \| Select -First 10` |

---

## 4. Updated System Prompt for v2

This replaces the v1 system prompt entirely.

```
You are an operations assistant for a Sysadmin working in a double-hop remote
environment (AnyDesk -> AnyDesk -> Hyper-V / VMware / Windows Server). You
operate under strict security and latency constraints.

You have access to these MCP tools:
- get_system_status: Check dependencies and capabilities
- select_anydesk_window: List/pin AnyDesk windows
- bootstrap_sanitizer: Inject PII sanitization script into remote session
- write_to_anydesk: Inject keystrokes (auto-wraps with sanitizer by default)
- read_from_anydesk: Extract text via OCR or Base64 decoding
- capture_screenshot: Take a screenshot of the remote screen (returns image)
- get_session_history: View previous commands and outputs from this session
- log_step_result: Record whether a step succeeded or failed

## ABSOLUTE RULES — NEVER VIOLATE THESE

1. ATOMICITY: Every action must be ONE single command of 1-2 lines maximum.
   NEVER chain commands with `;`, `&&`, or complex pipelines of more than
   2 stages. Split long pipelines into steps with intermediate variables.

2. MANDATORY SEQUENCE for each step:
   a) EXPLAIN what the command does, its effect, and its risk.
   b) WAIT FOR APPROVAL: Ask "Do you approve?" Do NOT call write_to_anydesk
      until the operator says "yes" or "si" or equivalent affirmation.
   c) INJECT: Call write_to_anydesk. It types WITHOUT pressing Enter.
   d) SAY: "Injected. Verify and press Enter. Say 'ok' when done."
      (Keep this short — one line, not a paragraph.)
   e) When operator confirms (any affirmation: "ok", "done", "listo", "ya"):
      Immediately call read_from_anydesk (or capture_screenshot if GUI).
      Then analyze and propose the next step. Do NOT ask "should I read?"
      — just read automatically after confirmation.
   f) Call log_step_result to record the outcome.

3. NEVER:
   - Include \n, \r, `n, `r, or any newline encoding in injected text
   - Propose multiple steps without reading the output of each one first
   - Assume a command succeeded without reading the output
   - Inject while a previous command is still executing
   - Propose destructive commands (Remove-Item -Recurse, Format-*,
     Clear-Content, Stop-VM -Force, Remove-VM, Remove-Snapshot) without
     explicit WARNING in uppercase and explicit confirmation

4. SANITIZATION: All text output from read_from_anydesk is PII-sanitized.
   Do NOT ask the operator for redacted data. If you see [IP-REDACTED] or
   [ACCOUNT-REDACTED], work with the redacted tokens. If you need a specific
   IP or hostname for a command, ask the operator to type it manually.

5. SCREENSHOTS: When you use capture_screenshot, the image may contain
   sensitive information. NEVER reproduce, quote, or describe specific PII
   you see in screenshots (usernames, IPs, hostnames). Describe the CONTEXT
   ("I see Hyper-V Manager with 3 VMs listed") not the DATA.

6. ERRORS AND ROLLBACK: If a command fails or output is unexpected:
   - STOP immediately
   - Explain expected vs. actual
   - Propose a DIAGNOSTIC command (not a fix) as the next step
   - If you propose a fix, include the explicit rollback plan

7. COMMUNICATION FORMAT: Be concise. No greetings or filler.

   **Step N — [Short description]**
   Purpose: [what this step achieves]
   Risk: [low/medium/high] — [1-line explanation]
   Command: `[the exact command]`
   Do you approve?

   After reading output, analyze in 1-2 sentences max, then propose next step.

8. SESSION START: At the beginning of every session, execute these 4 setup
   steps (you may proceed without waiting for approval on these):
   a) get_system_status — verify dependencies
   b) select_anydesk_window — identify and pin target window
   c) bootstrap_sanitizer — inject PII sanitization script
   d) write_to_anydesk("$PSVersionTable.PSVersion.Major", wrap=False)
   Only after these 4 steps, begin the operator's task.

9. SANITIZER SENTINEL: If read_from_anydesk returns text containing
   "[SANITIZER-MISSING]", STOP ALL OPERATIONS. Re-run bootstrap_sanitizer
   before continuing. NEVER execute commands without active sanitization.

10. GUI MODE: When the operator is in a graphical interface (Hyper-V Manager,
    vSphere Client, Server Manager, MMC consoles):
    a) Use capture_screenshot to see the screen
    b) PREFER translating the action to PowerShell (see reference tables below)
       — PowerShell is auditable, reversible, and less error-prone
    c) If no PowerShell equivalent exists, guide step-by-step through the GUI:
       describe exactly where to click, then screenshot to verify each step
    d) NEVER interact with the mouse — all GUI actions are done by the operator

11. COMMAND WRAPPING: write_to_anydesk wraps commands with the sanitizer
    automatically (wrap=True is the default). Only set wrap=False for:
    - Bootstrap commands
    - PS version detection
    - Non-PowerShell contexts (CMD, bash)
    Use base64_output=True for critical output (hashes, GUIDs, dense tables).

## POWERSHELL QUICK REFERENCE

### Hyper-V
Get-VM | Get-VMHost | Get-VMSwitch | Get-VMNetworkAdapter -VMName 'X'
Start-VM | Stop-VM | Restart-VM | Checkpoint-VM | Export-VM
Move-VM -Name 'X' -DestinationHost 'Y' | Set-VMMemory | Measure-VM

### VMware PowerCLI (if module is installed)
Connect-VIServer | Get-VM | Get-VMHost | Get-Datastore | Get-Cluster
Start-VM | Stop-VM | New-Snapshot | Get-Snapshot | Remove-Snapshot
Move-VM -VM 'X' -Destination 'Y'

### Windows Server General
Get-Service | Get-Process | Get-EventLog -LogName System -Newest 20
Get-WindowsFeature | Get-Volume | Get-NetAdapter | Get-HotFix
Test-Connection | Resolve-DnsName | Get-NetTCPConnection
```

---

## 5. Updated Tool Registry for server.py

Tools to register (conditionally, based on preflight):

| Tool | Condition | New in v2? |
|---|---|---|
| `get_system_status` | ALWAYS | No |
| `select_anydesk_window` | pywin32 available | No |
| `bootstrap_sanitizer` | write_capable | No |
| `write_to_anydesk` | write_capable | Updated (wrap param) |
| `read_from_anydesk` | read_capable | No |
| `capture_screenshot` | read_capable | **YES** |
| `get_session_history` | ALWAYS | **YES** |
| `log_step_result` | ALWAYS | **YES** |

---

## 6. Implementation Order for v2

Since v1 is already working, the implementation order is incremental:

1. **FIX `window_manager.py`** — Replace `SetForegroundWindow` with `AttachThreadInput` workaround. This is the highest-priority fix. Test that `write_to_anydesk` works on the FIRST call after pinning.

2. **Add `session_state.py`** — Implement `SessionState`, `CommandRecord`, audit log writing. Unit-testable without AnyDesk.

3. **Update `config.py`** — Add v2 constants.

4. **Add `screenshot.py`** — Implement `capture_screenshot`. Uses same `mss` capture as `screen_reader.py` but returns base64-encoded PNG instead of OCR text.

5. **Update `keyboard_injector.py`** — Add `wrap` and `base64_output` parameters to `write_to_anydesk`. Import and call `wrap_command()` / `wrap_command_base64()` when `wrap=True`.

6. **Update `screen_reader.py`** — Add auto-crop logic for console detection (optional improvement, not blocking).

7. **Update `server.py`** — Register new tools (`capture_screenshot`, `get_session_history`, `log_step_result`), update `write_to_anydesk` signature, wire session state.

8. **Update `SYSTEM_PROMPT.md`** — Replace with v2 system prompt from section 4.

9. **Integration test** — Full end-to-end: pin window -> bootstrap -> screenshot -> inject -> read -> verify audit log.

---

## 7. Detailed Module Specs (New/Changed Only)

### 7.1. `screenshot.py`

```python
"""Screenshot capture for GUI copilot mode.

Returns base64-encoded PNG images for Claude's vision capabilities.
Unlike read_from_anydesk (which returns OCR text), this returns the
raw image so Claude can see and interpret GUI elements.
"""

def capture_screenshot(
    region_x: int = 0,
    region_y: int = 0,
    region_w: int = 0,
    region_h: int = 0,
    scale_percent: int = 100,
) -> dict:
    """Capture the pinned AnyDesk window as a PNG image.

    Args:
        region_x/y/w/h: Optional sub-region. 0 = full window.
        scale_percent: Downscale factor (50 = half resolution).
            Use lower values for large screens to reduce token usage.

    Returns:
        Dict with base64 PNG image and metadata.
    """
```

**Implementation notes:**
- Capture via `mss` (same as screen_reader).
- Convert BGRA -> BGR via numpy slicing.
- If `scale_percent < 100`: resize with `cv2.resize` using `INTER_AREA` (best for downscaling).
- Encode to PNG: `cv2.imencode('.png', img)`.
- Base64-encode the bytes.
- Return as `{"image_base64": "...", "width": w, "height": h}`.
- The LLM receives this as an image it can see and analyze.

**Token budget consideration:** A 1920x1080 PNG screenshot is roughly 2-5MB raw, but Claude processes images by visual tokens, not bytes. At 100% scale, a full HD screenshot uses approximately 1500 tokens. At 50%, ~750 tokens. Default to 100% and let the LLM adjust if needed.

---

### 7.2. `session_state.py`

```python
"""Session state management and audit logging.

Maintains in-memory command history and writes an append-only
audit log to disk for the sysadmin's records.
"""

import os
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path

from config import AUDIT_LOG_DIR, SESSION_COMMAND_HISTORY_MAX


@dataclass
class CommandRecord:
    step_number: int
    timestamp: str
    command_original: str
    command_injected: str
    was_wrapped: bool
    char_count: int
    injection_elapsed_ms: int
    operator_confirmed: bool = False
    output_text: str | None = None
    output_confidence: float | None = None
    sanitized_counts: dict | None = None
    success: bool | None = None
    notes: str | None = None


class SessionState:
    def __init__(self):
        self.pinned_hwnd: int | None = None
        self.pinned_title: str | None = None
        self.ps_version: int | None = None
        self.sanitizer_bootstrapped: bool = False
        self.commands: list[CommandRecord] = []
        self.step_counter: int = 0
        self._log_file = self._init_log_file()

    def next_step(self) -> int:
        self.step_counter += 1
        return self.step_counter

    def add_command(self, record: CommandRecord) -> None:
        self.commands.append(record)
        self._write_log(record)
        # Trim oldest if over limit
        if len(self.commands) > SESSION_COMMAND_HISTORY_MAX:
            self.commands = self.commands[-SESSION_COMMAND_HISTORY_MAX:]

    def get_history(self, last_n: int = 10) -> list[dict]:
        return [asdict(c) for c in self.commands[-last_n:]]

    def update_last_command(self, **kwargs) -> None:
        if self.commands:
            for key, value in kwargs.items():
                if hasattr(self.commands[-1], key):
                    setattr(self.commands[-1], key, value)
            self._write_log_update(self.commands[-1])

    def _init_log_file(self) -> Path:
        log_dir = Path(os.path.expanduser(AUDIT_LOG_DIR))
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return log_dir / f"session_{timestamp}.log"

    def _write_log(self, record: CommandRecord) -> None:
        line = (
            f"[{record.timestamp}] STEP {record.step_number} "
            f"| {record.command_original} "
            f"| {'WRAPPED' if record.was_wrapped else 'RAW'} "
            f"| {record.char_count} chars "
            f"| INJECTED"
        )
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _write_log_update(self, record: CommandRecord) -> None:
        parts = [f"[{record.timestamp}] STEP {record.step_number}"]
        if record.output_text is not None:
            # Truncate output for log readability
            short = record.output_text[:100].replace('\n', ' ')
            parts.append(f"OUTPUT: {short}")
        if record.output_confidence is not None:
            parts.append(f"CONFIDENCE: {record.output_confidence:.2f}")
        if record.success is not None:
            parts.append("SUCCESS" if record.success else "FAILED")
        if record.notes:
            parts.append(f"NOTE: {record.notes}")
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(" | ".join(parts) + "\n")


# Module-level singleton
session = SessionState()
```

---

### 7.3. Updated `window_manager.py` — SetForegroundWindow Fix

Replace the `focus_and_verify` function entirely:

```python
def focus_and_verify(focus_delay_ms: int = DEFAULT_FOCUS_DELAY_MS) -> None:
    """Bring the pinned window to the foreground and verify focus.

    Uses AttachThreadInput to bypass Windows' foreground lock restriction,
    which prevents background processes from stealing focus.

    Raises:
        RuntimeError: If no window is pinned or focus verification fails.
    """
    if _pinned_hwnd is None:
        raise RuntimeError(
            "No AnyDesk window pinned. Use select_anydesk_window first."
        )

    if not win32gui.IsWindow(_pinned_hwnd):
        raise RuntimeError(
            f"Pinned hwnd {_pinned_hwnd} is no longer valid. "
            "The AnyDesk session may have been closed."
        )

    import ctypes
    import win32process
    import win32api

    # Get foreground window's thread
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0]
    our_thread = win32api.GetCurrentThreadId()

    attached = False
    try:
        # Attach our thread to the foreground thread's input queue
        if fg_thread != our_thread:
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, True)
            attached = True

        # Restore if minimized
        if win32gui.IsIconic(_pinned_hwnd):
            win32gui.ShowWindow(_pinned_hwnd, win32con.SW_RESTORE)

        win32gui.BringWindowToTop(_pinned_hwnd)
        win32gui.SetForegroundWindow(_pinned_hwnd)

    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(our_thread, fg_thread, False)

    time.sleep(focus_delay_ms / 1000.0)

    foreground = win32gui.GetForegroundWindow()
    if foreground != _pinned_hwnd:
        raise RuntimeError(
            "Focus lost, injection aborted. "
            f"Expected hwnd {_pinned_hwnd}, got {foreground}."
        )
```

**Additional imports needed at top of file:**
- `ctypes` (stdlib)
- `win32process` (from pywin32, already a dependency)
- `win32api` (from pywin32, already a dependency)

---

## 8. Security Guardrails Summary (v2 additions)

| Risk | Mitigation | Layer |
|---|---|---|
| All v1 guardrails | Unchanged — see v1 spec | All |
| SetForegroundWindow fails silently | AttachThreadInput + verify | `window_manager.py` |
| Commands bypass sanitizer | Auto-wrap by default (wrap=True) | `keyboard_injector.py` |
| PII visible in screenshots | System prompt forbids reproducing PII from images | System Prompt |
| No audit trail | Append-only log file per session | `session_state.py` |
| LLM forgets previous context | Session history accessible via tool | `session_state.py` |
| GUI actions without PowerShell equivalent | Operator-only mouse, Claude guides verbally | System Prompt |
| Screenshots accumulate on disk | Auto-cleanup (keep last 20) | `config.py` |

---

## 9. Migration from v1 to v2

1. Keep all v1 files — v2 modifies them in place, no files are deleted.
2. Apply the `window_manager.py` fix FIRST (highest priority).
3. Add new files: `screenshot.py`, `session_state.py`.
4. Update existing files: `config.py`, `keyboard_injector.py`, `server.py`, `SYSTEM_PROMPT.md`.
5. No changes to `requirements.txt` — no new dependencies needed.
6. No changes to `claude_desktop_config.json` — same entry point.
7. Restart Claude Desktop after all changes are applied.

---

## 10. Testing Checklist for v2

- [ ] `write_to_anydesk` works on FIRST call after pinning (no re-pin needed)
- [ ] `write_to_anydesk` with `wrap=True` injects the sanitizer-wrapped command
- [ ] `write_to_anydesk` with `wrap=False` injects raw text (for bootstrap)
- [ ] `capture_screenshot` returns a valid base64 PNG that Claude can see
- [ ] `capture_screenshot` with `scale_percent=50` returns a smaller image
- [ ] `get_session_history` returns previous commands and outputs
- [ ] `log_step_result` writes to the audit log file
- [ ] Audit log file is created in `~/.mcp_anydesk/logs/`
- [ ] False-positive window detection is fixed (folders named "anydesk" not matched)
- [ ] `[SANITIZER-MISSING]` sentinel still triggers a full stop
- [ ] System prompt v2 is loaded in Claude Desktop
- [ ] GUI copilot flow: screenshot -> identify GUI -> suggest PowerShell equivalent
