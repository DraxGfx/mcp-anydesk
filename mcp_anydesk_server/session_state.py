"""Session state management and audit logging.

Maintains in-memory command history and writes an append-only
audit log to disk for the sysadmin's records.

v3 changes:
- get_history() defaults to MAX_SESSION_HISTORY_RESPONSE (5) and
  truncates output_text to 80 chars for token efficiency.
- export_session_report() generates a markdown report file (N3).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    AUDIT_LOG_DIR,
    MAX_SESSION_HISTORY_RESPONSE,
    SESSION_COMMAND_HISTORY_MAX,
    SESSION_RECORDING_DIR,
)


@dataclass
class CommandRecord:
    step_number: int
    timestamp: str               # ISO 8601
    command_original: str        # What the LLM intended
    command_injected: str        # What was actually typed (wrapped)
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
    def __init__(self) -> None:
        self.pinned_hwnd: int | None = None
        self.pinned_title: str | None = None
        self.ps_version: int | None = None
        self.sanitizer_bootstrapped: bool = False
        self.commands: list[CommandRecord] = []
        self.step_counter: int = 0
        # Bootstrap tracking (v3.1)
        self.last_bootstrap_attempt: float | None = None   # time.monotonic() timestamp
        self.bootstrap_steps_completed: int = 0            # steps confirmed by operator
        self._log_file = self._init_log_file()

    def next_step(self) -> int:
        self.step_counter += 1
        return self.step_counter

    def add_command(self, record: CommandRecord) -> None:
        self.commands.append(record)
        self._write_log(record)
        if len(self.commands) > SESSION_COMMAND_HISTORY_MAX:
            self.commands = self.commands[-SESSION_COMMAND_HISTORY_MAX:]

    def get_history(self, last_n: int = MAX_SESSION_HISTORY_RESPONSE) -> list[dict]:
        records = [asdict(c) for c in self.commands[-last_n:]]
        # Truncate output_text for token efficiency; full text is on disk
        for r in records:
            if r.get("output_text") and len(r["output_text"]) > 80:
                r["output_text"] = r["output_text"][:80] + "...[truncated]"
        return records

    def export_session_report(self) -> str:
        """Generate a markdown report of the entire session and save to disk.

        Returns:
            Absolute path to the exported markdown file.
        """
        report_dir = Path(os.path.expanduser(SESSION_RECORDING_DIR))
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_path = report_dir / f"session_report_{ts}.md"

        lines: list[str] = [
            f"# Session Report — {ts}",
            "",
            f"**Window:** {self.pinned_title} (hwnd: {self.pinned_hwnd})",
            f"**PS Version:** {self.ps_version}",
            f"**Total Steps:** {self.step_counter}",
            "",
            "---",
            "",
        ]

        for cmd in self.commands:
            if cmd.success is True:
                status = "OK"
            elif cmd.success is False:
                status = "FAILED"
            else:
                status = "PENDING"
            lines.append(f"### Step {cmd.step_number} [{status}]")
            lines.append(f"**Time:** {cmd.timestamp}")
            lines.append(f"**Command:** `{cmd.command_original}`")
            lines.append(f"**Wrapped:** {'Yes' if cmd.was_wrapped else 'No'}")
            lines.append(f"**Chars:** {cmd.char_count}")
            if cmd.output_text:
                lines.append("**Output:**")
                lines.append("```")
                lines.append(cmd.output_text[:500])
                lines.append("```")
            if cmd.output_confidence is not None:
                lines.append(f"**OCR Confidence:** {cmd.output_confidence:.2f}")
            if cmd.notes:
                lines.append(f"**Notes:** {cmd.notes}")
            lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

    def update_last_command(self, **kwargs: object) -> None:
        if not self.commands:
            return
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
            short = record.output_text[:100].replace("\n", " ")
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
