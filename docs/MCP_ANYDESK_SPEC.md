# MCP AnyDesk Server — Architecture Decision Record & Implementation Spec

> **Purpose:** This document is the single source of truth for implementing the MCP AnyDesk Server. All architectural decisions have been finalized. Do not deviate from these specs without explicit instruction from the operator.

---

## 1. Problem Statement

A sysadmin operates through a high-friction remote access chain:

```
Local Machine → AnyDesk → AnyDesk → Target Server (Hyper-V)
```

- The clipboard is completely broken due to the double proprietary hop.
- Video compression is severe (double-encoded H.264/H.265).
- **It is strictly forbidden to install agents on the remote server.**

The solution: a local MCP server in Python that exposes keyboard injection and screen reading tools, consumed by Claude Desktop as an interactive decision table.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Claude Desktop                         │
│              (System Prompt with guardrails)              │
│                                                          │
│  Atomic step-by-step flow:                               │
│  EXPLAIN → APPROVE → INJECT → CONFIRM ENTER → READ      │
└──────────────┬────────────────────┬──────────────────────┘
               │                    │
         write_to_anydesk      read_from_anydesk
               │                    │
┌──────────────▼────────────────────▼──────────────────────┐
│              MCP Server (Python, local, stdio)            │
│                                                          │
│  ┌─────────────────────┐  ┌────────────────────────────┐ │
│  │  keyboard_injector  │  │     screen_reader          │ │
│  │                     │  │                            │ │
│  │ • pywin32 (focus)   │  │ • mss (region capture)     │ │
│  │ • pynput (keystrk)  │  │ • opencv (preprocessing)   │ │
│  │ • configurable delay│  │ • tesseract (OCR)          │ │
│  │ • NEVER presses     │  │ • base64 decode (fallback) │ │
│  │   Enter             │  │ • SHA256 truncated (CRC)   │ │
│  │ • focus verify pre  │  │                            │ │
│  │   and post inject   │  │                            │ │
│  └─────────────────────┘  └─────────────┬──────────────┘ │
│                                         │                │
│  ┌──────────────────┐    ┌──────────────▼─────────────┐  │
│  │  window_manager   │    │  sanitizer.py (Layer 2)   │  │
│  │                   │    │  Redundant regex over      │  │
│  │ • enumerate by    │    │  post-OCR output before    │  │
│  │   class + title   │    │  returning to LLM          │  │
│  │ • operator selects│    └──────────────────────────┘  │
│  │ • pin by hwnd     │                                   │
│  │ • validate before │                                   │
│  │   each injection  │                                   │
│  └──────────────────┘                                    │
└──────────────────────────────────────────────────────────┘
               │
    AnyDesk window (local)
               │
    AnyDesk → AnyDesk (double hop)
               │
┌──────────────▼───────────────────────────────────────────┐
│           Remote Server (Hyper-V)                         │
│                                                          │
│  %TEMP%\s.ps1 ← bootstrap sanitizer (ephemeral)         │
│                                                          │
│  Every command runs as:                                   │
│  if(!(Test-Path "$env:TEMP\s.ps1")){                     │
│    "[SANITIZER-MISSING]"                                  │
│  } else {                                                │
│    iex (Get-Content "$env:TEMP\s.ps1" -Raw);             │
│    S('actual-command')                                    │
│  }                                                       │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Project Structure

```
mcp_anydesk_server/
├── server.py                 # MCP entry point + preflight + tool registration
├── startup_checks.py         # PreflightResult + dependency validation
├── keyboard_injector.py      # write_to_anydesk: focus + injection + delays
├── screen_reader.py          # read_from_anydesk: capture + OCR/Base64
├── sanitizer.py              # Regex layer 2 (Python, post-OCR)
├── window_manager.py         # Enumeration + selection + hwnd pinning
├── command_templates.py      # PS templates adapted to PS version
├── config.py                 # Parameterizable defaults
└── requirements.txt
```

---

## 4. Dependencies

### requirements.txt

```
mcp>=1.0
pywin32>=306
pynput>=1.7
mss>=9.0
pytesseract>=0.3
opencv-python>=4.8
Pillow>=10.0
```

### External binary dependency

- **Tesseract OCR** >= 5.0 (for LSTM engine). Not a pip package — must be installed separately on Windows. The server validates this at startup and provides installation instructions if missing.

---

## 5. Module Specifications

### 5.1. `startup_checks.py` — Preflight Validation

**Purpose:** Validate all dependencies at server startup. Determine which tools can be registered.

**Data structure:**

```python
@dataclass
class PreflightResult:
    tesseract_available: bool
    tesseract_version: str | None
    tesseract_instructions: str | None  # None if OK, install instructions if missing
    opencv_available: bool
    pywin32_available: bool
    pynput_available: bool
    mss_available: bool
    anydesk_windows: list[tuple]  # [(hwnd, title), ...]

    @property
    def write_capable(self) -> bool:
        return self.pywin32_available and self.pynput_available

    @property
    def read_capable(self) -> bool:
        return self.pywin32_available and self.mss_available

    @property
    def ocr_capable(self) -> bool:
        return self.read_capable and self.tesseract_available
```

**Validation flow:**

1. Check Python packages via direct import (pywin32, pynput, mss, cv2, pytesseract, PIL).
2. Check Tesseract binary:
   - `shutil.which('tesseract')` to find it.
   - Run `tesseract --version`, parse version.
   - If >= 5.0: OK. If < 5.0: WARNING (degraded OCR). If missing: ERROR with detailed install instructions.
3. Check for visible AnyDesk windows (non-blocking WARNING if none found).
4. Return `PreflightResult`.

**Tesseract installation instructions (when missing):**

```
Tesseract OCR not found in PATH.

Installation on Windows:
1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
   (use the 64-bit .exe installer)
2. During installation, check "Add to PATH"
3. Verify with: tesseract --version

Or via winget:
  winget install UB-Mannheim.TesseractOCR

Or via choco:
  choco install tesseract

Restart the MCP server after installing.
```

**Critical behavior:** If a critical dependency is missing, the corresponding tool is NOT registered with the MCP server. The LLM never sees tools it cannot use. If Tesseract is missing but everything else works, `read_from_anydesk` registers in `base64-only` mode (OCR disabled).

---

### 5.2. `server.py` — MCP Entry Point

**Purpose:** Initialize MCP server via `stdio` transport, run preflight, register available tools.

**Transport:** `stdio` (consumed by Claude Desktop via `claude_desktop_config.json`).

**Tools to register (conditionally, based on preflight):**

1. `write_to_anydesk` — if `preflight.write_capable`
2. `read_from_anydesk` — if `preflight.read_capable`
3. `get_system_status` — ALWAYS registered (read-only, returns preflight result so LLM can adapt)
4. `select_anydesk_window` — if `preflight.pywin32_available` (lets operator choose which AnyDesk session to target)
5. `bootstrap_sanitizer` — if `preflight.write_capable` (injects the sanitization PS1 into remote %TEMP%)

---

### 5.3. `window_manager.py` — AnyDesk Window Detection

**Purpose:** Enumerate, select, and pin AnyDesk windows. The sysadmin connects to many different projects, so the target window varies.

**Enumeration strategy (layered):**

1. Use `win32gui.EnumWindows` to find all visible windows.
2. Filter by class name containing `AnyDesk` OR window title containing `AnyDesk`.
3. Return list of `(hwnd, title, class_name)` tuples.

**Selection flow:**

1. When a task begins, enumerate AnyDesk windows.
2. If 0 found: return error "No AnyDesk sessions detected".
3. If 1 found: auto-select it (still confirm with operator).
4. If >1 found: present list to operator for selection (via `select_anydesk_window` tool).
5. Pin selected `hwnd` for the session.

**Pre-injection validation (CRITICAL):**

Before every keystroke injection, the injector must:

```
1. SetForegroundWindow(pinned_hwnd)
2. Sleep(focus_delay_ms)  — default 500ms
3. Verify GetForegroundWindow() == pinned_hwnd
4. If NO match → ABORT, return error "Focus lost, injection aborted"
5. If match → proceed with keystroke injection
6. Post-injection: verify focus again
```

This prevents keystrokes from going to the wrong window if the operator clicks elsewhere during the focus delay.

---

### 5.4. `keyboard_injector.py` — write_to_anydesk

**Purpose:** Receive text, focus AnyDesk window, inject keystrokes with artificial delay.

**Library:** `pynput.keyboard.Controller` for character-by-character injection. Chosen over `pyautogui` because `pyautogui.typewrite()` cannot handle non-ASCII characters (`$`, `{`, `}`, `|`, `@`, `ñ`, accents) which are essential for PowerShell.

**Tool signature:**

```python
write_to_anydesk(
    command: str,                # Text to inject
    keystroke_delay_ms: int = 80,  # Delay between each character (ms)
    focus_delay_ms: int = 500    # Delay after focusing window before typing
) -> dict
```

**Returns:**

```python
{
    "status": "injected",  # or "error"
    "char_count": int,
    "elapsed_ms": int,
    "note": "Enter NOT pressed — awaiting operator confirmation"
}
```

**HARD CONSTRAINTS:**

- **NEVER press Enter.** The tool must strip any `\n`, `\r`, `` `n ``, or newline-equivalent characters from the input before injecting. The final Enter is always pressed physically by the sysadmin.
- **Minimum delay:** 30ms between keystrokes (server-side enforced floor, even if caller requests lower).
- **Default delay:** 80ms — empirical sweet spot for double AnyDesk hop jitter.
- **Focus verification:** Pre and post injection (see window_manager section).
- **No multi-line commands:** If the input contains newlines, the tool must reject it with an error explaining that multi-line commands must be split into separate steps.

---

### 5.5. `screen_reader.py` — read_from_anydesk

**Purpose:** Capture AnyDesk window region, extract text via OCR or Base64 decoding.

**Tool signature:**

```python
read_from_anydesk(
    mode: Literal["ocr", "base64"] = "ocr",
    region: Optional[tuple[int, int, int, int]] = None  # x, y, w, h override
) -> dict
```

**Returns:**

```python
{
    "text": str,              # Sanitized extracted text
    "confidence": float,      # 0.0-1.0 (OCR) or 1.0/0.0 (Base64 valid/invalid)
    "mode_used": str,
    "sanitized_fields": {     # Count of redacted items by type
        "ip_redacted": int,
        "email_redacted": int,
        "account_redacted": int,
        "sid_redacted": int,
        "mac_redacted": int,
        "unc_redacted": int,
        "credential_redacted": int
    },
    "warnings": list[str]     # e.g., ["Low OCR confidence on lines 3-5"]
}
```

**Mode: OCR (primary, for short/readable output < ~15 lines):**

Capture pipeline:
1. **`mss`** to capture the pinned AnyDesk window region (faster than `Pillow.ImageGrab`).
2. **Preprocessing with `cv2` (OpenCV):**
   - Convert to grayscale.
   - **Adaptive thresholding:** `cv2.adaptiveThreshold` with `ADAPTIVE_THRESH_GAUSSIAN_C` — critical because video compression creates gray gradients where there should be pure black/white.
   - **Denoising:** `cv2.fastNlMeansDenoising` — removes H.264/H.265 mosquito noise.
   - **2x upscaling:** `cv2.resize` with `INTER_CUBIC` interpolation — dramatically improves Tesseract accuracy on compressed console text.
3. **`pytesseract`** with config `--psm 6 --oem 1` (uniform text block + LSTM engine).
4. Pass extracted text through `sanitizer.py` (Layer 2).

**Mode: Base64 (fallback, for critical output where a single character matters):**

Used for hashes, GUIDs, long paths, dense tables. The remote PowerShell command wraps output in Base64 + checksum.

Decode pipeline:
1. Capture via `mss` (same as OCR).
2. OCR the Base64 string (limited charset: A-Z, a-z, 0-9, +, /, =).
3. Apply charset correction heuristics (e.g., `O` → `0`, `l` → `1` in Base64 context).
4. Decode Base64 to UTF-8 text.
5. Validate SHA256-truncated checksum (8 hex chars).
6. If checksum fails: retry capture up to 2 times.
7. If still fails: return error asking operator to read manually.
8. If succeeds: pass decoded text through `sanitizer.py` (Layer 2).

**Key property of Base64 mode:** Corrupted OCR causes decode failure or checksum mismatch — you never get silently corrupted data.

**Important:** If Tesseract is not installed, the tool registers in `base64-only` mode. In this mode, calling with `mode="ocr"` returns an error with Tesseract installation instructions.

---

### 5.6. `sanitizer.py` — PII Redaction (Python Layer 2)

**Purpose:** Redundant regex-based PII sanitization applied to all text AFTER OCR/Base64 decoding, BEFORE returning to the LLM. This is the second line of defense — the first is the PowerShell sanitizer on the remote server.

**Patterns to redact:**

| Pattern | Regex | Replacement |
|---|---|---|
| RFC1918 IPv4 (10.x.x.x) | `(?<!\d)(10\.\d{1,3}\.\d{1,3}\.\d{1,3})(?!\d)` | `[IP-REDACTED]` |
| RFC1918 IPv4 (172.16-31.x.x) | `(?<!\d)(172\.(1[6-9]\|2\d\|3[01])\.\d{1,3}\.\d{1,3})(?!\d)` | `[IP-REDACTED]` |
| RFC1918 IPv4 (192.168.x.x) | `(?<!\d)(192\.168\.\d{1,3}\.\d{1,3})(?!\d)` | `[IP-REDACTED]` |
| IPv6 link-local | `(?i)fe80:[0-9a-f:]+(%\w+)?` | `[IPv6-REDACTED]` |
| Email addresses | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` | `[EMAIL-REDACTED]` |
| SAM account (DOMAIN\user) | `(?i)[A-Z0-9_-]+\\[A-Z0-9_.-]+` | `[ACCOUNT-REDACTED]` |
| UPN (user@domain.local) | `(?i)[a-z0-9._-]+@[a-z0-9.-]+\.local` | `[UPN-REDACTED]` |
| Windows SID | `S-1-\d+-\d+(-\d+){1,}` | `[SID-REDACTED]` |
| MAC address | `([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}` | `[MAC-REDACTED]` |
| UNC path server | `\\\\[A-Za-z0-9_.-]+\\` | `\\[SERVER-REDACTED]\` |
| Credential keywords | Lines containing `Password=`, `SecureString`, `ConvertTo-SecureString`, `-Credential` | `[CREDENTIAL-LINE-REDACTED]` |

**Design decisions:**
- Public IPs are NOT redacted (sysadmin may need them for diagnostics).
- The DOMAIN\user pattern is intentionally aggressive — may false-positive on file paths with backslashes. Over-redaction is preferred over leaking Active Directory accounts.
- Credential detection redacts the ENTIRE LINE, not just the keyword.
- The function returns both the sanitized text and a count of redactions by type.

---

### 5.7. `command_templates.py` — PowerShell Templates

**Purpose:** Generate PS commands adapted to the detected PowerShell version. Compatible with both PS 5.1 (`powershell.exe`) and PS 7+ (`pwsh.exe`).

**Bootstrap template (Paso 0, injected once per session):**

The bootstrap creates an ephemeral `.ps1` file in `%TEMP%` containing the sanitization function. Uses `iex` (Invoke-Expression) instead of dot-sourcing to bypass Execution Policy restrictions.

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Content -Path "$env:TEMP\s.ps1" -Encoding UTF8 -Value @'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function S($c){
  try {
    $r = iex $c | Out-String
    # --- PII Sanitization Regexes (Layer 1) ---
    $r = $r -replace '(?<!\d)(10\.\d{1,3}\.\d{1,3}\.\d{1,3})(?!\d)','[IP-X]'
    $r = $r -replace '(?<!\d)(172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)','[IP-X]'
    $r = $r -replace '(?<!\d)(192\.168\.\d{1,3}\.\d{1,3})(?!\d)','[IP-X]'
    $r = $r -replace '(?i)fe80:[0-9a-f:]+(%\w+)?','[IPv6-X]'
    $r = $r -replace '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}','[EMAIL-X]'
    $r = $r -replace '(?i)[A-Z0-9_-]+\\[A-Z0-9_.-]+','[ACCT-X]'
    $r = $r -replace '(?i)[a-z0-9._-]+@[a-z0-9.-]+\.local','[UPN-X]'
    $r = $r -replace 'S-1-\d+-\d+(-\d+){1,}','[SID-X]'
    $r = $r -replace '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}','[MAC-X]'
    $r = $r -replace '\\\\[A-Za-z0-9_.-]+\\','\\[SRV-X]\'
    $r.TrimEnd()
  } catch {
    "[ERROR] $($_.Exception.Message)"
  }
}
'@
```

**Command wrapper template (every subsequent command):**

```powershell
if(!(Test-Path "$env:TEMP\s.ps1")){"[SANITIZER-MISSING]"}else{iex (Get-Content "$env:TEMP\s.ps1" -Raw); S('ACTUAL_COMMAND_HERE')}
```

The `[SANITIZER-MISSING]` sentinel: if the MCP's OCR reads this string in the output, it REFUSES to process and instructs the operator to re-run the bootstrap. Commands never execute without active sanitization.

**Base64 wrapper template (for critical output):**

```powershell
if(!(Test-Path "$env:TEMP\s.ps1")){"[SANITIZER-MISSING]"}else{iex (Get-Content "$env:TEMP\s.ps1" -Raw); $out = S('ACTUAL_COMMAND_HERE'); $b = [Text.Encoding]::UTF8.GetBytes($out); $e = [Convert]::ToBase64String($b); $crc = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b)).Replace('-','').Substring(0,8); "$e`n[CRC:$crc]"}
```

**PS Version detection (first command of any session):**

```powershell
$PSVersionTable.PSVersion.Major
```

The MCP reads this output and stores the version. Both templates work identically on PS 5.1 and 7+. The only adaptation is forcing `[Console]::OutputEncoding = UTF8` in the bootstrap, which handles PS 5.1's default UTF-16LE output.

**Why `iex` instead of dot-sourcing:** Dot-sourcing (`. "$env:TEMP\s.ps1"`) is subject to Execution Policy. `iex` evaluates a string in the current scope and is not subject to Execution Policy. The security implication is acceptable because we are executing a file we created seconds earlier in `%TEMP%`.

---

### 5.8. `config.py` — Defaults

```python
DEFAULT_KEYSTROKE_DELAY_MS = 80
MIN_KEYSTROKE_DELAY_MS = 30
DEFAULT_FOCUS_DELAY_MS = 500
DEFAULT_OCR_PSM = 6          # Uniform text block
DEFAULT_OCR_OEM = 1          # LSTM engine
OCR_UPSCALE_FACTOR = 2
BASE64_MAX_RETRIES = 2
SANITIZER_SENTINEL = "[SANITIZER-MISSING]"
BOOTSTRAP_FILENAME = "s.ps1"
```

---

## 6. System Prompt for Claude Desktop

This prompt must be set in Claude Desktop's project instructions or `claude_desktop_config.json`. It enforces the operational guardrails.

```
You are an operations assistant for a Sysadmin working in a double-hop remote
environment (AnyDesk → AnyDesk → Hyper-V). You operate under strict security
and latency constraints.

## ABSOLUTE RULES — NEVER VIOLATE THESE

1. ATOMICITY: Every action you propose must be ONE single command of 1-2 lines
   maximum. NEVER chain commands with `;`, `&&`, or complex pipelines of more
   than 2 stages in a single step. If you need a long pipeline, split it into
   steps with intermediate variables.

2. MANDATORY SEQUENCE for each step:
   a) EXPLAIN: Describe in plain language what the command does, what effect it
      will have on the system, and what risk it carries if something goes wrong.
      The operator needs to understand the command BEFORE executing it
      (Non-Repudiation principle).
   b) WAIT FOR APPROVAL: Explicitly ask "Do you approve injecting this command?"
      Do NOT use write_to_anydesk until you receive an explicit "yes".
   c) INJECT: Use write_to_anydesk. The command will be typed WITHOUT pressing
      Enter.
   d) WAIT FOR EXECUTION CONFIRMATION: After injecting, say exactly:
      "Command injected. Visually verify the text is correct in the remote
      console and press Enter when ready. Tell me when the command has finished
      executing."
   e) READ: Only after the operator confirms the command finished, use
      read_from_anydesk to read the sanitized output.
   f) ANALYZE AND PROPOSE: With the output read, analyze the result and propose
      the next step. Return to point (a).

3. NEVER do this:
   - Press Enter automatically (write_to_anydesk does not allow it, but NEVER
     attempt to include \n, `n, or newline codes in the text)
   - Propose more than one step at a time without having read the output of the
     previous one
   - Assume a command was successful without reading the output
   - Inject a second command while the first is still executing
   - Propose destructive commands (Remove-Item -Recurse, Format-*, 
     Clear-Content, Stop-VM -Force) without explicit WARNING in uppercase

4. SANITIZATION: All output you read from read_from_anydesk is already sanitized
   (internal IPs, emails, accounts, and SIDs are redacted). DO NOT ask the
   operator to provide you with that data. If you need an IP for diagnostics,
   ask the operator to enter it manually. Work with redacted data.

5. ERRORS AND ROLLBACK: If a command fails or output is unexpected:
   - STOP immediately
   - Explain what you expected vs. what you got
   - Propose a diagnostic command (not a fix) as the next step
   - If you propose a fix, explicitly include the rollback plan

6. COMMUNICATION FORMAT: Be concise. No greetings or filler. Structure each
   step like this:

   **Step N — [Short description]**
   Purpose: [what this step achieves]
   Risk: [low/medium/high] — [1-line explanation]
   Command: `[the exact command]`
   Do you approve?

7. SESSION START: At the beginning of every session:
   - Use get_system_status to check dependencies
   - Use select_anydesk_window to identify the target session
   - Use bootstrap_sanitizer to inject the sanitization script
   - Detect the PowerShell version with: $PSVersionTable.PSVersion.Major
   Only after these 4 setup steps are complete, begin the operator's task.

8. SANITIZER SENTINEL: If read_from_anydesk ever returns text containing
   "[SANITIZER-MISSING]", STOP ALL OPERATIONS. Inform the operator that the
   sanitizer is not active and re-run bootstrap_sanitizer before continuing.
   NEVER execute commands when the sanitizer is missing.
```

---

## 7. Security Guardrails Summary

| Risk | Mitigation | Layer |
|---|---|---|
| Keystroke goes to wrong window | Pre/post focus verification via hwnd | `keyboard_injector.py` |
| Double AnyDesk latency eats characters | Configurable delay (80ms default, 30ms floor) | `keyboard_injector.py` |
| Accidental destructive command | Enter NEVER pressed by tool; operator presses physically | `keyboard_injector.py` + System Prompt |
| PII leaks to LLM API | Dual-layer regex sanitization (PowerShell Layer 1 + Python Layer 2) | `command_templates.py` + `sanitizer.py` |
| Sanitizer disappears (shell restart) | `[SANITIZER-MISSING]` sentinel check on every command | `command_templates.py` + System Prompt |
| OCR reads corrupted data silently | Base64 mode with SHA256 checksum validation | `screen_reader.py` |
| Missing dependencies cause runtime crash | Preflight validation; tools not registered if deps missing | `startup_checks.py` |
| LLM skips safety steps | Atomic step-by-step flow enforced in System Prompt | System Prompt |
| Multi-line injection causes chaos | Tool rejects any input containing newlines | `keyboard_injector.py` |
| Execution Policy blocks sanitizer | `iex` instead of dot-sourcing to bypass | `command_templates.py` |

---

## 8. Implementation Order

Build and test in this sequence:

1. **`startup_checks.py`** — Preflight validation (can be tested immediately)
2. **`config.py`** — Constants and defaults
3. **`server.py`** — MCP skeleton with stdio transport, registers `get_system_status` only
4. **`window_manager.py`** — AnyDesk window enumeration and selection
5. **`sanitizer.py`** — Python regex layer (unit-testable in isolation)
6. **`command_templates.py`** — PS templates (string generation, testable without remote)
7. **`keyboard_injector.py`** — write_to_anydesk (requires AnyDesk running for integration test)
8. **`screen_reader.py`** — read_from_anydesk (requires AnyDesk + Tesseract for integration test)
9. **Wire all tools into `server.py`** — Full integration
10. **System Prompt** — Configure in Claude Desktop and test end-to-end

---

## 9. Testing Notes

- **Unit-testable without AnyDesk:** `sanitizer.py`, `command_templates.py`, `startup_checks.py`, `config.py`
- **Requires AnyDesk window:** `window_manager.py`, `keyboard_injector.py`, `screen_reader.py`
- **Integration test:** Open a local Notepad or PowerShell window titled "AnyDesk" for safe testing without a real remote connection.
- **OCR test:** Screenshot a console with known text, compress it with JPEG quality 30, then test the preprocessing + OCR pipeline against the degraded image.

---

## 10. Key Design Decisions (Rationale)

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Keyboard injection library | `pynput` | `pyautogui` | `pyautogui.typewrite()` can't handle non-ASCII chars needed for PowerShell |
| Screen capture library | `mss` | `Pillow.ImageGrab` | `mss` is faster and returns numpy-compatible arrays |
| Reading mode | OCR + Base64 | QR codes | QR rendered in console with Unicode block chars has very low decode rate over double-compressed video |
| Sanitizer persistence | Ephemeral `.ps1` in %TEMP% | Persistent function in shell memory | Shell restart silently loses function; file + sentinel provides fail-safe detection |
| Sanitizer invocation | `iex` (Invoke-Expression) | Dot-sourcing (`. file.ps1`) | Dot-sourcing subject to Execution Policy; `iex` is not |
| Checksum for Base64 | SHA256 truncated to 8 hex chars | CRC32 | .NET has SHA256 built-in; CRC32 requires loading System.IO.Compression or manual table |
| PS compatibility | Detect version + force UTF-8 | Assume PS 7+ | Many servers still run PS 5.1 with UTF-16LE default output |
| Window detection | Class + title enumeration with operator selection | Fixed window title match | Sysadmin connects to many different servers; target varies |
