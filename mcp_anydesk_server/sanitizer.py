"""PII redaction — Python Layer 2 (post-OCR, pre-LLM).

Redundant regex-based sanitization applied to all text after OCR/Base64
decoding, before returning to the LLM. This is the second line of defense —
the first is the PowerShell sanitizer on the remote server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Patterns — order matters: more specific patterns first to avoid partial
# matches being consumed by broader ones.
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Credential keywords — redact entire line
    (
        "credential_redacted",
        re.compile(
            r"^.*(?:Password=|SecureString|ConvertTo-SecureString|-Credential).*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        "[CREDENTIAL-LINE-REDACTED]",
    ),
    # Windows SID
    (
        "sid_redacted",
        re.compile(r"S-1-\d+-\d+(-\d+){1,}"),
        "[SID-REDACTED]",
    ),
    # UPN (user@domain.local) — before generic email to avoid overlap
    (
        "account_redacted",
        re.compile(r"(?i)[a-z0-9._-]+@[a-z0-9.-]+\.local"),
        "[UPN-REDACTED]",
    ),
    # Email addresses
    (
        "email_redacted",
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "[EMAIL-REDACTED]",
    ),
    # MAC address — before IP to avoid partial hex matches
    (
        "mac_redacted",
        re.compile(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}"),
        "[MAC-REDACTED]",
    ),
    # IPv6 link-local
    (
        "ip_redacted",
        re.compile(r"(?i)fe80:[0-9a-f:]+(%\w+)?"),
        "[IPv6-REDACTED]",
    ),
    # RFC1918 IPv4 — 10.x.x.x
    (
        "ip_redacted",
        re.compile(r"(?<!\d)(10\.\d{1,3}\.\d{1,3}\.\d{1,3})(?!\d)"),
        "[IP-REDACTED]",
    ),
    # RFC1918 IPv4 — 172.16-31.x.x
    (
        "ip_redacted",
        re.compile(
            r"(?<!\d)(172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)"
        ),
        "[IP-REDACTED]",
    ),
    # RFC1918 IPv4 — 192.168.x.x
    (
        "ip_redacted",
        re.compile(r"(?<!\d)(192\.168\.\d{1,3}\.\d{1,3})(?!\d)"),
        "[IP-REDACTED]",
    ),
    # UNC path server
    (
        "unc_redacted",
        re.compile(r"\\\\[A-Za-z0-9_.-]+\\"),
        r"\\[SERVER-REDACTED]\\",
    ),
    # SAM account (DOMAIN\user)
    (
        "account_redacted",
        re.compile(r"(?i)[A-Z0-9_-]+\\[A-Z0-9_.-]+"),
        "[ACCOUNT-REDACTED]",
    ),
]


@dataclass
class SanitizeResult:
    """Result of sanitization: cleaned text + redaction counts."""

    text: str
    counts: dict[str, int] = field(default_factory=dict)


def sanitize(text: str) -> SanitizeResult:
    """Apply all PII-redaction patterns to *text*.

    Returns:
        SanitizeResult with sanitized text and per-type redaction counts.
    """
    counts: dict[str, int] = {
        "ip_redacted": 0,
        "email_redacted": 0,
        "account_redacted": 0,
        "sid_redacted": 0,
        "mac_redacted": 0,
        "unc_redacted": 0,
        "credential_redacted": 0,
    }

    for counter_key, pattern, replacement in _PATTERNS:
        matches = pattern.findall(text)
        if matches:
            counts[counter_key] += len(matches)
            text = pattern.sub(replacement, text)

    return SanitizeResult(text=text, counts=counts)
