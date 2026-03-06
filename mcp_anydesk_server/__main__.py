"""Entry point for `python -m mcp_anydesk_server` and `mcp-anydesk` CLI."""

from __future__ import annotations


def main() -> None:
    from .server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
