"""
Trading tools for order management and execution.

This module contains MCP tools for:
- Order placement (buy/sell)
- Order cancellation
- Order status and history
- Account state and positions
- Trade history retrieval
"""

import os
import sys
import logging
from typing import Optional, List
from datetime import datetime, timedelta

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
from lib.market_helpers import MarketHelpers
# Import common utilities
from .common import _safe_json, _require_auth, _normalize_mep_settlement_input, get_mcp

logger = logging.getLogger(__name__)

# Shared FastMCP instance provided by server
mcp = get_mcp()


@mcp.tool()
def send_order(
    symbol: str,
    side: str,
    size: int,
    price: Optional[float] = None,
    order_type: str = "LIMIT",
    time_in_force: str = "DAY",
    settlement: str = "24hs",
    account: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Place a buy or sell order.
    
    Args:
        symbol: Trading symbol (e.g., "DLR/DIC23", "GGAL", "AL30")
        side: "BUY" or "SELL"
        size: Order size/quantity
        price: Order price (required for LIMIT orders, ignored for MARKET)
        order_type: "MARKET" or "LIMIT" (default: LIMIT)
        time_in_force: "DAY", "IOC", "FOK", "GTD" (default: DAY)
        settlement: "CI" or "24hs" (legacy "T0"/"T1" accepted and normalized)
        account: Trading account (uses session account if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with order result
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Auto-detect market and full ticker (normalize settlement input)
        human_settlement = _normalize_mep_settlement_input(settlement)
        # detect_market_and_ticker accepts both human and legacy values
        market_enum, full_ticker = MarketHelpers.detect_market_and_ticker(symbol, human_settlement)
        if not market_enum:
            return _safe_json({
                "success": False, 
                "error": f"No se pudo determinar el mercado para {symbol}"
            })
        
        logger.info(f"Auto-detected market {market_enum} for order {symbol} -> {full_ticker} (settlement: {human_settlement})")
        
        # Validate parameters
        is_valid, validation_error = MarketHelpers.validate_order_parameters(
            symbol, side, size, price, order_type
        )
        if not is_valid:
            return _safe_json({"success": False, "error": validation_error})
        
        # Map parameters to enums
        side_enum = MarketHelpers.map_side_to_enum(side)
        order_type_enum = MarketHelpers.map_order_type_to_enum(order_type)
        tif_enum = MarketHelpers.map_time_in_force_to_enum(time_in_force)
        
        if not side_enum or not order_type_enum or not tif_enum:
            return _safe_json({
                "success": False, 
                "error": "Parámetros de orden inválidos"
            })
        
        # Use session account if not provided
        trading_account = account or session.account
        if not trading_account:
            return _safe_json({
                "success": False, 
                "error": "No hay cuenta de trading disponible"
            })
        
        # Send order with explicit market
        order_params = {
            "ticker": full_ticker,
            "side": side_enum,
            "size": size,
            "order_type": order_type_enum,
            "time_in_force": tif_enum,
            "account": trading_account,
            "market": market_enum
        }
        
        # Add price for LIMIT orders
        if order_type_enum == pyRofex.OrderType.LIMIT and price is not None:
            order_params["price"] = price
        
        result = pyRofex.send_order(**order_params)
        
        # Log raw response for debugging (truncate if too long)
        log_result = str(result)[:500] + "..." if len(str(result)) > 500 else result
        logger.info(f"Raw pyRofex.send_order response for user {user_id} (market={market_enum}, ticker={full_ticker}): {log_result}")
        
        # Validate response before formatting
        if result is None:
            logger.error(f"pyRofex.send_order returned None for user {user_id}")
            return _safe_json({
                "success": False, 
                "error": f"Error enviando orden para {symbol}: Sin respuesta del broker"
            })
        
        if not isinstance(result, dict):
            logger.error(f"pyRofex.send_order returned invalid type {type(result)} for user {user_id}: {result}")
            return _safe_json({
                "success": False, 
                "error": f"Error enviando orden para {symbol}: Respuesta inválida del broker"
            })
        
        # Format response
        formatted = MarketHelpers.format_order_response(result)
        
        # Check if formatting failed - DO NOT claim success
        if "error" in formatted:
            logger.error(f"Order response formatting failed for user {user_id}: {formatted['error']}")
            logger.error(f"Raw response was: {result}")
            available_keys = list(result.keys()) if isinstance(result, dict) else []
            return _safe_json({
                "success": False, 
                "error": f"No se pudo procesar la respuesta de la orden para {symbol}. Claves disponibles: {available_keys}",
                "symbol": symbol,
                "market": str(market_enum),
                "raw_keys": available_keys
            })
        
        logger.info(f"Order placed successfully for user {user_id}: {formatted.get('order_id', 'N/A')} - {symbol} on {market_enum}")
        
        return _safe_json({
            "success": True,
            "order": formatted,
            "symbol": symbol,
            "market": str(market_enum),
            "message": f"Orden {formatted.get('order_id', 'N/A')} enviada exitosamente para {symbol}"
        })
        
    except Exception as e:
        logger.error(f"send_order error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def cancel_order(
    order_id: str,
    proprietary: str = "api",
    user_id: str = "anonymous"
) -> str:
    """
    Cancel an existing order.
    
    Args:
        order_id: Order ID to cancel
        proprietary: Proprietary identifier (default: "api")
        user_id: User identifier
        
    Returns:
        JSON string with cancellation result
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        if not order_id:
            return _safe_json({"success": False, "error": "Order ID is required"})
        
        # Cancel order
        result = pyRofex.cancel_order(client_order_id=order_id, proprietary=proprietary)
        
        # Format response
        formatted = MarketHelpers.format_order_response(result)
        
        logger.info(f"Order cancelled for user {user_id}: {order_id}")
        
        return _safe_json({
            "success": True,
            "cancellation": formatted,
            "message": f"Cancellation order {formatted.get('order_id', 'N/A')} placed"
        })
        
    except Exception as e:
        logger.error(f"cancel_order error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_orders(
    status: Optional[str] = None,
    account: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Get list of orders for the account.
    
    Args:
        status: Filter by status (NEW, PARTIALLY_FILLED, FILLED, CANCELLED, etc.)
        account: Trading account (uses session account if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with orders list
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
        
        # Get all orders
        result = pyRofex.get_all_orders_status(accountId=trading_account)
        orders = result.get("orders", [])
        
        # Filter by status if provided
        if status:
            status_upper = status.upper()
            orders = [order for order in orders if order.get("status", "").upper() == status_upper]
        
        return _safe_json({
            "success": True,
            "orders": orders,
            "count": len(orders),
            "account": trading_account
        })
        
    except Exception as e:
        logger.error(f"get_orders error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_order_status(
    order_id: str,
    proprietary: str = "api",
    user_id: str = "anonymous"
) -> str:
    """
    Get status of a specific order.
    
    Args:
        order_id: Order ID to check
        proprietary: Proprietary identifier (default: "api")
        user_id: User identifier
        
    Returns:
        JSON string with order status
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        if not order_id:
            return _safe_json({"success": False, "error": "Order ID is required"})
        
        # Get order status
        result = pyRofex.get_order_status(clOrdId=order_id, proprietary=proprietary)
        
        return _safe_json({
            "success": True,
            "order_status": result
        })
        
    except Exception as e:
        logger.error(f"get_order_status error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


# =============================================================================
# ACCOUNT INFORMATION
# =============================================================================


@mcp.tool()
def get_account_state(
    account: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Get account summary and state.
    
    Args:
        account: Trading account (uses session account if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with account state
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
        
        # Get account report
        result = pyRofex.get_account_report(trading_account)
        
        return _safe_json({
            "success": True,
            "account": trading_account,
            "account_state": result
        })
        
    except Exception as e:
        logger.error(f"get_account_state error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_positions(
    account: Optional[str] = None,
    user_id: str = "anonymous"
) -> str:
    """
    Get current positions for the account.
    
    Args:
        account: Trading account (uses session account if not provided)
        user_id: User identifier
        
    Returns:
        JSON string with positions
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
        
        # Get positions
        positions = pyRofex.get_account_position(trading_account)
        detailed_positions = pyRofex.get_detailed_position(trading_account)
        
        return _safe_json({
            "success": True,
            "account": trading_account,
            "positions": positions,
            "detailed_positions": detailed_positions
        })
        
    except Exception as e:
        logger.error(f"get_positions error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_trade_history(
    symbol: str,
    from_date: str,
    to_date: str,
    market_id: str = "ROFEX",
    user_id: str = "anonymous"
) -> str:
    """
    Get historical trades for an instrument.
    
    Args:
        symbol: Trading symbol
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        market_id: Market identifier (ROFEX, MERV)
        user_id: User identifier
        
    Returns:
        JSON string with trade history
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Validate dates format
        try:
            datetime.strptime(from_date, "%Y-%m-%d")
            datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            return _safe_json({"success": False, "error": "Invalid date format. Use YYYY-MM-DD"})
        
        # Validate symbol
        if not MarketHelpers.validate_symbol(symbol):
            return _safe_json({"success": False, "error": f"Invalid symbol format: {symbol}"})
        
        # Map market
        market_enum = MarketHelpers.map_market_to_enum(market_id)
        if not market_enum:
            return _safe_json({"success": False, "error": f"Invalid market '{market_id}'"})
        
        # Get trade history
        result = pyRofex.get_trade_history(
            ticker=symbol,
            start_date=from_date,
            end_date=to_date,
            market=market_enum
        )
        
        return _safe_json({
            "success": True,
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
            "trades": result
        })
        
    except Exception as e:
        logger.error(f"get_trade_history error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


# =============================================================================
# MEP DOLLAR TRADING OPERATIONS
# =============================================================================


