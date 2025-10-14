"""Utilidades para gestionar sesiones y estado en memoria del MCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .pyrofex_session import PyRofexSession


@dataclass
class SessionRegistry:
    """Almacén en memoria para sesiones y estado WebSocket por usuario."""

    _sessions: Dict[str, PyRofexSession] = field(default_factory=dict)
    _quotes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _connections: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Sesiones
    # ------------------------------------------------------------------
    def get_session(self, user_id: str) -> Optional[PyRofexSession]:
        session = self._sessions.get(user_id)
        if session and session.is_valid():
            return session
        if user_id in self._sessions:
            self.remove_session(user_id)
        return None

    def store_session(self, session: PyRofexSession) -> None:
        self._sessions[session.user_id] = session

    def remove_session(self, user_id: str) -> None:
        session = self._sessions.pop(user_id, None)
        if session:
            try:
                session.close()
            except Exception:
                pass
        self._connections.pop(user_id, None)
        self._remove_quotes(user_id)

    def iter_sessions(self):
        return self._sessions.items()

    def session_count(self) -> int:
        return len(self._sessions)

    def has_session(self, user_id: str) -> bool:
        return user_id in self._sessions

    # ------------------------------------------------------------------
    # Quotes cache
    # ------------------------------------------------------------------
    def store_quote(self, user_id: str, symbol: str, payload: Dict[str, Any]) -> None:
        self._quotes[f"{user_id}:{symbol.upper()}"] = payload

    def list_quotes(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        prefix = f"{user_id}:"
        result: Dict[str, Dict[str, Any]] = {}
        for key, value in self._quotes.items():
            if key.startswith(prefix):
                symbol = key.split(":", 1)[1]
                result[symbol] = value
        return result

    def quote_count(self) -> int:
        return len(self._quotes)

    def _remove_quotes(self, user_id: str) -> None:
        prefix = f"{user_id}:"
        keys = [key for key in self._quotes if key.startswith(prefix)]
        for key in keys:
            del self._quotes[key]

    # ------------------------------------------------------------------
    # WebSocket connections & order updates
    # ------------------------------------------------------------------
    def get_connection_state(self, user_id: str) -> Dict[str, Any]:
        return self._connections.setdefault(
            user_id,
            {
                "initialized": False,
                "market_subscriptions": [],
                "order_subscriptions": [],
                "order_updates": [],
            },
        )

    def peek_connection_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self._connections.get(user_id)

    def connection_count(self) -> int:
        return len(self._connections)

    def websocket_initialized(self, user_id: str) -> bool:
        state = self._connections.get(user_id)
        return bool(state and state.get("initialized"))

    def mark_websocket_initialized(self, user_id: str) -> None:
        state = self.get_connection_state(user_id)
        state["initialized"] = True

    def append_order_update(self, user_id: str, update: Dict[str, Any]) -> None:
        state = self.get_connection_state(user_id)
        updates: List[Dict[str, Any]] = state.setdefault("order_updates", [])
        updates.append(update)
        if len(updates) > 100:
            state["order_updates"] = updates[-100:]

    def list_order_updates(self, user_id: str) -> List[Dict[str, Any]]:
        state = self._connections.get(user_id)
        if not state:
            return []
        return list(state.get("order_updates", []))

    def order_update_count(self, user_id: str) -> int:
        state = self._connections.get(user_id)
        if not state:
            return 0
        return len(state.get("order_updates", []))

    def remove_connection(self, user_id: str) -> None:
        self._connections.pop(user_id, None)

    def clear_user_quotes(self, user_id: str) -> None:
        self._remove_quotes(user_id)

    def cleanup(self) -> int:
        """Remueve sesiones inválidas y estados huérfanos. Devuelve la cantidad limpiada."""
        invalid_users = [
            user_id for user_id, session in self._sessions.items() if not session.is_valid()
        ]
        for user_id in invalid_users:
            self.remove_session(user_id)
        return len(invalid_users)


session_registry = SessionRegistry()
"""Instancia reutilizable para el servidor MCP."""
