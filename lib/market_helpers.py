#!/usr/bin/env python3
"""
Market Helpers - Utility functions for ROFEX market operations.

Provides symbol mapping, market segment handling, and parameter validation.
"""

import os
import sys
import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

# Add pyRofex to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROFEX_SRC = os.path.abspath(os.path.join(REPO_ROOT, "pyRofex-master", "src"))
if PYROFEX_SRC not in sys.path:
    sys.path.insert(0, PYROFEX_SRC)

try:
    import pyRofex
    from pyRofex.components.enums import (
        Environment, Side, OrderType, TimeInForce, 
        MarketDataEntry, Market, MarketSegment, CFICode
    )
    PYROFEX_AVAILABLE = True
except ImportError as e:
    logging.error(f"pyRofex not available: {e}")
    PYROFEX_AVAILABLE = False

logger = logging.getLogger(__name__)


class MarketHelpers:
    """Utility functions for market operations and data transformation."""
    
    # ---------------------------------------------------------------------
    # Canonicalization aliases (user-friendly → canonical broker symbol)
    # ---------------------------------------------------------------------
    _ALIAS_MAP: Dict[str, str] = {
        # Common Argentine stock confusions
        "YPF": "YPFD",
        "PAMPA": "PAMP",
        "BBVA": "BBAR",
        "BANCO_MACRO": "BMA",
        "TELECOM": "TECO2",
        "TELEFONICA": "TEF",
        "TENARIS": "TS",
        "GALICIA": "GGAL",
        "FRANCES": "BFRA",
        "SUPERVIELLE": "SUPV",
        "GRUPO_FINANCIERO": "GGAL",
        "ALUAR": "ALUA",
        "CENTRAL_PUERTO": "CEPU",
        "EDENOR": "EDN",
        "TRANSENER": "TRAN",
        "TRANSPORTADORA": "TGS",
        "CRESUD": "CRES",
        "IRSA": "IRS",
        "MIRGOR": "MIRG",
        "MOLINOS": "MOLI",
    }
    
    # ---------------------------------------------------------------------
    # Bond instrument cache (for price normalization decisions)
    # ---------------------------------------------------------------------
    _bond_cache: Dict[str, Any] = {
        "root_symbols": set(),      # e.g., {"AL30", "GD30"}
        "full_tickers": set(),      # e.g., {"MERV - XMEV - AL30 - 24hs", ...}
        "symbol_variants": set(),   # e.g., {"AL30", "AL30D"}
        "updated_at": 0.0
    }
    BOND_CACHE_TTL_SECONDS: int = int(os.getenv("BOND_CACHE_TTL_SECONDS", "43200"))  # 12h
    # Fallback common bond roots if remote fetch is unavailable
    _BOND_FALLBACK_ROOTS: set = set((os.getenv("BOND_FALLBACK_ROOTS") or "AL30,AL35,GD30,GD35,AE38,AL41,GD41").upper().split(","))

    @staticmethod
    def _extract_root_symbol(symbol: str) -> str:
        """
        Extract root symbol from full BYMA ticker if applicable.
        Example: "MERV - XMEV - AL30 - 24hs" -> "AL30".
        """
        if not symbol:
            return symbol
        s = symbol.strip().upper()
        # Typical format: MERV - XMEV - <ROOT> - <SETTLEMENT>
        parts = [p.strip() for p in s.split(" - ")]
        if len(parts) >= 4 and parts[0] == "MERV" and parts[1] == "XMEV":
            return parts[2]
        # Also support simpler format: <ROOT> - <SETTLEMENT>
        if len(parts) == 2 and parts[1] in ("24HS", "48HS", "CI", "T0", "T1"):
            return parts[0]
        return s

    @staticmethod
    def _refresh_bond_cache_if_needed() -> None:
        if not PYROFEX_AVAILABLE:
            return
        now = time.time()
        cache = MarketHelpers._bond_cache
        # Throttle refreshes regardless of whether previous fetch returned data
        if cache.get("updated_at", 0) and (now - cache["updated_at"]) < MarketHelpers.BOND_CACHE_TTL_SECONDS:
            return
        try:
            result = pyRofex.get_instruments('by_cfi', cfi_code=[CFICode.BOND])
            instruments = result.get("instruments", []) if isinstance(result, dict) else []
            root_set = set()
            full_set = set()
            variant_set = set()
            for inst in instruments:
                instrument_id = inst.get("instrumentId", {}) if isinstance(inst, dict) else {}
                sym = instrument_id.get("symbol")
                if not sym:
                    continue
                s_upper = str(sym).upper()
                full_set.add(s_upper)
                root = MarketHelpers._extract_root_symbol(s_upper)
                root_set.add(root)
                variant_set.add(s_upper)
                variant_set.add(root)
                # Precompute common currency suffix variants so AL30D, AL30C etc. are recognized
                for suffix in ("D", "C", "N", "L"):
                    variant_set.add(f"{root}{suffix}")

            # If remote delivers no bond instruments, fall back to known common roots
            if not root_set:
                fallback_roots = MarketHelpers._BOND_FALLBACK_ROOTS
                for root in fallback_roots:
                    root_set.add(root)
                    variant_set.add(root)
                    for suffix in ("D", "C", "N", "L"):
                        variant_set.add(f"{root}{suffix}")
                full_set = set()
                logger.debug("Bond cache using fallback roots (remote empty)")

            cache["root_symbols"] = root_set
            cache["full_tickers"] = full_set
            cache["symbol_variants"] = variant_set
            cache["updated_at"] = now
            logger.info(f"Bond cache built: roots={len(root_set)}, full_tickers={len(full_set)}")
        except Exception as e:
            # Avoid tight loop on repeated failures
            cache["updated_at"] = now
            logger.warning(f"Failed to refresh bond cache: {e}")

    @staticmethod
    def is_bond_symbol(symbol: str) -> bool:
        """
        Determine if the provided symbol/ticker is a bond (sovereign or corporate).
        Uses a cached instrument list fetched via pyRofex CFI code BOND.
        """
        if not symbol:
            return False
        MarketHelpers._refresh_bond_cache_if_needed()
        s_upper = symbol.strip().upper()
        root = MarketHelpers._extract_root_symbol(s_upper)

        candidates = {s_upper, root}

        # If symbol carries a currency suffix (e.g., AL30D), include stripped variant
        for suffix in ("D", "C", "N", "L"):
            if s_upper.endswith(suffix) and len(s_upper) > len(suffix):
                candidates.add(s_upper[:-len(suffix)])
                if root.endswith(suffix):
                    candidates.add(root[:-len(suffix)])

        normalized_candidates = set()
        for cand in candidates:
            if not cand:
                continue
            normalized_candidates.add(cand)
            normalized_candidates.add(MarketHelpers._extract_root_symbol(cand))

        # Include derived variants (root + currency suffixes) for completeness
        derived = set()
        for cand in normalized_candidates:
            if not cand:
                continue
            for suffix in ("D", "C", "N", "L"):
                derived.add(f"{cand}{suffix}")
        candidates |= normalized_candidates | derived

        cache = MarketHelpers._bond_cache
        symbol_variants = cache.get("symbol_variants", set())
        if any(candidate in symbol_variants for candidate in candidates):
            return True

        if any(candidate in cache["full_tickers"] for candidate in candidates):
            return True

        if any(candidate in cache["root_symbols"] for candidate in candidates):
            return True

        # Fallback on common bond roots when cache is empty or refresh failed
        fallback_roots = MarketHelpers._BOND_FALLBACK_ROOTS
        if not cache["root_symbols"] and not cache["full_tickers"]:
            return any(candidate in fallback_roots for candidate in candidates)

        # If cache exists but symbol not found, rely on fallback as last resort
        if any(candidate in fallback_roots for candidate in candidates):
            return True

        return False

    @staticmethod
    def normalize_price_for_display(symbol: str, price: Optional[float]) -> Optional[float]:
        """
        Convert broker-unit price to user display price.
        For bonds: divide by 100; others unchanged.
        """
        if price is None:
            return None
        if MarketHelpers.is_bond_symbol(symbol):
            try:
                return round(float(price) / 100.0, 6)
            except Exception:
                return price
        return price

    @staticmethod
    def normalize_price_for_broker(symbol: str, price: Optional[float]) -> Optional[float]:
        """
        Convert user display price to broker units.
        For bonds: multiply by 100; others unchanged.
        """
        if price is None:
            return None
        if MarketHelpers.is_bond_symbol(symbol):
            try:
                return round(float(price) * 100.0, 6)
            except Exception:
                return price
        return price

    @staticmethod
    def normalize_quote_block_for_display(symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply display normalization to a formatted market data 'data' block.
        Mutates and returns the same dict.
        """
        if not isinstance(data, dict):
            return data
        if not MarketHelpers.is_bond_symbol(symbol):
            return data

        def _div100(v: Any) -> Any:
            if v is None:
                return None
            try:
                return round(float(v) / 100.0, 6)
            except Exception:
                return v

        if "bid" in data and isinstance(data["bid"], dict):
            data["bid"]["price"] = _div100(data["bid"].get("price"))
        if "offer" in data and isinstance(data["offer"], dict):
            data["offer"]["price"] = _div100(data["offer"].get("price"))
        if "last" in data and isinstance(data["last"], dict):
            data["last"]["price"] = _div100(data["last"].get("price"))
        for key in ("open", "close", "high", "low"):
            if key in data:
                data[key] = _div100(data.get(key))
        return data

    @staticmethod
    def validate_symbol(symbol: str) -> bool:
        """
        Validate ROFEX symbol format.
        
        Args:
            symbol: Trading symbol (e.g., "DLR/DIC23", "GGAL")
            
        Returns:
            bool: True if valid format
        """
        if not symbol or not isinstance(symbol, str):
            return False
        
        # Basic validation - allow alphanumeric, /, -, and common suffixes
        import re
        pattern = r'^[A-Z0-9]+(/[A-Z]{3}\d{2})?(-\w+)?$'
        return bool(re.match(pattern, symbol.upper()))
    
    @staticmethod
    def canonicalize_symbol(symbol: str) -> str:
        """
        Map user-friendly or colloquial references to canonical broker symbols.
        
        Examples:
            "YPF" -> "YPFD"
            "PAMPA" -> "PAMP"
            "YPF 24hs" -> "YPFD"
            "MERV - XMEV - YPF - 24hs" -> "YPFD"
        
        This function is intentionally conservative to avoid changing valid
        symbols inadvertently. It only applies aliasing on simple root tokens
        or clear BYMA formatted tickers. Futures (with "/") are left unchanged.
        """
        if not symbol or not isinstance(symbol, str):
            return symbol
        
        s_upper = symbol.strip().upper()
        
        # Do not touch futures or explicit derivatives
        if "/" in s_upper:
            return s_upper
        
        base = s_upper
        
        # Handle BYMA formatted full tickers: MERV - XMEV - <ROOT> - <TERM>
        parts = [p.strip() for p in base.split(" - ")]
        if len(parts) >= 4 and parts[0] == "MERV" and parts[1] == "XMEV":
            base = parts[2]
        else:
            # Strip common settlement suffixes if present at the end
            for suffix in (" 24HS", " 48HS", " CI", " T0", " T1"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
        
        # Some inputs may come with underscores instead of spaces
        base_alt = base.replace(" ", "_")
        
        mapped = MarketHelpers._ALIAS_MAP.get(base) or MarketHelpers._ALIAS_MAP.get(base_alt)
        if mapped and mapped != base:
            try:
                logger.info(f"Canonicalized symbol '{symbol}' → '{mapped}'")
            except Exception:
                pass
            return mapped
        
        return s_upper
    
    @staticmethod
    def map_side_to_enum(side: str) -> Optional['Side']:
        """
        Map string side to pyRofex Side enum.
        
        Args:
            side: "BUY" or "SELL"
            
        Returns:
            Side enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        side_upper = side.upper()
        if side_upper == "BUY":
            return Side.BUY
        elif side_upper == "SELL":
            return Side.SELL
        else:
            return None
    
    @staticmethod
    def map_order_type_to_enum(order_type: str) -> Optional['OrderType']:
        """
        Map string order type to pyRofex OrderType enum.
        
        Args:
            order_type: "MARKET", "LIMIT", etc.
            
        Returns:
            OrderType enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        type_upper = order_type.upper()
        if type_upper == "MARKET":
            return OrderType.MARKET
        elif type_upper == "LIMIT":
            return OrderType.LIMIT
        else:
            return None
    
    @staticmethod
    def map_time_in_force_to_enum(tif: str) -> Optional['TimeInForce']:
        """
        Map string time in force to pyRofex TimeInForce enum.
        
        Args:
            tif: "DAY", "IOC", "FOK", "GTD"
            
        Returns:
            TimeInForce enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        tif_upper = tif.upper()
        if tif_upper == "DAY":
            return TimeInForce.DAY
        elif tif_upper == "IOC":
            return TimeInForce.IOC
        elif tif_upper == "FOK":
            return TimeInForce.FOK
        elif tif_upper == "GTD":
            return TimeInForce.GTD
        else:
            return None
    
    @staticmethod
    def map_market_data_entries(entries: List[str]) -> List['MarketDataEntry']:
        """
        Map string entries to MarketDataEntry enums.
        
        Args:
            entries: List of entry types like ["BIDS", "OFFERS", "LAST"]
            
        Returns:
            List of MarketDataEntry enums
        """
        if not PYROFEX_AVAILABLE:
            return []
        
        mapped = []
        for entry in entries:
            entry_upper = entry.upper()
            if entry_upper == "BIDS" or entry_upper == "BID":
                mapped.append(MarketDataEntry.BIDS)
            elif entry_upper == "OFFERS" or entry_upper == "OFFER" or entry_upper == "ASK":
                mapped.append(MarketDataEntry.OFFERS)
            elif entry_upper == "LAST" or entry_upper == "TRADE":
                mapped.append(MarketDataEntry.LAST)
            elif entry_upper == "VOLUME":
                mapped.append(MarketDataEntry.VOLUME)
            elif entry_upper == "HIGH":
                mapped.append(MarketDataEntry.HIGH)
            elif entry_upper == "LOW":
                mapped.append(MarketDataEntry.LOW)
            elif entry_upper == "OPEN":
                mapped.append(MarketDataEntry.OPEN)
            elif entry_upper == "CLOSE":
                mapped.append(MarketDataEntry.CLOSE)
        
        return mapped
    
    @staticmethod
    def map_market_to_enum(market: str) -> Optional['Market']:
        """
        Map string market to Market enum.
        
        Args:
            market: "ROFEX", "MERV", etc.
            
        Returns:
            Market enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        market_upper = market.upper()
        if market_upper == "ROFEX":
            return Market.ROFEX
        elif market_upper == "MERV":
            return Market.ROFEX  # MERV instruments use Market.ROFEX
        else:
            return None
    
    @staticmethod
    def map_market_segment_to_enum(segment: str) -> Optional['MarketSegment']:
        """
        Map string market segment to MarketSegment enum.
        
        Args:
            segment: "DDF", "MERV", etc.
            
        Returns:
            MarketSegment enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        segment_upper = segment.upper()
        if segment_upper == "DDF":
            return MarketSegment.DDF
        elif segment_upper == "MERV":
            return MarketSegment.MERV
        else:
            return None
    
    @staticmethod
    def map_cfi_code_to_enum(cfi_code: str) -> Optional['CFICode']:
        """
        Map string CFI code to CFICode enum.
        
        Args:
            cfi_code: "STOCK", "BOND", "CEDEAR", etc.
            
        Returns:
            CFICode enum or None if invalid
        """
        if not PYROFEX_AVAILABLE:
            return None
        
        cfi_upper = cfi_code.upper()
        if cfi_upper == "STOCK":
            return CFICode.STOCK
        elif cfi_upper == "BOND":
            return CFICode.BOND
        elif cfi_upper == "CEDEAR":
            return CFICode.CEDEAR
        else:
            return None
    
    @staticmethod
    def format_market_data_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format pyRofex market data response for consistent output.
        
        Args:
            response: Raw pyRofex market data response
            
        Returns:
            Formatted response dict
        """
        if response is None:
            return {"error": "Market data response is None"}
        
        if not isinstance(response, dict):
            return {"error": f"Market data response is not a dictionary, got: {type(response)}"}
        
        if "marketData" not in response:
            available_keys = list(response.keys()) if response else []
            return {"error": f"No 'marketData' key in response. Available keys: {available_keys}"}
        
        market_data = response["marketData"]
        formatted = {
            "symbol": response.get("instrumentId", {}).get("symbol", ""),
            "market": response.get("instrumentId", {}).get("marketId", ""),
            "timestamp": response.get("timestamp"),
            "data": {}
        }
        
        # Extract common fields - handle None values gracefully
        if "BI" in market_data and market_data["BI"] and len(market_data["BI"]) > 0:
            formatted["data"]["bid"] = {
                "price": market_data["BI"][0].get("price"),
                "size": market_data["BI"][0].get("size")
            }

        if "OF" in market_data and market_data["OF"] and len(market_data["OF"]) > 0:
            formatted["data"]["offer"] = {
                "price": market_data["OF"][0].get("price"),
                "size": market_data["OF"][0].get("size")
            }

        if "LA" in market_data and market_data["LA"]:
            formatted["data"]["last"] = {
                "price": market_data["LA"].get("price"),
                "size": market_data["LA"].get("size"),
                "datetime": market_data["LA"].get("datetime")
            }
        
        if "OP" in market_data:
            formatted["data"]["open"] = market_data["OP"].get("price")
        
        if "CL" in market_data:
            formatted["data"]["close"] = market_data["CL"].get("price")
        
        if "HI" in market_data:
            formatted["data"]["high"] = market_data["HI"].get("price")
        
        if "LO" in market_data:
            formatted["data"]["low"] = market_data["LO"].get("price")
        
        if "VU" in market_data:
            formatted["data"]["volume"] = market_data["VU"].get("size")
        
        return formatted
    
    @staticmethod
    def format_order_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format pyRofex order response for consistent output.
        
        Args:
            response: Raw pyRofex order response
            
        Returns:
            Formatted response dict
        """
        if response is None:
            return {"error": "Order response is None"}
        
        if not isinstance(response, dict):
            return {"error": f"Order response is not a dictionary, got: {type(response)}"}
        
        if "order" not in response:
            available_keys = list(response.keys()) if response else []
            return {"error": f"No 'order' key in response. Available keys: {available_keys}"}
        
        order = response["order"]
        symbol_from_resp = (order.get("instrumentId", {}) or {}).get("symbol")
        raw_price = order.get("price")
        normalized_price = MarketHelpers.normalize_price_for_display(symbol_from_resp or "", raw_price)
        return {
            "order_id": order.get("clientId"),
            "proprietary": order.get("proprietary"),
            "symbol": symbol_from_resp,
            "market": order.get("instrumentId", {}).get("marketId"),
            "side": order.get("side"),
            "type": order.get("type"),
            "quantity": order.get("orderQty"),
            "price": normalized_price,
            "status": order.get("status"),
            "timestamp": order.get("transactTime"),
            "text": order.get("text", "")
        }
    
    @staticmethod
    def validate_order_parameters(
        symbol: str,
        side: str,
        size: Optional[int] = None,
        price: Optional[float] = None,
        order_type: str = "LIMIT"
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate order parameters before submission.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            size: Order size
            price: Order price (required for LIMIT)
            order_type: Order type
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate symbol
        if not MarketHelpers.validate_symbol(symbol):
            return False, f"Invalid symbol format: {symbol}"
        
        # Validate side
        if not MarketHelpers.map_side_to_enum(side):
            return False, f"Invalid side: {side}. Must be BUY or SELL"
        
        # Validate size
        if size is not None and (not isinstance(size, (int, float)) or size <= 0):
            return False, f"Invalid size: {size}. Must be positive number"
        
        # Validate price for LIMIT orders
        if order_type.upper() == "LIMIT":
            if price is None or price <= 0:
                return False, "Price is required for LIMIT orders"
        
        # Validate order type
        if not MarketHelpers.map_order_type_to_enum(order_type):
            return False, f"Invalid order type: {order_type}"
        
        return True, None
    
    @staticmethod
    def detect_market_and_ticker(symbol: str, settlement: str = "24hs") -> Tuple[Optional['Market'], str]:
        """
        Auto-detect market and construct full ticker for BYMA instruments.
        
        Simple rules:
        - If symbol contains "/" → ROFEX future (unchanged)
        - Otherwise → BYMA instrument with MERV ticker format
        
        Args:
            symbol: Trading symbol (e.g., "AL30", "GGAL", "DLR/DIC23")
            settlement: Settlement cycle for BYMA input ("CI" or "24hs").
                        Also accepts legacy "T0"/"T1" and normalizes: T0→CI, T1→24hs.
            
        Returns:
            Tuple of (Market enum, full_ticker_string)
        """
        if not PYROFEX_AVAILABLE:
            return None, symbol
        
        # Canonicalize first (handles aliases like YPF → YPFD)
        symbol = MarketHelpers.canonicalize_symbol(symbol)
        
        # Simple rule: if it contains "/" it's a future (ROFEX)
        if "/" in symbol:
            return Market.ROFEX, symbol
        
        # Everything else is BYMA → use MERV ticker format
        s = (settlement or "").strip().upper()
        if s in ("CI", "T0"):
            settlement_suffix = "CI"
        else:
            settlement_suffix = "24hs"
        full_ticker = f"MERV - XMEV - {symbol} - {settlement_suffix}"
        return Market.ROFEX, full_ticker  # Always Market.ROFEX!
    
    @staticmethod
    def get_supported_instruments() -> List[str]:
        """
        Get list of commonly traded instruments.
        
        Returns:
            List of symbol examples
        """
        return [
            # Major stocks
            "GGAL", "YPFD", "TXAR", "BMA", "SUPV", "CRES", "MIRG", "LOMA",

            # Bonds (examples)
            "AL30", "AL35", "GD30", "GD35", "AE38", "AL41", "GD41",

            # Dollar futures (examples)
            "DLR/DIC23", "DLR/ENE24", "DLR/FEB24",

            # CEDEARs (examples)
            "KO", "AAPL", "GOOGL", "MSFT", "TSLA", "NVDA", "AMZN"
        ]

    # =============================================================================
    # MEP DOLLAR TRADING HELPERS
    # =============================================================================

    @staticmethod
    def get_mep_bond_pairs() -> Dict[str, str]:
        """
        Get available MEP bond pairs (ARS/USD).

        Returns:
            Dictionary mapping ARS bond symbol to its USD counterpart
        """
        return {
            "AL30": "AL30D",
            "GD30": "GD30D",
            "AE38": "AE38D",
            "AL35": "AL35D",
            "GD35": "GD35D",
            "AL41": "AL41D",
            "GD41": "GD41D",
            "DICP": "DICPD",
            "CUAP": "CUAPD",
        }

    @staticmethod
    def is_mep_eligible_bond(symbol: str) -> bool:
        """
        Check if a bond symbol can be used for MEP operations.

        Args:
            symbol: Bond symbol (e.g., "AL30", "AL30D")

        Returns:
            True if bond can be used for MEP
        """
        mep_pairs = MarketHelpers.get_mep_bond_pairs()
        base_symbol = symbol.rstrip('D')  # Remove 'D' suffix if present
        return base_symbol in mep_pairs

    @staticmethod
    def get_mep_counterpart(symbol: str) -> Optional[str]:
        """
        Get the counterpart bond for MEP operations.

        Args:
            symbol: Bond symbol (e.g., "AL30" or "AL30D")

        Returns:
            Counterpart symbol (e.g., "AL30D" for "AL30", "AL30" for "AL30D")
        """
        mep_pairs = MarketHelpers.get_mep_bond_pairs()

        if symbol.endswith('D'):
            # USD bond -> ARS bond
            base_symbol = symbol[:-1]  # Remove 'D'
            return base_symbol if base_symbol in mep_pairs else None
        else:
            # ARS bond -> USD bond
            return mep_pairs.get(symbol)

    @staticmethod
    def detect_mep_operation(orders: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Detect if a list of orders represents a MEP operation.

        Args:
            orders: List of order dictionaries with 'symbol' and 'side' keys

        Returns:
            MEP operation details if detected, None otherwise
        """
        if len(orders) != 2:
            return None

        symbols = [order.get('symbol', '') for order in orders]
        sides = [order.get('side', '') for order in orders]

        # Check if we have a bond pair
        ars_symbols = [s for s in symbols if not s.endswith('D')]
        usd_symbols = [s for s in symbols if s.endswith('D')]

        if len(ars_symbols) != 1 or len(usd_symbols) != 1:
            return None

        ars_bond = ars_symbols[0]
        usd_bond = usd_symbols[0]

        # Verify it's a valid MEP pair
        if MarketHelpers.get_mep_counterpart(ars_bond) != usd_bond:
            return None

        # Find the orders for each bond
        ars_order = next((o for o in orders if o.get('symbol') == ars_bond), None)
        usd_order = next((o for o in orders if o.get('symbol') == usd_bond), None)

        if not ars_order or not usd_order:
            return None

        # Determine operation type based on USD bond side
        is_buying_usd = usd_order.get('side', '').upper() == 'BUY'
        operation_type = 'MEP_BUY' if is_buying_usd else 'MEP_SELL'

        # Validate order consistency
        expected_ars_side = 'SELL' if is_buying_usd else 'BUY'
        if ars_order.get('side', '').upper() != expected_ars_side:
            return None

        return {
            'operation_type': operation_type,
            'base_bond': ars_bond,
            'usd_bond': usd_bond,
            'ars_order': ars_order,
            'usd_order': usd_order,
            'is_buying_usd': is_buying_usd,
            'usd_amount': usd_order.get('size', 0) * usd_order.get('price', 0)
        }

    @staticmethod
    def validate_mep_order_pair(ars_order: Dict[str, Any], usd_order: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate that two orders form a valid MEP pair.

        Args:
            ars_order: ARS bond order dictionary
            usd_order: USD bond order dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check symbols
            ars_symbol = ars_order.get('symbol', '')
            usd_symbol = usd_order.get('symbol', '')

            if not MarketHelpers.is_mep_eligible_bond(ars_symbol):
                return False, f"Bond {ars_symbol} is not MEP eligible"

            expected_usd = MarketHelpers.get_mep_counterpart(ars_symbol)
            if usd_symbol != expected_usd:
                return False, f"Invalid MEP pair: {ars_symbol} should pair with {expected_usd}, not {usd_symbol}"

            # Check quantities match
            ars_size = ars_order.get('size', 0)
            usd_size = usd_order.get('size', 0)

            if ars_size != usd_size:
                return False, f"MEP order sizes must match: ARS={ars_size}, USD={usd_size}"

            # Check sides are opposite
            ars_side = ars_order.get('side', '').upper()
            usd_side = usd_order.get('side', '').upper()

            valid_combinations = [('BUY', 'SELL'), ('SELL', 'BUY')]
            if (ars_side, usd_side) not in valid_combinations:
                return False, f"Invalid MEP side combination: ARS={ars_side}, USD={usd_side}"

            # Check settlement (should be same)
            ars_settlement = ars_order.get('settlement', 'T1')
            usd_settlement = usd_order.get('settlement', 'T1')

            if ars_settlement != usd_settlement:
                return False, f"MEP orders must have same settlement: ARS={ars_settlement}, USD={usd_settlement}"

            return True, "Valid MEP order pair"

        except Exception as e:
            return False, f"Error validating MEP pair: {str(e)}"

    @staticmethod
    def get_recommended_mep_bonds() -> List[Dict[str, Any]]:
        """
        Get list of recommended bonds for MEP operations with metadata.

        Returns:
            List of bond information dictionaries
        """
        return [
            {
                'symbol': 'AL30',
                'usd_symbol': 'AL30D',
                'name': 'BODEN 2030',
                'currency': 'USD',
                'maturity': '2030-07-09',
                'liquidity': 'high',
                'recommended': True,
                'description': 'Bono más líquido para operaciones MEP'
            },
            {
                'symbol': 'GD30',
                'usd_symbol': 'GD30D',
                'name': 'GLOBALES 2030',
                'currency': 'USD',
                'maturity': '2030-07-09',
                'liquidity': 'high',
                'recommended': True,
                'description': 'Alternativa líquida al AL30'
            },
            {
                'symbol': 'AE38',
                'usd_symbol': 'AE38D',
                'name': 'BONARES 2038',
                'currency': 'EUR',
                'maturity': '2038-01-09',
                'liquidity': 'medium',
                'recommended': False,
                'description': 'Bono en euros, menor liquidez'
            }
        ]
