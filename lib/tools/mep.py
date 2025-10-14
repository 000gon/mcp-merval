"""
MEP (Mercado Electrónico de Pagos) dollar calculation and execution tools.

This module contains MCP tools for:
- MEP dollar exchange rate calculation
- MEP buy/sell operation preview
- MEP order execution (both legs)
- Bond-based USD/ARS arbitrage flows
"""

import os
import sys
import json
import logging
from typing import Optional, Dict, Any, List

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
# Import common utilities
from .common import _safe_json, _require_auth, _normalize_mep_settlement_input, get_mcp

logger = logging.getLogger(__name__)

# Shared FastMCP instance provided by server
mcp = get_mcp()


@mcp.tool()
def calculate_mep_price(
    bond_symbol: str = "AL30",
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Calculate current MEP dollar exchange rate using bond pairs (AL30/AL30D).

    Args:
        bond_symbol: Bond symbol to use for MEP calculation (default: AL30)
        settlement: Settlement type - "CI" for T0 or "24hs" for T1 (default: CI)
        user_id: User identifier

    Returns:
        JSON string with MEP buy/sell rates and spread
    """
    # Normalize settlement to 'CI' or '24hs' (default CI)
    settlement = _normalize_mep_settlement_input(settlement)

    if settings.use_pyrofex_for_mep:
        logger.info(f"Using pyRofex for MEP calculation (user: {user_id})")
        try:
            # Try primary method with get_market_data
            return _calculate_mep_via_pyrofex(bond_symbol, settlement, user_id)
        except Exception as e:
            logger.warning(f"pyRofex MEP calculation failed: {e}")

            # Fallback to marketdata service if available
            if settings.marketdata_url:
                logger.info("Falling back to marketdata service")
                try:
                    return _calculate_mep_via_marketdata(bond_symbol, settlement, user_id)
                except Exception as e2:
                    logger.error(f"Fallback MEP calculation also failed: {e2}")

            return _safe_json({
                "success": False,
                "error": "No se pudo calcular el precio MEP. Tanto pyRofex como el servicio de respaldo fallaron."
            })
    else:
        logger.info(f"Using marketdata service for MEP calculation (user: {user_id})")
        # Current implementation
        return _calculate_mep_via_marketdata(bond_symbol, settlement, user_id)



@mcp.tool()
def preview_mep_buy(
    usd_amount: float,
    bond_symbol: str = "AL30",
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Preview MEP dollar buy operation (buy USD, pay ARS).
    Generates two orders: BUY USD bond + SELL ARS bond.

    Args:
        usd_amount: Amount of USD to buy
        bond_symbol: Bond symbol to use for MEP (default: AL30)
        settlement: Settlement type - "CI" for T0 or "24hs" for T1 (default: CI)
        user_id: User identifier

    Returns:
        JSON string with order preview and effective rate
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})

        if usd_amount <= 0:
            return _safe_json({
                "success": False,
                "error": "El monto en USD debe ser mayor a cero"
            })

        # Normalize and get current MEP rate
        settlement = _normalize_mep_settlement_input(settlement)
        mep_calc_result = calculate_mep_price(bond_symbol, settlement, user_id)
        mep_data = json.loads(mep_calc_result)

        if not mep_data.get("success"):
            return _safe_json({
                "success": False,
                "error": f"No se pudo obtener la cotización MEP: {mep_data.get('error')}"
            })

        mep_rates = mep_data["mep_rates"]
        underlying = mep_data["underlying_quotes"]

        # Calculate bond quantities needed using MEP rate approach
        ars_bond = underlying["ars_bond"]
        usd_bond = underlying["usd_bond"]

        usd_display_price = float(usd_bond.get("ask")) if usd_bond.get("ask") is not None else 0.0
        usd_raw_price = float(usd_bond.get("raw_ask")) if usd_bond.get("raw_ask") is not None else usd_display_price * 100
        ars_display_price = float(ars_bond.get("bid")) if ars_bond.get("bid") is not None else 0.0
        ars_raw_price = float(ars_bond.get("raw_bid")) if ars_bond.get("raw_bid") is not None else ars_display_price * 100

        if usd_display_price <= 0 or ars_display_price <= 0:
            return _safe_json({
                "success": False,
                "error": "No se pudieron obtener precios válidos para la operación MEP."
            })

        # Get MEP buy rate (ARS per USD)
        mep_buy_rate = mep_rates["buy_rate"]

        # For MEP buy: Calculate required bond quantities based on USD amount
        # USD bonds needed = requested USD / USD bond ask price (per nominal)
        usd_bond_quantity = max(1, round(usd_amount / usd_display_price))

        # ARS bonds to sell = same nominal quantity (paired MEP operation)
        ars_bond_quantity = usd_bond_quantity

        # Calculate actual amounts based on bond quantities
        actual_usd_cost = round(usd_bond_quantity * usd_display_price, 2)
        broker_usd_cost = round(usd_bond_quantity * usd_raw_price, 2)
        actual_ars_received = round(ars_bond_quantity * ars_display_price, 2)
        broker_ars_received = round(ars_bond_quantity * ars_raw_price, 2)

        # Validate minimum trade amount (warn if actual amount differs significantly)
        if abs(actual_usd_cost - usd_amount) > (usd_amount * 0.1):  # More than 10% difference
            logger.warning(
                "MEP buy: requested $%s, actual $%s (difference: $%s)",
                usd_amount,
                actual_usd_cost,
                round(abs(actual_usd_cost - usd_amount), 2),
            )

        # Calculate effective rate from actual execution
        effective_rate = round(actual_ars_received / actual_usd_cost, 2) if actual_usd_cost > 0 else 0

        # Map settlement for order generation
        order_settlement = "T0" if settlement.upper() == "CI" else "T1"

        # Calculate commission for MEP operations (0.5% per leg)
        mep_commission_rate = settings.commission_rate
        usd_commission = round(actual_usd_cost * mep_commission_rate, 2)
        ars_commission = round(actual_ars_received * mep_commission_rate, 2)

        # Generate orders
        orders = [
            {
                "symbol": f"{bond_symbol.upper()}D",
                "side": "BUY",
                "size": usd_bond_quantity,
                "price": usd_display_price,
                "display_price": usd_display_price,
                "broker_price": usd_raw_price,
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "settlement": order_settlement,
                "estimated_amount_usd": actual_usd_cost,
                "estimated_cost": actual_usd_cost,
                "display_estimated_cost": actual_usd_cost,
                "broker_estimated_cost": broker_usd_cost,
                "commission": usd_commission,
                "currency": "USD"
            },
            {
                "symbol": bond_symbol.upper(),
                "side": "SELL",
                "size": ars_bond_quantity,
                "price": ars_display_price,
                "display_price": ars_display_price,
                "broker_price": ars_raw_price,
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "settlement": order_settlement,
                "estimated_amount_ars": actual_ars_received,
                "estimated_result": actual_ars_received,
                "display_estimated_result": actual_ars_received,
                "broker_estimated_result": broker_ars_received,
                "commission": ars_commission,
                "currency": "ARS"
            }
        ]

        return _safe_json({
            "success": True,
            "operation_type": "MEP_BUY",
            "requested_usd": usd_amount,
            "effective_rate": effective_rate,
            "market_rate": mep_rates["buy_rate"],
            "rate_difference": round(effective_rate - mep_rates["buy_rate"], 2),
            "bond_symbol": bond_symbol,
            "settlement": settlement,
            "orders": orders,
            "summary": {
                "usd_bonds_to_buy": usd_bond_quantity,
                "ars_bonds_to_sell": ars_bond_quantity,
                "total_usd_cost": actual_usd_cost,
                "total_usd_cost_broker": broker_usd_cost,
                "total_ars_received": actual_ars_received,
                "total_ars_received_broker": broker_ars_received,
                "net_ars_cost": round(actual_ars_received - actual_usd_cost * effective_rate, 2)
            }
        })

    except Exception as e:
        logger.error(f"preview_mep_buy error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def preview_mep_sell(
    usd_amount: float,
    bond_symbol: str = "AL30",
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Preview MEP dollar sell operation (sell USD, receive ARS).
    Generates two orders: SELL USD bond + BUY ARS bond.

    Args:
        usd_amount: Amount of USD to sell
        bond_symbol: Bond symbol to use for MEP (default: AL30)
        settlement: Settlement type - "CI" for T0 or "24hs" for T1 (default: CI)
        user_id: User identifier

    Returns:
        JSON string with order preview and effective rate
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})

        if usd_amount <= 0:
            return _safe_json({
                "success": False,
                "error": "El monto en USD debe ser mayor a cero"
            })

        # Normalize and get current MEP rate
        settlement = _normalize_mep_settlement_input(settlement)
        mep_calc_result = calculate_mep_price(bond_symbol, settlement, user_id)
        mep_data = json.loads(mep_calc_result)

        if not mep_data.get("success"):
            return _safe_json({
                "success": False,
                "error": f"No se pudo obtener la cotización MEP: {mep_data.get('error')}"
            })

        mep_rates = mep_data["mep_rates"]
        underlying = mep_data["underlying_quotes"]

        # Calculate bond quantities needed using MEP rate approach
        ars_bond = underlying["ars_bond"]
        usd_bond = underlying["usd_bond"]

        usd_display_price = float(usd_bond.get("bid")) if usd_bond.get("bid") is not None else 0.0
        usd_raw_price = float(usd_bond.get("raw_bid")) if usd_bond.get("raw_bid") is not None else usd_display_price * 100
        ars_display_price = float(ars_bond.get("ask")) if ars_bond.get("ask") is not None else 0.0
        ars_raw_price = float(ars_bond.get("raw_ask")) if ars_bond.get("raw_ask") is not None else ars_display_price * 100

        if usd_display_price <= 0 or ars_display_price <= 0:
            return _safe_json({
                "success": False,
                "error": "No se pudieron obtener precios válidos para la operación MEP."
            })

        # Get MEP sell rate (ARS per USD)
        mep_sell_rate = mep_rates["sell_rate"]

        # For MEP sell: Calculate required bond quantities based on USD amount
        # USD bonds to sell = requested USD / USD bond bid price (per nominal)
        usd_bond_quantity = max(1, round(usd_amount / usd_display_price))

        # ARS bonds to buy = same nominal quantity (paired MEP operation)
        ars_bond_quantity = usd_bond_quantity

        # Calculate actual amounts based on bond quantities
        actual_usd_received = round(usd_bond_quantity * usd_display_price, 2)
        broker_usd_received = round(usd_bond_quantity * usd_raw_price, 2)
        actual_ars_cost = round(ars_bond_quantity * ars_display_price, 2)
        broker_ars_cost = round(ars_bond_quantity * ars_raw_price, 2)

        # Validate minimum trade amount (warn if actual amount differs significantly)
        if abs(actual_usd_received - usd_amount) > (usd_amount * 0.1):  # More than 10% difference
            logger.warning(
                "MEP sell: requested $%s, actual $%s (difference: $%s)",
                usd_amount,
                actual_usd_received,
                round(abs(actual_usd_received - usd_amount), 2),
            )

        # Calculate effective rate from actual execution
        effective_rate = round(actual_ars_cost / actual_usd_received, 2) if actual_usd_received > 0 else 0

        # Map settlement for order generation
        order_settlement = "T0" if settlement.upper() == "CI" else "T1"

        # Calculate commission for MEP operations (0.5% per leg)
        mep_commission_rate = settings.commission_rate
        usd_commission = round(actual_usd_received * mep_commission_rate, 2)
        ars_commission = round(actual_ars_cost * mep_commission_rate, 2)

        # Generate orders
        orders = [
            {
                "symbol": f"{bond_symbol.upper()}D",
                "side": "SELL",
                "size": usd_bond_quantity,
                "price": usd_display_price,
                "display_price": usd_display_price,
                "broker_price": usd_raw_price,
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "settlement": order_settlement,
                "estimated_amount_usd": actual_usd_received,
                "estimated_result": actual_usd_received,
                "display_estimated_result": actual_usd_received,
                "broker_estimated_result": broker_usd_received,
                "commission": usd_commission,
                "currency": "USD"
            },
            {
                "symbol": bond_symbol.upper(),
                "side": "BUY",
                "size": ars_bond_quantity,
                "price": ars_display_price,
                "display_price": ars_display_price,
                "broker_price": ars_raw_price,
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "settlement": order_settlement,
                "estimated_amount_ars": actual_ars_cost,
                "estimated_cost": actual_ars_cost,
                "display_estimated_cost": actual_ars_cost,
                "broker_estimated_cost": broker_ars_cost,
                "commission": ars_commission,
                "currency": "ARS"
            }
        ]

        return _safe_json({
            "success": True,
            "operation_type": "MEP_SELL",
            "requested_usd": usd_amount,
            "effective_rate": effective_rate,
            "market_rate": mep_rates["sell_rate"],
            "rate_difference": round(effective_rate - mep_rates["sell_rate"], 2),
            "bond_symbol": bond_symbol,
            "settlement": settlement,
            "orders": orders,
            "summary": {
                "usd_bonds_to_sell": usd_bond_quantity,
                "ars_bonds_to_buy": ars_bond_quantity,
                "total_usd_received": actual_usd_received,
                "total_usd_received_broker": broker_usd_received,
                "total_ars_cost": actual_ars_cost,
                "total_ars_cost_broker": broker_ars_cost,
                "net_ars_received": round(actual_ars_cost - actual_usd_received * effective_rate, 2)
            }
        })

    except Exception as e:
        logger.error(f"preview_mep_sell error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


# =============================================================================
# MEP EXECUTION (MARKET)
# =============================================================================


@mcp.tool()
def execute_mep_orders(
    orders: List[Dict[str, Any]],
    user_id: str = "anonymous"
) -> str:
    """
    Execute both MEP legs as MARKET orders using a preview's orders array.

    Args:
        orders: Array from preview_mep_buy/preview_mep_sell["orders"]. Each item
                should include at least: symbol, side, size, settlement, time_in_force.
        user_id: User identifier

    Returns:
        JSON string with per-leg results and a summary.
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})

        session.update_activity()

        if not orders or not isinstance(orders, list):
            return _safe_json({"success": False, "error": "Orders array requerido (previsualización MEP)"})

        executions: List[Dict[str, Any]] = []
        any_failed = False

        for idx, o in enumerate(orders):
            try:
                symbol = (o or {}).get("symbol")
                side = (o or {}).get("side")
                size = (o or {}).get("size")
                settlement_in = (o or {}).get("settlement")
                # Accept both human ('CI'/'24hs') and broker ('T0'/'T1') inputs, default CI
                human_settlement = _normalize_mep_settlement_input(settlement_in)
                settlement = "T0" if human_settlement.upper() == "CI" else "T1"
                tif = (o or {}).get("time_in_force") or "DAY"

                if not symbol or not side or not size:
                    executions.append({
                        "index": idx,
                        "success": False,
                        "error": f"Previsualización inválida: faltan campos (symbol/side/size)",
                    })
                    any_failed = True
                    continue

                # Enforce MARKET regardless of preview content; omit price
                resp_json = send_order(
                    symbol=symbol,
                    side=side,
                    size=size,
                    order_type="MARKET",
                    time_in_force=tif,
                    settlement=settlement,
                    user_id=user_id,
                )

                resp = json.loads(resp_json)
                if resp.get("success"):
                    executions.append({
                        "index": idx,
                        "success": True,
                        "order": resp.get("order"),
                        "symbol": symbol,
                        "side": side,
                        "size": size,
                        "settlement": human_settlement,
                    })
                else:
                    executions.append({
                        "index": idx,
                        "success": False,
                        "error": resp.get("error"),
                        "symbol": symbol,
                        "side": side,
                        "size": size,
                        "settlement": human_settlement,
                    })
                    any_failed = True

            except Exception as leg_err:
                executions.append({
                    "index": idx,
                    "success": False,
                    "error": str(leg_err),
                })
                any_failed = True

        return _safe_json({
            "success": not any_failed,
            "legs": len(orders),
            "executions": executions,
            "message": ("Ambas piernas enviadas" if not any_failed else "Alguna pierna falló; revisar ejecuciones"),
        })

    except Exception as e:
        logger.error(f"execute_mep_orders error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def execute_mep_buy(
    usd_amount: float,
    bond_symbol: str = "AL30",
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Convenience: preview and execute a MEP BUY (BUY USD via bond + SELL ARS bond).
    Always uses MARKET for both legs.
    """
    try:
        # Normalize settlement; default CI
        settlement = _normalize_mep_settlement_input(settlement)
        prev_json = preview_mep_buy(usd_amount, bond_symbol, settlement, user_id)
        prev = json.loads(prev_json)
        if not prev.get("success"):
            return _safe_json(prev)

        exec_json = execute_mep_orders(prev.get("orders", []), user_id)
        exec_data = json.loads(exec_json)
        return _safe_json({
            "success": exec_data.get("success", False),
            "operation_type": "MEP_BUY",
            "preview": prev,
            "execution": exec_data,
        })
    except Exception as e:
        logger.error(f"execute_mep_buy error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def execute_mep_sell(
    usd_amount: float,
    bond_symbol: str = "AL30",
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Convenience: preview and execute a MEP SELL (SELL USD bond + BUY ARS bond).
    Always uses MARKET for both legs.
    """
    try:
        # Normalize settlement; default CI
        settlement = _normalize_mep_settlement_input(settlement)
        prev_json = preview_mep_sell(usd_amount, bond_symbol, settlement, user_id)
        prev = json.loads(prev_json)
        if not prev.get("success"):
            return _safe_json(prev)

        exec_json = execute_mep_orders(prev.get("orders", []), user_id)
        exec_data = json.loads(exec_json)
        return _safe_json({
            "success": exec_data.get("success", False),
            "operation_type": "MEP_SELL",
            "preview": prev,
            "execution": exec_data,
        })
    except Exception as e:
        logger.error(f"execute_mep_sell error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})

# =============================================================================
# WEBSOCKET REAL-TIME FEATURES
# =============================================================================

def _create_market_data_handler(user_id: str):
    """Create market data handler for specific user."""
    def handler(message):
        try:
            symbol = message.get("instrumentId", {}).get("symbol", "")
            market_data = message.get("marketData", {})
            
            # Cache the data
            if symbol:
                # Normalize for display if bond
                def _div100(v):
                    try:
                        return round(float(v) / 100.0, 6)
                    except Exception:
                        return v
                is_bond = False
                try:
                    is_bond = MarketHelpers.is_bond_symbol(symbol)
                except Exception:
                    is_bond = False

                bid_price = (market_data.get("BI", [{}])[0] or {}).get("price")
                ask_price = (market_data.get("OF", [{}])[0] or {}).get("price")
                last_price = market_data.get("LA", {}).get("price")
                high_price = market_data.get("HI", {}).get("price")
                low_price = market_data.get("LO", {}).get("price")

                if is_bond:
                    bid_price = _div100(bid_price)
                    ask_price = _div100(ask_price)
                    last_price = _div100(last_price)
                    high_price = _div100(high_price)
                    low_price = _div100(low_price)

                session_registry.store_quote(
                    user_id,
                    symbol,
                    {
                        "symbol": symbol,
                        "timestamp": message.get("timestamp"),
                        "bid": bid_price,
                        "ask": ask_price,
                        "last": last_price,
                        "volume": market_data.get("VU", {}).get("size"),
                        "high": high_price,
                        "low": low_price,
                        "user_id": user_id,
                    },
                )
                logger.debug(f"Market data updated para {user_id}:{symbol}")
        except Exception as e:
            logger.warning(f"Market data handler error for user {user_id}: {e}")
    
    return handler


def _create_order_report_handler(user_id: str):
    """Create order report handler for specific user."""
    def handler(message):
        try:
            order_id = message.get("clOrdId", "")
            status = message.get("status", "")
            
            logger.info(f"Order update for user {user_id}: {order_id} -> {status}")
            
            session_registry.append_order_update(
                user_id,
                {
                    "timestamp": message.get("timestamp"),
                    "order_id": order_id,
                    "status": status,
                    "message": message,
                },
            )
                
        except Exception as e:
            logger.warning(f"Order report handler error for user {user_id}: {e}")
    
    return handler


def _create_error_handler(user_id: str):
    """Create error handler for specific user."""
    def handler(message):
        logger.warning(f"WebSocket error for user {user_id}: {message}")
    return handler


def _create_exception_handler(user_id: str):
    """Create exception handler for specific user."""
    def handler(exception):
        logger.error(f"WebSocket exception for user {user_id}: {exception}")
        # Mark websocket as uninitialized so it can be retried
        state = session_registry.get_connection_state(user_id)
        state["initialized"] = False
    return handler



