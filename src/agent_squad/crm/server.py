"""MCP server (stdio). Exposes only the tools of SQUAD_ROLE: an agent cannot do what it has no tool for."""
from mcp.server.fastmcp import FastMCP

from . import ROLE_TOOLS, role


def main(argv: list[str] | None = None) -> None:
    mcp = FastMCP("crm")
    for tool in ROLE_TOOLS[role()]:
        mcp.add_tool(tool)
    mcp.run()


if __name__ == "__main__":
    main()
