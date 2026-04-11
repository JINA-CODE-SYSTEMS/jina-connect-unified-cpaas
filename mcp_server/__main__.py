"""
Run the Jina Connect MCP server.

Usage:
    python -m mcp_server                      # stdio transport (Claude Desktop, Cursor)
    python -m mcp_server --transport http      # streamable HTTP on port 9000
    python -m mcp_server --transport http --port 8001
"""

import argparse

from mcp_server.server import mcp

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Jina Connect MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )
    parser.add_argument("--port", type=int, default=9000, help="HTTP port (default: 9000)")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host (default: 0.0.0.0)")  # noqa: S104

    args = parser.parse_args()

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
