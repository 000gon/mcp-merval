"""
Market data retrieval and instrument information tools.

This module contains MCP tools for:
- Instrument listings and search
- Market data quotes and orderbook
- Instrument details and segments
- Integration with external marketdata service
"""

import os
import sys
import json
import logging
import time
from typing import Any, Dict, List, Optional
from datetime import datetime

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


def _get_marketdata_base_url() -> str:
    return settings.marketdata_url or os.getenv("MARKETDATA_SERVICE_URL") or "http://localhost:8000"



def _fetch_bond_quotes_for_mep(
    bond_symbol: str,
    settlement: str,
    user_id: str
) -> tuple[dict, dict]:
    """
    Fetch both ARS and USD bond quotes using get_market_data for MEP calculations.

    Args:
        bond_symbol: Base bond symbol (e.g., "AL30")
        settlement: Settlement type - "CI" for T0 or "24hs" for T1
        user_id: User identifier

    Returns:
        Tuple of (ars_result, usd_result) dictionaries from get_market_data
    """
    try:
        def _fetch_one(sym: str, settle: str, retries: int = 3, delay_s: float = 0.4) -> dict:
            """
            Try to fetch market data for a symbol using the requested settlement.
            Retries are limited to the same settlement; WS fallback handles gaps.
            """
            target_settle = (settle or "T0").upper()
            last_err: Optional[Exception] = None
            for attempt in range(1, retries + 1):
                try:
                    s = get_market_data(
                        symbol=sym,
                        entries=["BIDS", "OFFERS", "LAST"],
                        depth=1,
                        settlement=target_settle,
                        user_id=user_id,
                    )
                    if not s or not isinstance(s, str) or not s.strip():
                        raise ValueError("Empty market data response")
                    obj = json.loads(s)
                    if isinstance(obj, dict) and (obj.get("success") or obj.get("error")):
                        if obj.get("success"):
                            meta = obj.setdefault("_meta", {})
                            meta["settlement_used"] = target_settle
                        return obj
                    raise ValueError("Unrecognized market data payload")
                except Exception as e:
                    last_err = e
                    logger.debug(
                        "Retry %s/%s for %s %s failed: %s",
                        attempt,
                        retries,
                        sym,
                        target_settle,
                        e,
                    )
                    if attempt < retries:
                        time.sleep(delay_s)
                        continue
                    error_str = str(last_err) if last_err else "unknown error"
                    return {"success": False, "error": f"{sym}@{target_settle}: {error_str}"}

        # Map settlement: CI -> T0, 24hs -> T1
        settlement_mapped = "T0" if settlement.upper() == "CI" else "T1"

        # First try REST for both legs
        ars_result = _fetch_one(bond_symbol, settlement_mapped)
        usd_symbol = f"{bond_symbol}D"
        usd_result = _fetch_one(usd_symbol, settlement_mapped)

        # WS fallback if any leg failed
        if not (ars_result.get("success") and usd_result.get("success")):
            try:
                success, error, session = _require_auth(user_id)
                if not success:
                    return ars_result, usd_result

                # Initialize WS if needed
                if not session_registry.websocket_initialized(user_id):
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

                # Determine full BYMA tickers for subscription
                market_enum, ars_full = MarketHelpers.detect_market_and_ticker(bond_symbol, settlement)
                _, usd_full = MarketHelpers.detect_market_and_ticker(usd_symbol, settlement)

                entries = MarketHelpers.map_market_data_entries(["BIDS", "OFFERS", "LAST"])

                try:
                    pyRofex.market_data_subscription(
                        tickers=[ars_full, usd_full],
                        entries=entries,
                        market=market_enum
                    )
                except Exception:
                    # Subscription failure prevents WS fallback; log for debugging and continue without WS data
                    logger.debug("Market data subscription failed for WS fallback", exc_info=True)
                    return ars_result, usd_result

                wanted = {ars_full.upper(), usd_full.upper()}
                deadline = time.time() + 2.0
                got = {}
                while time.time() < deadline and len(got) < 2:
                    user_quotes = session_registry.list_quotes(user_id)
                    for k, v in user_quotes.items():
                        ku = k.upper()
                        if ku in wanted and ku not in got and isinstance(v, dict):
                            bid = v.get("bid")
                            ask = v.get("ask")
                            last = v.get("last")
                            # Build minimal formatted payload compatible with callers
                            formatted = {
                                "symbol": "",
                                "market": "",
                                "timestamp": v.get("timestamp"),
                                "data": {
                                    "bid": {"price": bid, "size": None} if bid is not None else None,
                                    "offer": {"price": ask, "size": None} if ask is not None else None,
                                    "last": {"price": last, "size": None, "datetime": None} if last is not None else None,
                                },
                            }
                            got[ku] = {"success": True, "symbol": k, "market_data": formatted}
                    if len(got) < 2:
                        time.sleep(0.2)
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
                                pyRofex.market_data_subscription(
                                    tickers=[ars_full, usd_full],
                                    entries=entries,
                                    market=market_enum
                                )
                            except Exception:
                                logger.debug("Re-initializing websocket during fallback failed", exc_info=True)

                # Fill missing legs from WS if available
                if not ars_result.get("success") and ars_full.upper() in got:
                    ars_result = got[ars_full.upper()]
                if not usd_result.get("success") and usd_full.upper() in got:
                    usd_result = got[usd_full.upper()]

            except Exception as _e:
                logger.debug(f"WS fallback skipped: {_e}")

        return ars_result, usd_result

    except Exception as e:
        logger.error(f"Error fetching bond quotes for MEP: {e}")
        raise


def _calculate_mep_via_pyrofex(
    bond_symbol: str,
    settlement: str,
    user_id: str
) -> str:
    """
    Calculate MEP price using pyRofex get_market_data (requires authentication).
    """
    try:
        # Require authentication like normal orders
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})

        session.update_activity()

        # Fetch quotes using get_market_data
        ars_data, usd_data = _fetch_bond_quotes_for_mep(
            bond_symbol, settlement, user_id
        )

        # Check if both requests succeeded
        if not ars_data.get("success"):
            return _safe_json({
                "success": False,
                "error": f"Error obteniendo cotización ARS: {ars_data.get('error', 'Unknown error')}"
            })

        if not usd_data.get("success"):
            return _safe_json({
                "success": False,
                "error": f"Error obteniendo cotización USD: {usd_data.get('error', 'Unknown error')}"
            })

        # Extract bid/ask prices from formatted market data
        ars_market_data = ars_data.get("market_data", {})
        usd_market_data = usd_data.get("market_data", {})

        # Get bid/ask from formatted structure (data.bid/data.offer)
        ars_data_block = ars_market_data.get("data", {})
        usd_data_block = usd_market_data.get("data", {})

        ars_bid = ars_data_block.get("bid", {}).get("price") if ars_data_block.get("bid") else None
        ars_ask = ars_data_block.get("offer", {}).get("price") if ars_data_block.get("offer") else None
        usd_bid = usd_data_block.get("bid", {}).get("price") if usd_data_block.get("bid") else None
        usd_ask = usd_data_block.get("offer", {}).get("price") if usd_data_block.get("offer") else None

        # Store raw prices before normalization (USD bonds are quoted per 100 nominal)
        raw_ars_bid = ars_bid * 100 if ars_bid is not None else None
        raw_ars_ask = ars_ask * 100 if ars_ask is not None else None
        raw_usd_bid = usd_bid * 100 if usd_bid is not None else None
        raw_usd_ask = usd_ask * 100 if usd_ask is not None else None

        # Validate all prices are available
        if not all([ars_bid, ars_ask, usd_bid, usd_ask]):
            return _safe_json({
                "success": False,
                "error": f"Cotizaciones incompletas para MEP {bond_symbol}. Faltan precios bid/ask."
            })

        # Calculate MEP rates (same logic as before)
        mep_buy_rate = round(float(ars_bid) / float(usd_ask), 2)
        mep_sell_rate = round(float(ars_ask) / float(usd_bid), 2)

        # Calculate spread
        spread = round(mep_sell_rate - mep_buy_rate, 2)
        spread_pct = round((spread / mep_buy_rate) * 100, 2)

        return _safe_json({
            "success": True,
            "bond_symbol": bond_symbol,
            "settlement": settlement,
            "mep_rates": {
                "buy_rate": mep_buy_rate,
                "sell_rate": mep_sell_rate,
                "spread": spread,
                "spread_percent": spread_pct
            },
            "underlying_quotes": {
                "ars_bond": {
                    "symbol": bond_symbol.upper(),
                    "bid": float(ars_bid) if ars_bid is not None else None,
                    "ask": float(ars_ask) if ars_ask is not None else None,
                    "raw_bid": float(raw_ars_bid) if raw_ars_bid is not None else None,
                    "raw_ask": float(raw_ars_ask) if raw_ars_ask is not None else None
                },
                "usd_bond": {
                    "symbol": f"{bond_symbol.upper()}D",
                    "bid": float(usd_bid),
                    "ask": float(usd_ask),
                    "raw_bid": float(raw_usd_bid) if raw_usd_bid is not None else None,
                    "raw_ask": float(raw_usd_ask) if raw_usd_ask is not None else None
                }
            },
            "data_source": "pyrofex"
        })

    except Exception as e:
        logger.error(f"pyRofex MEP calculation error: {e}")
        # Re-raise to allow fallback handling
        raise


def _calculate_mep_via_marketdata(
    bond_symbol: str,
    settlement: str,
    user_id: str
) -> str:
    """
    Calculate MEP price using marketdata service (original implementation).
    """
    try:
        import requests

        base = _get_marketdata_base_url().rstrip("/")

        # Map settlement for marketdata service
        settlement_param = "t0" if settlement.upper() == "CI" else "t1"

        # Get both ARS and USD bond quotes
        ars_symbol = bond_symbol.upper()  # e.g., AL30
        usd_symbol = f"{bond_symbol.upper()}D"  # e.g., AL30D

        # Fetch both quotes in parallel
        params_ars = {"symbols": ars_symbol, "settlement": settlement_param}
        params_usd = {"symbols": usd_symbol, "settlement": settlement_param}

        r_ars = requests.get(f"{base}/v1/quotes", params=params_ars, timeout=5)
        r_usd = requests.get(f"{base}/v1/quotes", params=params_usd, timeout=5)

        if r_ars.status_code != 200 or r_usd.status_code != 200:
            return _safe_json({
                "success": False,
                "error": f"No se pudieron obtener cotizaciones para {bond_symbol} MEP"
            })

        ars_data = r_ars.json()
        usd_data = r_usd.json()

        if not isinstance(ars_data, list) or not ars_data:
            return _safe_json({
                "success": False,
                "error": f"No hay datos disponibles para {ars_symbol}"
            })

        if not isinstance(usd_data, list) or not usd_data:
            return _safe_json({
                "success": False,
                "error": f"No hay datos disponibles para {usd_symbol}"
            })

        ars_quote = ars_data[0]
        usd_quote = usd_data[0]

        # Extract bid/ask prices
        ars_bid = None
        ars_ask = None
        usd_bid = None
        usd_ask = None

        if ars_quote.get("bids"):
            ars_bid = ars_quote["bids"][0].get("price")
        if ars_quote.get("offers"):
            ars_ask = ars_quote["offers"][0].get("price")
        if usd_quote.get("bids"):
            usd_bid = usd_quote["bids"][0].get("price")
        if usd_quote.get("offers"):
            usd_ask = usd_quote["offers"][0].get("price")

        if not all([ars_bid, ars_ask, usd_bid, usd_ask]):
            return _safe_json({
                "success": False,
                "error": f"Cotizaciones incompletas para MEP {bond_symbol}. Faltan precios bid/ask."
            })

        # Calculate MEP rates
        mep_buy_rate = round(float(ars_bid) / float(usd_ask), 2)
        mep_sell_rate = round(float(ars_ask) / float(usd_bid), 2)

        # Calculate spread
        spread = round(mep_sell_rate - mep_buy_rate, 2)
        spread_pct = round((spread / mep_buy_rate) * 100, 2)

        return _safe_json({
            "success": True,
            "bond_symbol": bond_symbol,
            "settlement": settlement,
            "mep_rates": {
                "buy_rate": mep_buy_rate,
                "sell_rate": mep_sell_rate,
                "spread": spread,
                "spread_percent": spread_pct
            },
            "underlying_quotes": {
                "ars_bond": {
                    "symbol": ars_symbol,
                    "bid": float(ars_bid),
                    "ask": float(ars_ask)
                },
                "usd_bond": {
                    "symbol": usd_symbol,
                    "bid": float(usd_bid),
                    "ask": float(usd_ask)
                }
            },
            "updated_at": ars_quote.get("updatedAt") or usd_quote.get("updatedAt"),
            "data_source": "marketdata"
        })

    except Exception as e:
        logger.error(f"marketdata MEP calculation error: {e}")
        raise


@mcp.tool()


@mcp.tool()
def get_instruments(
    type: str = "all",
    segment: Optional[str] = None,
    cfi_code: Optional[str] = None,
    market: str = "ROFEX",
    user_id: str = "anonymous"
) -> str:
    """
    Get list of available instruments.
    
    Args:
        type: "all", "by_segment", or "by_cfi"
        segment: Market segment (DDF, MERV) - required for by_segment
        cfi_code: CFI code (STOCK, BOND, CEDEAR) - required for by_cfi  
        market: Market identifier (ROFEX, MERV)
        user_id: User identifier
        
    Returns:
        JSON string with instruments list
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Map parameters to enums
        market_enum = MarketHelpers.map_market_to_enum(market) if market else None
        
        if type == "all":
            result = pyRofex.get_all_instruments()
        elif type == "by_segment" and segment:
            segment_enum = MarketHelpers.map_market_segment_to_enum(segment)
            if not segment_enum or not market_enum:
                return _safe_json({"success": False, "error": f"Invalid segment '{segment}' or market '{market}'"})
            result = pyRofex.get_instruments('by_segments', market=market_enum, market_segment=[segment_enum])
        elif type == "by_cfi" and cfi_code:
            cfi_enum = MarketHelpers.map_cfi_code_to_enum(cfi_code)
            if not cfi_enum:
                return _safe_json({"success": False, "error": f"Invalid CFI code '{cfi_code}'"})
            result = pyRofex.get_instruments('by_cfi', cfi_code=[cfi_enum])
        else:
            return _safe_json({"success": False, "error": "Invalid parameters. For by_segment need 'segment', for by_cfi need 'cfi_code'"})
        
        return _safe_json({
            "success": True,
            "instruments": result.get("instruments", []),
            "count": len(result.get("instruments", []))
        })
        
    except Exception as e:
        logger.error(f"get_instruments error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def marketdata_get_instruments() -> str:
    """
    Get all instruments from the Marketdata Service.

    Returns lightweight instrument list without pyRofex overhead.
    Optimized for instrument resolution and search.

    Returns:
        JSON string with instruments list from marketdata service
    """
    try:
        import requests

        base = _get_marketdata_base_url().rstrip("/")
        r = requests.get(f"{base}/v1/instruments", timeout=10)
        if r.status_code != 200:
            return _safe_json({"success": False, "error": f"Marketdata service {r.status_code}"})

        instruments = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
        if not isinstance(instruments, list):
            instruments = []

        # Return in the same format as pyRofex get_instruments for compatibility
        return _safe_json({
            "success": True,
            "instruments": instruments,
            "count": len(instruments),
            "source": "marketdata_service"
        })

    except Exception as e:
        logger.error(f"marketdata_get_instruments error: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def marketdata_get_quote(
    symbol: str,
    settlement: str = "CI",
    depth: int = 1,
    compact: bool = True,
    user_id: str = "anonymous"
) -> str:
    """
    Get top-of-book quote from the Marketdata Service (compact by default).

    Args:
        symbol: Simple ticker (e.g., "AL30", "AL30D")
        settlement: "CI" or "24hs" (normalized to service params t0/t1)
        depth: Order book depth to include (default 1)
        compact: If True, returns only top-of-book; otherwise returns up to 'depth'

    Returns:
        JSON string with minimal quote fields to avoid context bloat
    """
    try:
        import requests

        base = _get_marketdata_base_url().rstrip("/")
        s_norm = _normalize_mep_settlement_input(settlement)
        service_settlement = "t0" if s_norm.upper() == "CI" else "t1"
        params = {"symbols": symbol, "settlement": service_settlement}
        r = requests.get(f"{base}/v1/quotes", params=params, timeout=5)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            arr = r.json()
            if isinstance(arr, list) and arr:
                q = arr[0]
                top_bid = (q.get("bids") or [{}])[0] if (q.get("bids") and len(q.get("bids")) > 0) else None
                top_ask = (q.get("offers") or [{}])[0] if (q.get("offers") and len(q.get("offers")) > 0) else None

                data = {
                    "symbol": q.get("symbol"),
                    "settlement": q.get("settlement"),
                    "currency": q.get("currency"),
                    "bid": {"price": (top_bid or {}).get("price"), "size": (top_bid or {}).get("size")},
                    "ask": {"price": (top_ask or {}).get("price"), "size": (top_ask or {}).get("size")},
                    "last": (q.get("last") or {}).get("price"),
                    "updatedAt": q.get("updatedAt"),
                    "source": "marketdata_service",
                }

                if not compact and depth and isinstance(depth, int) and depth > 1:
                    data["bids"] = (q.get("bids") or [])[:depth]
                    data["offers"] = (q.get("offers") or [])[:depth]

                return _safe_json({"success": True, "data": data})
            raise ValueError(f"No data for {symbol} {settlement}")
        raise ValueError(f"Marketdata service error ({r.status_code})")

    except Exception as e:
        logger.debug(f"marketdata_get_quote fallback to pyRofex for {symbol} {settlement}: {e}")
        fallback = _fallback_marketdata_via_pyrofex(symbol, settlement, depth, user_id)
        if fallback.get("success"):
            return _safe_json(fallback)
        error_text = fallback.get("error", str(e))
        return _safe_json({
            "success": False,
            "error": (
                f"No se obtuvo la cotización desde el servicio ni pyRofex: {error_text}"
            )
        })



@mcp.tool()
def marketdata_get_orderbook(
    symbol: str,
    settlement: str = "CI",
    depth: int = 5
) -> str:
    """
    Get order book (bids/offers) from the Marketdata Service with bounded depth.
    Defaults to depth=5 to remain compact.
    """
    try:
        import requests

        base = _get_marketdata_base_url().rstrip("/")
        s_norm = _normalize_mep_settlement_input(settlement)
        service_settlement = "t0" if s_norm.upper() == "CI" else "t1"
        params = {"symbols": symbol, "settlement": service_settlement}
        r = requests.get(f"{base}/v1/quotes", params=params, timeout=5)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
            arr = r.json()
            if isinstance(arr, list) and arr:
                q = arr[0]
                data = {
                    "symbol": q.get("symbol"),
                    "settlement": q.get("settlement"),
                    "currency": q.get("currency"),
                    "bids": (q.get("bids") or [])[:depth],
                    "offers": (q.get("offers") or [])[:depth],
                    "last": (q.get("last") or {}).get("price"),
                    "updatedAt": q.get("updatedAt"),
                    "source": "marketdata_service",
                }
                return _safe_json({"success": True, "data": data})
            raise ValueError(f"No data for {symbol} {settlement}")
        raise ValueError(f"Marketdata service error ({r.status_code})")

    except Exception as e:
        logger.debug(f"marketdata_get_orderbook fallback to pyRofex for {symbol} {settlement}: {e}")
        fallback = _fallback_marketdata_via_pyrofex(symbol, settlement, depth, user_id)
        return _safe_json(fallback)



@mcp.tool()
def marketdata_search_instruments(
    query: str,
    limit: int = 20
) -> str:
    """
    Search normalized instruments from the Marketdata Service (client-side filter).

    Returns minimal fields: symbol, settlement, currency, hasMEP, mepPair.
    """
    try:
        import requests

        if not query or len(query.strip()) < 1:
            return _safe_json({"success": False, "error": "query required"})

        base = _get_marketdata_base_url().rstrip("/")
        r = requests.get(f"{base}/v1/instruments", timeout=5)
        if r.status_code != 200:
            return _safe_json({"success": False, "error": f"Marketdata service {r.status_code}"})
        arr = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
        if not isinstance(arr, list):
            arr = []

        q = query.strip().upper()
        matches = []
        for inst in arr:
            sym = (inst.get("symbol") or "").upper()
            if q in sym:
                matches.append({
                    "symbol": inst.get("symbol"),
                    "settlement": inst.get("settlement"),
                    "currency": inst.get("currency"),
                    "hasMEP": inst.get("hasMEP"),
                    "mepPair": inst.get("mepPair"),
                })
            if len(matches) >= limit:
                break

        return _safe_json({"success": True, "count": len(matches), "results": matches})
    except Exception as e:
        return _safe_json({"success": False, "error": str(e)})


@mcp.tool()
def get_market_data(
    symbol: str,
    entries: List[str] = None,
    depth: int = 1,
    market_id: str = None,
    settlement: str = "CI",
    user_id: str = "anonymous"
) -> str:
    """
    Get current market data for an instrument.
    
    Args:
        symbol: Trading symbol (e.g., "DLR/DIC23", "GGAL", "AL30")
        entries: List of data entries ["BIDS", "OFFERS", "LAST", "VOLUME", "HIGH", "LOW", "OPEN", "CLOSE"]
        depth: Market depth for bid/offer (default: 1)
        market_id: Market identifier (ROFEX, MERV) - auto-detected if None
        settlement: Settlement for BYMA instruments: "CI" or "24hs" (legacy "T0"/"T1" accepted)
        user_id: User identifier
        
    Returns:
        JSON string with market data
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Normalize settlement input (support CI/24hs preferred; legacy T0/T1 accepted)
        _settlement_in = settlement
        if isinstance(_settlement_in, str):
            s_norm = _settlement_in.strip().upper()
            if s_norm in ("CI", "T0"):
                settlement_broker = "T0"
            elif s_norm in ("24HS", "T1"):
                settlement_broker = "T1"
            else:
                settlement_broker = "T0"  # default to CI as safer default
        else:
            settlement_broker = "T0"

        # Auto-detect market and full ticker if not provided
        if market_id is None:
            market_enum, full_ticker = MarketHelpers.detect_market_and_ticker(symbol, settlement_broker)
            if not market_enum:
                return _safe_json({"success": False, "error": "Could not determine market for symbol"})
            logger.info(f"Auto-detected market {market_enum} for symbol {symbol} -> {full_ticker}")
        else:
            # Use provided market
            market_enum = MarketHelpers.map_market_to_enum(market_id)
            if not market_enum:
                return _safe_json({"success": False, "error": f"Invalid market '{market_id}'"})
            full_ticker = symbol
        
        # Default entries if none provided
        if entries is None:
            entries = ["BIDS", "OFFERS", "LAST"]
        
        # Map entries to enums
        entry_enums = MarketHelpers.map_market_data_entries(entries)
        if not entry_enums:
            return _safe_json({"success": False, "error": "Invalid market data entries"})
        
        # Get market data with explicit market
        try:
            result = pyRofex.get_market_data(
                ticker=full_ticker,
                entries=entry_enums,
                depth=depth,
                market=market_enum
            )
        except Exception as fetch_err:
            logger.error(
                f"pyRofex.get_market_data failed for {full_ticker} "
                f"(market={market_enum.value if hasattr(market_enum, 'value') else market_enum}, "
                f"entries={[e.value if hasattr(e, 'value') else str(e) for e in entry_enums]}, "
                f"depth={depth}): {fetch_err}"
            )
            raise

        # Log raw response for debugging (truncate if too long)
        log_result = str(result)[:500] + "..." if len(str(result)) > 500 else result
        logger.info(f"Raw pyRofex.get_market_data response for {symbol} (market={market_enum}): {log_result}")
        
        # Validate response before formatting
        if result is None:
            logger.error(f"pyRofex.get_market_data returned None for symbol {symbol} on market {market_enum}")
            return _safe_json({
                "success": False,
                "error": f"No hay datos de mercado disponibles para {symbol}",
                "symbol": symbol,
                "market": str(market_enum) if market_enum else "unknown"
            })
        
        if not isinstance(result, dict):
            logger.error(f"pyRofex.get_market_data returned invalid type {type(result)} for {symbol}: {result}")
            return _safe_json({
                "success": False,
                "error": f"Respuesta inválida del mercado para {symbol}",
                "symbol": symbol,
                "market": str(market_enum) if market_enum else "unknown"
            })
        
        # Check if response is an error (has 'status' key instead of 'marketData')
        if "status" in result and "marketData" not in result:
            logger.warning(f"API returned error for ticker {full_ticker}: {result}")
            error_msg = result.get("message", "Error desconocido")
            
            # Try fallback ticker formats for BYMA instruments
            if not "/" in symbol and market_enum == pyRofex.Market.ROFEX:  # Not a future
                logger.info(f"Trying fallback ticker formats for {symbol}")
                
                # Try alternative ticker formats
                fallback_tickers = [
                    symbol,  # Just "AL30"
                    f"{symbol} - 24hs",  # "AL30 - 24hs"
                    f"{symbol} - CI",    # "AL30 - CI" for T0
                ]
                
                for fallback_ticker in fallback_tickers:
                    if fallback_ticker == full_ticker:  # Skip the one we already tried
                        continue
                    
                    logger.info(f"Trying fallback ticker: {fallback_ticker}")
                    try:
                        fallback_result = pyRofex.get_market_data(
                            ticker=fallback_ticker,
                            entries=entry_enums,
                            depth=depth,
                            market=market_enum
                        )
                        
                        if fallback_result and isinstance(fallback_result, dict) and "marketData" in fallback_result:
                            logger.info("Fallback ticker %s worked for %s", fallback_ticker, symbol)
                            result = fallback_result
                            full_ticker = fallback_ticker  # Update for logging
                            break
                    except Exception as e:
                        logger.debug(f"Fallback ticker {fallback_ticker} failed: {e}")
                        continue
                else:
                    # None of the fallback tickers worked
                    logger.error(f"All ticker formats failed for {symbol}. Original error: {error_msg}")
                    return _safe_json({
                        "success": False,
                        "error": f"No se encontraron datos de mercado para {symbol}. Error: {error_msg}",
                        "symbol": symbol,
                        "market": str(market_enum) if market_enum else "unknown"
                    })
        
        # Format response (result should have marketData at this point)
        formatted = MarketHelpers.format_market_data_response(result)
        try:
            # Apply display normalization for bonds (divide by 100)
            sym_for_norm = formatted.get("symbol") or symbol
            MarketHelpers.normalize_quote_block_for_display(sym_for_norm, formatted.get("data", {}))
        except Exception as _e:
            logger.debug(f"Display normalization skipped: {_e}")
        
        # If formatting failed, provide Spanish error with context
        if "error" in formatted:
            logger.error(f"Market data formatting failed for {symbol} on {market_enum}: {formatted['error']}")
            available_keys = list(result.keys()) if isinstance(result, dict) else []
            return _safe_json({
                "success": False,
                "error": f"No se pudieron procesar los datos de mercado para {symbol}. Claves disponibles: {available_keys}",
                "symbol": symbol,
                "market": str(market_enum) if market_enum else "unknown",
                "raw_keys": available_keys
            })
        
        return _safe_json({
            "success": True,
            "symbol": symbol,
            "market": str(market_enum) if market_enum else "unknown",
            "market_data": formatted
        })
        
    except Exception as e:
        logger.error(f"get_market_data error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def search_instruments(
    query: str,
    limit: int = 20,
    user_id: str = "anonymous"
) -> str:
    """
    Search instruments by symbol or description.
    
    Args:
        query: Search query (symbol or text)
        limit: Maximum number of results
        user_id: User identifier
        
    Returns:
        JSON string with search results
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        if not query or len(query.strip()) < 2:
            return _safe_json({"success": False, "error": "Query must be at least 2 characters"})
        
        # Get all instruments for searching
        all_instruments = pyRofex.get_detailed_instruments()
        instruments = all_instruments.get("instruments", [])
        
        # Search logic
        query_upper = query.upper().strip()
        results = []
        
        for instrument in instruments:
            symbol = instrument.get("instrumentId", {}).get("symbol", "")
            description = instrument.get("description", "")
            
            # Score matches
            score = 0
            if symbol and query_upper in symbol.upper():
                score += 10 if symbol.upper().startswith(query_upper) else 5
            if description and query_upper in description.upper():
                score += 3
            
            if score > 0:
                results.append({
                    "symbol": symbol,
                    "description": description,
                    "market": instrument.get("instrumentId", {}).get("marketId"),
                    "segment": instrument.get("instrumentId", {}).get("segment"),
                    "cfi_code": instrument.get("cfiCode"),
                    "score": score
                })
        
        # Sort by score and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:limit]
        
        return _safe_json({
            "success": True,
            "query": query,
            "results": results,
            "count": len(results)
        })
        
    except Exception as e:
        logger.error(f"search_instruments error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_instrument_details(
    symbol: str,
    market_id: str = "ROFEX",
    user_id: str = "anonymous"
) -> str:
    """
    Get detailed information for a specific instrument.
    
    Args:
        symbol: Trading symbol
        market_id: Market identifier (ROFEX, MERV)
        user_id: User identifier
        
    Returns:
        JSON string with instrument details
    """
    try:
        success, error, session = _require_auth(user_id)
        if not success:
            return _safe_json({"success": False, "error": error})
        
        session.update_activity()
        
        # Validate symbol
        if not MarketHelpers.validate_symbol(symbol):
            return _safe_json({"success": False, "error": f"Invalid symbol format: {symbol}"})
        
        # Map market
        market_enum = MarketHelpers.map_market_to_enum(market_id)
        if not market_enum:
            return _safe_json({"success": False, "error": f"Invalid market '{market_id}'"})
        
        # Get instrument details
        result = pyRofex.get_instrument_details(ticker=symbol, market=market_enum)
        
        return _safe_json({
            "success": True,
            "symbol": symbol,
            "details": result
        })
        
    except Exception as e:
        logger.error(f"get_instrument_details error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})



@mcp.tool()
def get_segments() -> str:
    """
    Get list of available market segments.
    
    Returns:
        JSON string with segments list
    """
    try:
        if not PYROFEX_AVAILABLE:
            return _safe_json({"success": False, "error": "pyRofex not available"})
        
        result = pyRofex.get_segments()
        
        return _safe_json({
            "success": True,
            "segments": result
        })
        
    except Exception as e:
        logger.error(f"get_segments error: {e}")
        return _safe_json({"success": False, "error": str(e)})
