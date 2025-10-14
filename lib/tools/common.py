"""
Shared utilities and helper functions for MCP tools.

This module contains common functions used across all tool modules:
- JSON serialization helpers
- Session management utilities
- Authentication helpers
- Settlement normalization
- Market data fallback logic
"""

import os
import sys
import json
import logging
from typing import Any, Dict, Optional, Tuple

from mcp.server.fastmcp import FastMCP

# Add pyRofex to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYROFEX_SRC = os.path.abspath(os.path.join(REPO_ROOT, "pyRofex-master", "src"))
if PYROFEX_SRC not in sys.path:
    sys.path.insert(0, PYROFEX_SRC)

# Import configuration and components
from config import settings
from lib.pyrofex_session import PyRofexSession
from lib.session_registry import session_registry

logger = logging.getLogger(__name__)

_FAST_MCP: Optional[FastMCP] = None

def bind_mcp(instance: FastMCP) -> None:
    """Bind the shared FastMCP instance for tool registration."""
    global _FAST_MCP
    _FAST_MCP = instance

def get_mcp() -> FastMCP:
    """Return the shared FastMCP instance."""
    if _FAST_MCP is None:
        raise RuntimeError("FastMCP instance not bound. Call bind_mcp() before importing tool modules.")
    return _FAST_MCP


def _safe_json(data: Dict[str, Any]) -> str:
    """Safely convert dict to JSON string."""
    try:
        return json.dumps(data, default=str)
    except Exception as e:
        logger.error(f"JSON encoding error: {e}")
        return json.dumps({"success": False, "error": str(e)})


def _normalize_mep_settlement_input(value: Optional[str]) -> str:
    """Normalize human settlement input for MEP flows.

    - Default to 'CI' if missing/invalid
    - Accepts: 'CI', '24hs' (case-insensitive)
    - Also maps 'T0'->'CI' and 'T1'->'24hs' if those appear
    """
    if not value or not isinstance(value, str):
        return "CI"
    v = value.strip().upper()
    if v in ("CI",):
        return "CI"
    if v in ("24HS", "24H", "24 HORAS", "24-HS"):
        return "24hs"
    if v == "T0":
        return "CI"
    if v == "T1":
        return "24hs"
    # Fallback default
    return "CI"


def _get_session(user_id: str) -> Tuple[bool, Optional[str], Optional[PyRofexSession]]:
    """Recupera la sesión activa (solo memoria)."""
    logger.debug(f"Buscando sesión para {user_id}")

    session = session_registry.get_session(user_id)
    if session:
        logger.debug(f" Sesión válida en memoria para {user_id}")
        return True, None, session

    logger.debug(f"L Sin sesión activa para {user_id}")
    return False, f"El usuario {user_id} no está autenticado. Iniciá sesión primero.", None


def _ensure_authenticated(user_id: str) -> Tuple[bool, Optional[str], Optional[PyRofexSession]]:
    """Ensure user is authenticated, attempting auto-login from config if needed.

    Auto-login flow:
    1. Check if session already exists (return if yes)
    2. Check if user_id is configured in broker_config.json
    3. If configured, load credentials and authenticate automatically
    4. If not configured, return authentication error

    Args:
        user_id: User identifier

    Returns:
        Tuple of (success, error_message, session)
    """
    # First check if already authenticated
    success, error, session = _get_session(user_id)
    if success:
        return success, error, session

    # Try auto-login from config
    logger.debug(f"Attempting auto-login for {user_id}")
    account_config = settings.get_user_account(user_id)

    if not account_config:
        logger.debug(f"No auto-login config found for {user_id}")
        return False, f"El usuario {user_id} no está autenticado. Iniciá sesión primero.", None

    # Validate required fields
    username = account_config.get("username", "")
    password = account_config.get("password", "")
    account = account_config.get("account", "")
    broker_id = account_config.get("broker", "")

    if not all([username, password, account]):
        logger.warning(f"Incomplete credentials in config for {user_id}")
        return False, f"Configuración incompleta para {user_id}. Falta usuario, contraseña o cuenta.", None

    # Get broker config to set API URL
    broker_config = settings.get_broker_config(broker_id)
    api_url = None
    original_url = None
    if broker_config:
        api_url = broker_config.get("api_url")
        if api_url:
            # Temporarily update pyrofx_live_url for this authentication
            original_url = settings.pyrofx_live_url
            settings.pyrofx_live_url = api_url

    try:
        logger.info(f"= Auto-login attempt for {user_id} (broker: {broker_id})")

        # Create new session
        new_session = PyRofexSession(user_id)

        # Authenticate with retry logic
        auth_success = new_session.authenticate_with_retry(
            username,
            password,
            account,
            settings.live_environment,
            max_retries=3
        )

        if auth_success:
            # Store in memory
            session_registry.store_session(new_session)
            logger.info(f" Auto-login successful for {user_id}")
            return True, None, new_session
        else:
            logger.warning(f"L Auto-login failed for {user_id}")
            return False, f"Auto-login falló para {user_id}. Verificá las credenciales en broker_config.json", None

    except Exception as e:
        logger.error(f"L Auto-login error for {user_id}: {e}")
        return False, f"Error en auto-login para {user_id}: {str(e)}", None
    finally:
        # Restore original URL if it was changed
        if broker_config and api_url and original_url:
            settings.pyrofx_live_url = original_url


def _require_auth(user_id: str) -> Tuple[bool, Optional[str], Optional[PyRofexSession]]:
    """Require authentication for operations.

    Automatically attempts to authenticate from config if no session exists.
    """
    return _ensure_authenticated(user_id)


def _fallback_marketdata_via_pyrofex(
    symbol: str,
    settlement: str,
    depth: int,
    user_id: str,
) -> Dict[str, Any]:
    """
    Fallback to pyRofex REST when the external marketdata service is unavailable.
    Returns a consistent payload structure regardless of the original request type.

    Note: This function depends on get_market_data which will be imported from market_data module.
    """
    try:
        # Import here to avoid circular dependency
        from .market_data import get_market_data

        normalized_settlement = _normalize_mep_settlement_input(settlement)
        md_json = get_market_data(
            symbol=symbol,
            entries=["BIDS", "OFFERS", "LAST"],
            depth=depth,
            settlement=normalized_settlement,
            user_id=user_id,
        )
        parsed = json.loads(md_json) if isinstance(md_json, str) else md_json
        if not parsed.get("success"):
            return parsed

        data_block = (parsed.get("market_data") or {}).get("data", {}) or {}
        bid_entry = data_block.get("bid") or {}
        offer_entry = data_block.get("offer") or {}
        last_entry = data_block.get("last") or {}

        # Build consistent payload with optional book data
        bids = []
        offers = []
        if bid_entry.get("price") is not None:
            bids.append({"price": bid_entry.get("price"), "size": bid_entry.get("size")})
        if offer_entry.get("price") is not None:
            offers.append({"price": offer_entry.get("price"), "size": offer_entry.get("size")})

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "settlement": normalized_settlement,
                "source": "pyrofex_fallback",
                "bid": {"price": bid_entry.get("price"), "size": bid_entry.get("size")},
                "ask": {"price": offer_entry.get("price"), "size": offer_entry.get("size")},
                "last": last_entry.get("price"),
                "timestamp": parsed.get("market_data", {}).get("timestamp"),
                "bids": bids[:depth] if depth and depth > 0 else bids,
                "offers": offers[:depth] if depth and depth > 0 else offers,
            }
        }
    except Exception as err:
        return {"success": False, "error": f"Fallback pyRofex failed: {err}"}
