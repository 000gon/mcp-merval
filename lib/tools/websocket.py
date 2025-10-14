"""
WebSocket tools for real-time market data and order updates.

This module contains MCP tools for:
- Market data subscriptions (real-time quotes)
- Order report subscriptions (real-time order updates)
- Cached quote retrieval
- Order update history
- Subscription management
"""

import os
import sys
import logging
from typing import Optional, List, Dict, Any

# Add pyRofex to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYROFEX_SRC = os.path.abspath(os.path.join(REPO_ROOT, "pyRofex-master", "src"))
if PYROFEX_SRC not in sys.path:
    sys.path.insert(0, PYROFEX_SRC)

try:
    import pyRofex
    PYROFEX_AVAILABLE = True
except ImportError:
    PYROFEX_AVAILABLE = False

# Import configuration and components
from config import settings
from lib.session_registry import session_registry
from lib.market_helpers import MarketHelpers
# Import common utilities
from .common import _safe_json, _require_auth, get_mcp

logger = logging.getLogger(__name__)

# Shared FastMCP instance provided by server
mcp = get_mcp()


@mcp.tool()
def subscribe_market_data(
    symbols: List[str],
    entries: List[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Subscribe to real-time market data for instruments.
    
    Args:
        symbols: List of symbols to subscribe to
        entries: Data entries to subscribe (BIDS, OFFERS, LAST, etc.)
        user_id: User identifier
        
    Returns:
        JSON string with subscription result
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        if not symbols:
            return _safe_json({"success": False, "error": "At least one symbol required"})
        
        # Default entries
        if entries is None:
            entries = ["BIDS", "OFFERS", "LAST"]
        
        # Map entries to enums
        entry_enums = MarketHelpers.map_market_data_entries(entries)
        if not entry_enums:
            return _safe_json({"success": False, "error": "Invalid market data entries"})
        
        # Validate symbols
        for symbol in symbols:
            if not MarketHelpers.validate_symbol(symbol):
                return _safe_json({"success": False, "error": f"Invalid symbol format: {symbol}"})
        
        state = session_registry.get_connection_state(user_id)

        # Initialize WebSocket si aún no se levantó
        if not session_registry.websocket_initialized(user_id):
            try:
                md_handler = _create_market_data_handler(user_id)
                or_handler = _create_order_report_handler(user_id)
                err_handler = _create_error_handler(user_id)
                exc_handler = _create_exception_handler(user_id)

                session.init_websocket(
                    market_data_handler=md_handler,
                    order_report_handler=or_handler,
                    error_handler=err_handler,
                    exception_handler=exc_handler,
                )

                session_registry.mark_websocket_initialized(user_id)
                logger.info(f"WebSocket inicializado para {user_id}")

            except Exception as e:
                return _safe_json({"success": False, "error": f"Failed to initialize WebSocket: {str(e)}"})
        
        # Subscribe to market data
        try:
            pyRofex.market_data_subscription(tickers=symbols, entries=entry_enums)
            
            # Track subscriptions
            for symbol in symbols:
                if symbol not in state["market_subscriptions"]:
                    state["market_subscriptions"].append(symbol)
            
            # Update session subscriptions
            for symbol in symbols:
                session.active_subscriptions[f"md:{symbol}"] = {
                    "type": "market_data",
                    "symbol": symbol,
                    "entries": entries
                }
            
            logger.info(f"Market data subscription created for user {user_id}: {symbols}")
            
            return _safe_json({
                "success": True,
                "subscribed_symbols": symbols,
                "entries": entries,
                "message": f"Subscribed to market data for {len(symbols)} symbols"
            })
            
        except Exception as e:
            return _safe_json({"success": False, "error": f"Subscription failed: {str(e)}"})
        
    except Exception as e:
        logger.error(f"subscribe_market_data error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def subscribe_order_reports(
    account: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Subscribe to real-time order reports.
    
    Args:
        account: Trading account (uses session account if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with subscription result
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Use session account if not provided
        trading_account = account or session.account
        if not trading_account:
            return _safe_json({"success": False, "error": "No trading account available"})
        
        state = session_registry.get_connection_state(user_id)

        if not session_registry.websocket_initialized(user_id):
            try:
                md_handler = _create_market_data_handler(user_id)
                or_handler = _create_order_report_handler(user_id)
                err_handler = _create_error_handler(user_id)
                exc_handler = _create_exception_handler(user_id)

                session.init_websocket(
                    market_data_handler=md_handler,
                    order_report_handler=or_handler,
                    error_handler=err_handler,
                    exception_handler=exc_handler,
                )

                session_registry.mark_websocket_initialized(user_id)
                logger.info(f"WebSocket inicializado para {user_id}")

            except Exception as e:
                return _safe_json({"success": False, "error": f"Failed to initialize WebSocket: {str(e)}"})
        
        # Subscribe to order reports
        try:
            pyRofex.order_report_subscription()
            
            # Track subscription
            if trading_account not in state["order_subscriptions"]:
                state["order_subscriptions"].append(trading_account)
            
            # Update session subscriptions
            session.active_subscriptions[f"or:{trading_account}"] = {
                "type": "order_reports",
                "account": trading_account
            }
            
            logger.info(f"Order reports subscription created for user {user_id}, account {trading_account}")
            
            return _safe_json({
                "success": True,
                "account": trading_account,
                "message": "Subscribed to order reports"
            })
            
        except Exception as e:
            return _safe_json({"success": False, "error": f"Order subscription failed: {str(e)}"})
        
    except Exception as e:
        logger.error(f"subscribe_order_reports error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_cached_quotes(
    symbol: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Get cached real-time quotes.
    
    Args:
        symbol: Specific symbol (returns all if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with cached quotes
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        user_quotes = session_registry.list_quotes(user_id)

        if symbol:
            normalized_symbol = symbol.upper()
            if normalized_symbol not in user_quotes:
                return _safe_json({
                    "success": False,
                    "error": f"No hay datos cacheados para {symbol}. Suscribite primero."
                })
            quote_payload = user_quotes.get(normalized_symbol)
            return _safe_json({
                "success": True,
                "symbol": normalized_symbol,
                "quotes": quote_payload,
                "count": 1
            })

        return _safe_json({
            "success": True,
            "symbol": None,
            "quotes": user_quotes,
            "count": len(user_quotes)
        })
        
    except Exception as e:
        logger.error(f"get_cached_quotes error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_order_updates(
    limit: int = 10,
    user_id: str = "anonymous"
) -> str:
    """
    Get recent order updates from WebSocket feed.
    
    Args:
        limit: Maximum number of updates to return
        user_id: User identifier
        
    Returns:
        JSON string with order updates
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        updates = session_registry.list_order_updates(user_id)
        if limit > 0:
            updates = updates[-limit:]

        return _safe_json({
            "success": True,
            "updates": updates,
            "count": len(updates)
        })
        
    except Exception as e:
        logger.error(f"get_order_updates error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def unsubscribe_all(user_id: str = "anonymous") -> str:
    """
    Unsubscribe from all WebSocket feeds and close connection.
    
    Args:
        user_id: User identifier
        
    Returns:
        JSON string with result
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Close WebSocket connection
        try:
            pyRofex.close_websocket_connection()
            logger.info(f"WebSocket connection closed for user {user_id}")
        except Exception as e:
            logger.warning(f"Error closing WebSocket for user {user_id}: {e}")
        
        # Clear session subscriptions
        session.active_subscriptions.clear()
        
        # Clear cached state
        session_registry.remove_connection(user_id)
        session_registry.clear_user_quotes(user_id)

        return _safe_json({
            "success": True,
            "message": "All subscriptions closed and cache cleared"
        })
        
    except Exception as e:
        logger.error(f"unsubscribe_all error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_subscription_status(user_id: str = "anonymous") -> str:
    """
    Get current WebSocket subscription status.
    
    Args:
        user_id: User identifier
        
    Returns:
        JSON string with subscription status
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        state = session_registry.peek_connection_state(user_id) or {}

        return _safe_json({
            "success": True,
            "websocket_active": session_registry.websocket_initialized(user_id),
            "market_subscriptions": state.get("market_subscriptions", []),
            "order_subscriptions": state.get("order_subscriptions", []),
            "cached_quotes_count": len(session_registry.list_quotes(user_id)),
            "recent_updates": session_registry.order_update_count(user_id)
        })
        
    except Exception as e:
        logger.error(f"get_subscription_status error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


if __name__ == "__main__":
    logger.info("ð Starting pyRofex MCP Server")
    logger.info(f"pyRofex Available: {PYROFEX_AVAILABLE}")
    logger.info("Session storage: in-memory")
    mcp.run()

