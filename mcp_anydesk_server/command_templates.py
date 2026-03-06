"""PowerShell command templates adapted to PS version.

Generates bootstrap steps, command wrapper, and Base64 wrapper templates
compatible with both PS 5.1 (powershell.exe) and PS 7+ (pwsh.exe).

bootstrap_steps() returns a list of 9 short commands (each < 120 chars).
wrap_command() and wrap_command_base64() wrap commands with sanitizer check.
"""

from __future__ import annotations

from .config import BOOTSTRAP_FILENAME, SANITIZER_SENTINEL


# ---------------------------------------------------------------------------
# Compressed command wrapper (v3 — uses PS aliases for brevity)
# ---------------------------------------------------------------------------
# gc  = Get-Content (built-in PS alias, works on 5.1 and 7+)
# |iex eliminates the parens of iex(...)
# Overhead: ~80 chars  (~40 char savings vs v2 = 1 fewer chunk)
_COMMAND_WRAPPER = (
    'if(!(Test-Path "$env:TEMP\\{filename}")){{"{sentinel}"}}'
    'else{{gc "$env:TEMP\\{filename}" -Raw|iex;S(\'{command}\')}}'
)

# ---------------------------------------------------------------------------
# Compressed Base64 wrapper (v3) — requires bootstrap (uses S() sanitizer)
# ---------------------------------------------------------------------------
_BASE64_WRAPPER = (
    'if(!(Test-Path "$env:TEMP\\{filename}")){{"{sentinel}"}}'
    'else{{gc "$env:TEMP\\{filename}" -Raw|iex;'
    "$out=S('{command}');"
    "$b=[Text.Encoding]::UTF8.GetBytes($out);"
    "$e=[Convert]::ToBase64String($b);"
    "$crc=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create()"
    ".ComputeHash($b)).Replace('-','').Substring(0,8);"
    '"$e`n[CRC:$crc]"}}'
)

# ---------------------------------------------------------------------------
# Base64 lite wrapper (v3.2) — works WITHOUT bootstrap, no PII sanitization
# ---------------------------------------------------------------------------
_BASE64_WRAPPER_LITE = (
    "$out={command}|Out-String;"
    "$b=[Text.Encoding]::UTF8.GetBytes($out);"
    "$e=[Convert]::ToBase64String($b);"
    "$crc=[BitConverter]::ToString("
    "[Security.Cryptography.SHA256]::Create()"
    ".ComputeHash($b)).Replace('-','').Substring(0,8);"
    '"$e`n[CRC:$crc]"'
)

# ---------------------------------------------------------------------------
# PS version detection (first command of any session)
# ---------------------------------------------------------------------------
PS_VERSION_COMMAND = "$PSVersionTable.PSVersion.Major"


def bootstrap_steps() -> list[dict]:
    """Return ordered list of short bootstrap commands (each < 120 chars).

    Each command is a separate inject+Enter cycle. The LLM injects each
    step individually via write_to_anydesk(wrap=False) and the operator
    presses Enter after each one.

    After the last step, call read_from_anydesk to confirm [BOOTSTRAP-OK].

    Replaces the single EncodedCommand approach (v3.0) which generated a
    ~2700-char line causing ~80s injection timeouts and LLM retry loops.

    Returns:
        List of dicts: step (int), description (str), command (str).
    """
    tmp = '"$env:TEMP\\' + BOOTSTRAP_FILENAME + '"'

    return [
        {
            "step": 1,
            "description": "Create sanitizer file with function header",
            "command": f"Set-Content {tmp} 'function S($c){{'",
        },
        {
            "step": 2,
            "description": "Add try block with command execution",
            "command": f"Add-Content {tmp} 'try{{$r=iex $c|Out-String'",
        },
        {
            "step": 3,
            "description": "Add RFC1918 IP sanitization (10.x and 192.168.x)",
            "command": (
                f"Add-Content {tmp} "
                r"'$r=$r -replace ''10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+'',''[IP-X]'''"
            ),
        },
        {
            "step": 4,
            "description": "Add RFC1918 IP sanitization (172.16-31.x)",
            "command": (
                f"Add-Content {tmp} "
                r"'$r=$r -replace ''172\.(1[6-9]|2\d|3[01])\.\d+\.\d+'',''[IP-X]'''"
            ),
        },
        {
            "step": 5,
            "description": "Add email sanitization",
            "command": (
                f"Add-Content {tmp} "
                r"'$r=$r -replace ''[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'',''[EMAIL-X]'''"
            ),
        },
        {
            "step": 6,
            "description": "Add DOMAIN\\user account sanitization",
            "command": (
                f"Add-Content {tmp} "
                r"'$r=$r -replace ''(?i)[A-Z0-9_-]+\\[A-Z0-9_.-]+'',''[ACCT-X]'''"
            ),
        },
        {
            "step": 7,
            "description": "Add SID sanitization",
            "command": (
                f"Add-Content {tmp} "
                r"'$r=$r -replace ''S-1-\d+-\d+(-\d+)+'',''[SID-X]'''"
            ),
        },
        {
            "step": 8,
            "description": "Close function with error handler",
            "command": (
                f"Add-Content {tmp} "
                """'$r.TrimEnd()}catch{"[ERROR] "+$_.Exception.Message}}'"""
            ),
        },
        {
            "step": 9,
            "description": "Verify sanitizer file was created",
            "command": (
                f'if(Test-Path {tmp}){{"[BOOTSTRAP-OK]"}}else{{"[BOOTSTRAP-FAILED]"}}'
            ),
        },
    ]


def wrap_command(command: str) -> str:
    """Wrap a command with the compressed sanitizer-check wrapper.

    Args:
        command: The raw PS command to execute.

    Returns:
        ~80-char-overhead one-liner using gc alias and |iex.
    """
    escaped = command.replace("'", "''")
    return _COMMAND_WRAPPER.format(
        filename=BOOTSTRAP_FILENAME,
        sentinel=SANITIZER_SENTINEL,
        command=escaped,
    )


def wrap_command_base64(command: str, sanitizer_available: bool = True) -> str:
    """Wrap a command so its output is Base64-encoded with a SHA256 CRC.

    When sanitizer_available=True (bootstrap completed), uses the full
    wrapper that routes output through S() for PII redaction.

    When sanitizer_available=False, uses the lite wrapper that encodes
    output directly without PII sanitization. This allows base64 mode
    to work before bootstrap.

    Args:
        command: The raw PS command to execute.
        sanitizer_available: Whether the bootstrap sanitizer is active.

    Returns:
        One-liner that outputs Base64 + [CRC:xxxxxxxx].
    """
    escaped = command.replace("'", "''")
    if sanitizer_available:
        return _BASE64_WRAPPER.format(
            filename=BOOTSTRAP_FILENAME,
            sentinel=SANITIZER_SENTINEL,
            command=escaped,
        )
    return _BASE64_WRAPPER_LITE.format(command=escaped)
