"""Tool registration helpers for the pyRofex trading server."""

from typing import TYPE_CHECKING

from .common import bind_mcp

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP

__all__ = ["register_all_tools"]


def register_all_tools(mcp: "FastMCP") -> None:
    """Bind shared FastMCP instance and import tool modules for registration."""
    bind_mcp(mcp)

    # Import modules lazily so decorators run after the MCP instance is bound.
    from . import auth, market_data, mep, trading, websocket  # noqa: F401

    # Explicitly reference modules to silence lint about unused imports.
    _ = (auth, market_data, mep, trading, websocket)
