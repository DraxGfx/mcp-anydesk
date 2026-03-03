"""Entry point for `python -m mcp_anydesk_server` and `mcp-anydesk` CLI."""

from .server import mcp


def main() -> None:
    """Run the MCP AnyDesk Server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
