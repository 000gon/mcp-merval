#!/usr/bin/env python3
"""MCP server for MERVAL trading via pyRofex."""

import logging
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from config import settings
from mcp.server.fastmcp import FastMCP

from lib.tools import register_all_tools

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger("pyrofex-mcp-server")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PYROFEX_SRC = os.path.abspath(os.path.join(REPO_ROOT, "pyRofex-master", "src"))
if PYROFEX_SRC not in sys.path:
    sys.path.insert(0, PYROFEX_SRC)

try:  # pragma: no cover - optional dependency
    import pyRofex  # noqa: F401  # pylint: disable=unused-import
    PYROFEX_AVAILABLE = True
    logger.info("✅ pyRofex library loaded successfully")
except ImportError as exc:
    PYROFEX_AVAILABLE = False
    logger.error("❌ pyRofex not available: %s", exc)

mcp = FastMCP(
    name="pyrofex-trading",
    instructions=(
        "Servidor MCP para operar con Matriz (ROFEX/MERVAL). "
        "Requiere credenciales LIVE y mantiene las sesiones en memoria. "
        "Incluye herramientas para cotizaciones, gestión de órdenes y dólar MEP."
    ),
)

register_all_tools(mcp)


if __name__ == "__main__":
    logger.info("🚀 Starting pyRofex MCP Server")
    logger.info("pyRofex Available: %s", PYROFEX_AVAILABLE)
    logger.info("Session storage: in-memory")
    mcp.run()
