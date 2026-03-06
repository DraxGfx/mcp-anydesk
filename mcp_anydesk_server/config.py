DEFAULT_KEYSTROKE_DELAY_MS = 80
MIN_KEYSTROKE_DELAY_MS = 30
DEFAULT_FOCUS_DELAY_MS = 500
DEFAULT_OCR_PSM = 4          # Single column of variable-size text (better for console)
DEFAULT_OCR_OEM = 1          # LSTM engine
OCR_UPSCALE_FACTOR = 3       # 3x upscale for video-compressed text (was 2)
OCR_DENOISE_H = 13           # fastNlMeansDenoising strength (was 18 — reduced to preserve edges)
OCR_SHARPEN_AFTER_DENOISE = True  # Sharpen kernel after denoise to recover edges
BASE64_MAX_RETRIES = 2
SANITIZER_SENTINEL = "[SANITIZER-MISSING]"
BOOTSTRAP_FILENAME = "s.ps1"

# --- v2 additions ---

# Screenshot
SCREENSHOT_DIR = "~/.mcp_anydesk/screenshots"  # Local only, never sent to API
SCREENSHOT_MAX_KEPT = 20                        # Auto-cleanup oldest
SCREENSHOT_FORMAT = "jpeg"                      # v3: jpeg default (was png)
SCREENSHOT_JPEG_QUALITY = 35                    # 1-100
SCREENSHOT_DEFAULT_SCALE = 25                   # v3.1: 25% default (was 50%)
SCREENSHOT_MAX_WIDTH = 640                      # Hard cap after scale resize
SCREENSHOT_MAX_BASE64_KB = 15                   # Hard limit for base64 output size

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

# --- v3 Phase 1 additions ---

# Session history
MAX_SESSION_HISTORY_RESPONSE = 5    # Max steps returned in get_session_history

# Session recording (N3)
SESSION_RECORDING_DIR = "~/.mcp_anydesk/recordings"

# Keystroke cancel
CANCEL_FOCUS_DELAY_MS = 300         # Focus delay before sending Ctrl+C


# Auto-focus click: click window center after SetForegroundWindow so
# AnyDesk receives input without requiring the user to place the cursor manually.
FOCUS_CLICK_ENABLED = True
FOCUS_CLICK_DELAY_MS = 50   # Extra delay after click before returning


# --- v3 Phase 2 additions ---

# Quick diagnostic mode (N1) — commands that skip approval in diagnostic mode
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

# Session recording (N3)
SESSION_RECORDING_ENABLED = False       # Operator enables per session

# Health heartbeat (N7)
HEARTBEAT_INTERVAL_MINUTES = 5
HEARTBEAT_ENABLED = False               # Off by default

# Dead session detection (N6)
ANYDESK_DISCONNECT_PATTERNS = [
    "connecting",
    "waiting for",
    "session interrupted",
    "connection closed",
    "not connected",
]
