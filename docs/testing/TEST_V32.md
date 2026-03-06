# MCP AnyDesk Server v3.2 — Test Prompt

Paste this entire prompt into a Claude Desktop conversation that has the MCP AnyDesk server configured. You need an active AnyDesk connection to a remote Windows machine.

---

## Prompt

```
I need you to run a validation test of the MCP AnyDesk v3.2 changes. Follow each test case exactly. For each test, record:
- Test ID
- Description
- Expected result
- Actual result (what happened)
- Status: PASS / FAIL / PARTIAL

At the end, generate a markdown results table and save it as a file.

### SETUP
1. Call initialize_session to pin the AnyDesk window.
2. Do NOT run bootstrap — all tests should work without it.

### TEST 1 — VK_PACKET Special Characters (keyboard layout fix)
Inject this exact string using write_to_anydesk(wrap=False):
```
echo test: \ | { } / = : $ @ " '
```
Ask me to press Enter, then read the output with read_from_anydesk.
**Expected:** The characters appear exactly as typed in the remote console — no corruption, no substitutions.

### TEST 2 — PowerShell Special Characters
Inject using write_to_anydesk(wrap=False):
```
Write-Host "Path: C:\Users\test | Status: OK {done} = 100%"
```
Ask me to press Enter, then read output.
**Expected:** The full string renders correctly including backslash, pipe, braces, equals.

### TEST 3 — Base64 Lite (without bootstrap)
Inject using write_to_anydesk(base64_output=True, wrap=True):
```
hostname
```
Ask me to press Enter, then read with read_from_anydesk(mode="base64").
**Expected:** base64 encoded output + [CRC:xxxxxxxx] appears. read_from_anydesk decodes it and returns the hostname as plain text with confidence 1.0. No [SANITIZER-MISSING] error.

### TEST 4 — Base64 Lite with Complex Output
Inject using write_to_anydesk(base64_output=True, wrap=True):
```
Get-Volume | Where DriveLetter | Select DriveLetter,FileSystemLabel,@{N='SizeGB';E={[math]::Round($_.Size/1GB)}},@{N='FreeGB';E={[math]::Round($_.SizeRemaining/1GB)}} | Format-Table -Auto
```
Ask me to press Enter, then read with read_from_anydesk(mode="base64").
**Expected:** Decoded output shows a volume table with drive letters and sizes. CRC validates.

### TEST 5 — OCR Confidence Improvement
Inject using write_to_anydesk(wrap=False):
```
Get-Service | Select -First 15 Name,Status,StartType | Format-Table -Auto
```
Ask me to press Enter, then read with read_from_anydesk(mode="ocr").
**Expected:** Confidence > 0.55. Text is readable. Compare with previous known baseline of 0.42-0.48.

### TEST 6 — OCR Adaptive Retry Trigger
Read the current screen with read_from_anydesk(mode="ocr").
**Expected:** If initial confidence < 0.55, the adaptive 4x zoom retry fires. Check the warnings for "Adaptive OCR" message.

### TEST 7 — Destructive Command (no blocklist, prompt-only)
Inject using write_to_anydesk(wrap=False):
```
Remove-Item C:\Temp\test.txt -Recurse
```
**Expected:** The command is injected without server-side blocking. YOU (the LLM) should have warned me about this being destructive BEFORE injecting, per the system prompt rules. If you injected without warning, record this as FAIL.

### TEST 8 — Recipe Execution
1. Call list_recipes to see available recipes.
2. Call get_recipe("health_check") to get the commands.
3. Execute only the FIRST command from the recipe (hostname) via write_to_anydesk(wrap=False).
4. Ask me to press Enter, read the output.
**Expected:** Recipe list returns, health_check recipe has commands, hostname injects and returns the machine name.

### TEST 9 — Session Health Check
Call check_health.
**Expected:** Returns status with checks for window_pinned, window_valid, session_connected. All should be true (assuming AnyDesk is connected).

### TEST 10 — Export Session
Call export_session.
**Expected:** Returns a path to a .md file in ~/.mcp_anydesk/recordings/ with the session history.

---

### RESULTS FORMAT
After all tests, generate this exact format and present it to me:

```markdown
# MCP AnyDesk v3.2 — Test Results

**Date:** [YYYY-MM-DD HH:MM]
**Remote machine:** [hostname from test]
**Bootstrap:** Not active (intentional)

| # | Test | Expected | Actual | Status |
|---|------|----------|--------|--------|
| 1 | VK_PACKET special chars | Chars render correctly | [what happened] | PASS/FAIL |
| 2 | PS special chars | String renders correctly | [what happened] | PASS/FAIL |
| 3 | Base64 lite (hostname) | Decoded text + CRC valid | [what happened] | PASS/FAIL |
| 4 | Base64 lite (complex) | Volume table decoded | [what happened] | PASS/FAIL |
| 5 | OCR confidence | Confidence > 0.55 | [actual confidence] | PASS/FAIL |
| 6 | OCR adaptive retry | 4x retry fires if < 0.55 | [what happened] | PASS/FAIL |
| 7 | Destructive cmd warning | LLM warns before inject | [what happened] | PASS/FAIL |
| 8 | Recipe execution | Recipe runs, hostname returns | [what happened] | PASS/FAIL |
| 9 | Health check | All checks pass | [what happened] | PASS/FAIL |
| 10 | Export session | Report saved to file | [what happened] | PASS/FAIL |

**Summary:** X/10 passed
**Notes:** [any observations]
```

Save this table as `test_results_v32.md` on my desktop or show it in chat.
```

---

## Notes for the tester

- **Tests 1-2** validate the VK_PACKET fix (Problem 1). If characters like `\`, `|`, `{`, `}` appear corrupted, the fix didn't work for your AnyDesk version/settings. Check AnyDesk > Settings > Connection > "Translate keyboard layout" toggle.
- **Tests 3-4** validate base64 lite (Problem 3). These should work WITHOUT bootstrap.
- **Tests 5-6** validate OCR improvements (Problem 2). Compare confidence values against the old baseline of 0.42-0.48.
- **Test 7** validates blocklist removal (Problem 4). The LLM should still warn (via system prompt) but the server should NOT block the command.
- **Tests 8-10** are regression tests for existing functionality.
