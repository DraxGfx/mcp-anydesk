# Changelog

## v3.2 — Usability Fixes (2026-03-02)

Focused on resolving the 4 critical usability issues identified during real-world sysadmin testing.

### 1. Keyboard Injection — VK_PACKET (Layout-Agnostic)

**Problem:** `pynput.keyboard.Controller.type()` sends scancodes mapped to the OS's active keyboard layout. When local layout is EN-US but remote is ES-LA (or vice versa), characters `\ | { } / = :` get corrupted during AnyDesk injection.

**Fix:** Replaced pynput character injection with Win32 `SendInput` + `KEYEVENTF_UNICODE` (VK_PACKET). This sends Unicode codepoints directly — the OS generates `WM_CHAR` messages without consulting the keyboard layout map.

**Files changed:**
- `keyboard_injector.py` — New `_send_unicode_char()` using ctypes `SendInput`. `_inject_chunk()` now calls this instead of `pynput.type()`. pynput retained only for `send_cancel()` (Ctrl+C modifier combo).

**Impact:** All printable characters inject correctly regardless of keyboard layout on either side of the AnyDesk connection.

---

### 2. OCR Pipeline — Improved Confidence

**Problem:** OCR confidence stuck at 0.42–0.48 even with 4x zoom retry. Terminal text (light on dark) with H.264 compression artifacts produced borderline reads that never triggered the adaptive retry (threshold was 0.4).

**Fix:** Five changes to the preprocessing pipeline:

| Parameter | Before | After | Why |
|---|---|---|---|
| Color inversion | None | `cv2.bitwise_not` | Tesseract optimized for dark-on-light |
| `blockSize` | 11 | 17 | Better local threshold for compression gradients |
| `C` (threshold constant) | 2 | 3 | Tighter binarization |
| Denoising `h` | 10 | 18 | Stronger mosquito noise removal |
| `OCR_UPSCALE_FACTOR` | 2 | 3 | More resolution for Tesseract LSTM |
| `DEFAULT_OCR_PSM` | 6 (uniform block) | 4 (single column) | Better for variable-width console lines |
| Retry threshold | 0.4 | 0.55 | Now catches 0.42–0.48 reads for 4x zoom retry |

**Files changed:**
- `config.py` — `OCR_UPSCALE_FACTOR = 3`, `DEFAULT_OCR_PSM = 4`
- `screen_reader.py` — `_preprocess_for_ocr()` with inversion + new params, retry threshold raised

**Impact:** Reads that previously sat at 0.42–0.48 now trigger 4x zoom retry and benefit from better preprocessing. Expected confidence improvement to 0.6+ range.

---

### 3. Base64 Mode Without Bootstrap

**Problem:** `read_from_anydesk(mode="base64")` required bootstrap to be completed first because the Base64 wrapper called `S()` (the sanitizer function from `s.ps1`). Without bootstrap, PowerShell printed `[SANITIZER-MISSING]` and base64 decoding failed.

**Fix:** Added `_BASE64_WRAPPER_LITE` — a standalone wrapper that encodes command output to Base64 + SHA256 CRC without routing through `S()`. No PII sanitization, but base64 encoding works.

**Files changed:**
- `command_templates.py` — New `_BASE64_WRAPPER_LITE` template. `wrap_command_base64()` now accepts `sanitizer_available` parameter to select wrapper.
- `keyboard_injector.py` — `write_to_anydesk()` accepts `sanitizer_available` parameter, passes to `wrap_command_base64()`.
- `server.py` — Passes `sanitizer_available=session.sanitizer_bootstrapped` to the injector. When `base64_output=True`, wrap stays enabled even without bootstrap (uses lite wrapper).

**Impact:** `base64_output=True` works immediately after `initialize_session()`, before bootstrap. PII sanitization in base64 mode still requires bootstrap.

---

### 4. Blocklist Removed

**Problem:** User preference — server-side blocklist adds complexity and false sense of security. Destructive command awareness should be handled by the system prompt, not hard-coded regex patterns.

**Fix:** Removed the entire blocklist system.

**Files changed:**
- `safety.py` — **DELETED**
- `config.py` — `BLOCKED_DESTRUCTIVE_PATTERNS` list removed
- `keyboard_injector.py` — Removed `from safety import check_command_safety` and the safety check block in `write_to_anydesk()`
- `server.py` — `force_dangerous` parameter kept for API compatibility but is unused

**Impact:** All commands pass through to injection. The system prompt is the sole guardrail for destructive command awareness. The `force_dangerous` parameter is a no-op.

---

### Summary of File Changes

| File | Action | Lines changed |
|---|---|---|
| `keyboard_injector.py` | Rewritten injection core | VK_PACKET + removed safety import |
| `screen_reader.py` | Updated preprocessing | Inversion, thresholds, retry |
| `command_templates.py` | Added lite wrapper | `_BASE64_WRAPPER_LITE` + param |
| `config.py` | Updated constants | OCR params, removed blocklist |
| `server.py` | Updated wiring | sanitizer_available passthrough |
| `safety.py` | **Deleted** | — |
