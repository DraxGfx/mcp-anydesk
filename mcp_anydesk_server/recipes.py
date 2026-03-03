"""Pre-built command sequences for common sysadmin tasks (N8).

Each recipe is a list of PowerShell commands that the LLM executes
sequentially, following the standard inject -> Enter -> read cycle
for each one.
"""

from __future__ import annotations


RECIPES: dict[str, dict] = {
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
        "description": "Automatic services that are not running",
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
    """Return a summary of all available recipes."""
    return [
        {"name": name, "description": r["description"], "steps": len(r["commands"])}
        for name, r in RECIPES.items()
    ]
