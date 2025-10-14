"""Configuración central del servidor MCP.

Expone una dataclass `Settings` que lee variables de entorno y valida que la
instancia esté preparada para operar contra Matriz en modo LIVE.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any


def _to_bool(value: Optional[str], *, default: bool = False) -> bool:
    """Convertir str booleano en bool real."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


@dataclass
class Settings:
    """Valores de configuración para el servidor MCP.

    - Obliga entorno LIVE (sin modo REMARKET/offline).
    - Garantiza que las operaciones de MEP usen tasas reales.
    - Soporta configuración de brokers desde archivo JSON.
    """

    log_level: str = "INFO"
    commission_rate: float = 0.005
    session_ttl_hours: int = 8
    marketdata_url: Optional[str] = None
    use_pyrofex_for_mep: bool = False
    force_live_environment: bool = True
    pyrofx_live_url: str = "https://api.eco.xoms.com.ar/"
    pyrofx_request_timeout: int = 10
    pyrofx_ws_insecure: bool = False
    require_credentials: bool = True
    mcp_api_key: Optional[str] = None
    broker_config_path: Optional[str] = None
    broker_config: Optional[Dict[str, Any]] = None

    @classmethod
    def load(cls) -> "Settings":
        """Construye `Settings` leyendo variables de entorno y configuración de brokers."""
        commission_rate = float(os.getenv("COMMISSION_RATE", "0.005"))
        session_ttl_hours = int(os.getenv("SESSION_TTL_HOURS", "8"))
        pyrofx_request_timeout = int(os.getenv("PYROFEX_TIMEOUT_SECONDS", "10"))

        # Load broker configuration if available
        broker_config_path = os.getenv("BROKER_CONFIG_PATH", "broker_config.json")
        broker_config = cls._load_broker_config(broker_config_path)

        # Use default broker URL from config, or fall back to env var or default ECO
        default_broker_url = "https://api.eco.xoms.com.ar/"
        if broker_config and "brokers" in broker_config:
            # Find default broker
            for broker_id, broker_data in broker_config["brokers"].items():
                if broker_data.get("default", False):
                    default_broker_url = broker_data.get("api_url", default_broker_url)
                    break

        instance = cls(
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            commission_rate=commission_rate,
            session_ttl_hours=session_ttl_hours,
            marketdata_url=os.getenv("MARKETDATA_URL"),
            # Predeterminado True para alinear con flujo recomendado (precios vía REST)
            use_pyrofex_for_mep=_to_bool(os.getenv("USE_PYROFEX_FOR_MEP"), default=True),
            force_live_environment=_to_bool(os.getenv("FORCE_LIVE_ENVIRONMENT"), default=True),
            pyrofx_live_url=os.getenv("PYROFEX_URL", default_broker_url),
            pyrofx_request_timeout=pyrofx_request_timeout,
            pyrofx_ws_insecure=_to_bool(os.getenv("PYROFEX_WS_INSECURE"), default=False),
            require_credentials=_to_bool(os.getenv("REQUIRE_BROKER_CREDENTIALS"), default=True),
            mcp_api_key=os.getenv("MCP_API_KEY"),
            broker_config_path=broker_config_path,
            broker_config=broker_config,
        )
        instance.validate()
        return instance

    @staticmethod
    def _load_broker_config(config_path: str) -> Optional[Dict[str, Any]]:
        """Load broker configuration from JSON file.

        Supports relative paths from either the current working directory or the
        directory containing this module, so the server works when launched
        from other repos.
        """
        candidate_paths: list[Path] = []

        path = Path(config_path)
        if path.is_absolute():
            candidate_paths.append(path)
        else:
            candidate_paths.append(Path.cwd() / path)
            module_dir = Path(__file__).resolve().parent
            candidate_paths.append(module_dir / path)

        for candidate in candidate_paths:
            try:
                if candidate.exists() and candidate.is_file():
                    with open(candidate, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                # Silently ignore if config doesn't exist or fails to load
                continue

        return None

    def validate(self) -> None:
        """Valida consistencia de la configuración cargada."""
        if self.commission_rate < 0:
            raise ValueError("COMMISSION_RATE no puede ser negativo")
        if self.session_ttl_hours <= 0:
            raise ValueError("SESSION_TTL_HOURS debe ser mayor a cero")
        if self.pyrofx_request_timeout <= 0:
            raise ValueError("PYROFEX_TIMEOUT_SECONDS debe ser mayor a cero")

    @property
    def live_environment(self) -> str:
        """Nombre del entorno soportado (siempre LIVE)."""
        return "LIVE"

    def get_broker_config(self, broker_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific broker by ID."""
        if not self.broker_config or "brokers" not in self.broker_config:
            return None
        return self.broker_config["brokers"].get(broker_id)

    def get_default_broker(self) -> Optional[Dict[str, Any]]:
        """Get the default broker configuration."""
        if not self.broker_config or "brokers" not in self.broker_config:
            return None
        for broker_id, broker_data in self.broker_config["brokers"].items():
            if broker_data.get("default", False):
                return broker_data
        return None

    def list_available_brokers(self) -> list[str]:
        """List all configured broker IDs."""
        if not self.broker_config or "brokers" not in self.broker_config:
            return []
        return list(self.broker_config["brokers"].keys())

    def get_user_account(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get account configuration for a specific user.

        Supports environment variable substitution in password field.
        Example: "password": "${MERVAL_PASSWORD}" will read from env var.

        Args:
            user_id: User identifier from broker_config.json

        Returns:
            Dict with 'broker', 'username', 'password', 'account' or None if not found
        """
        if not self.broker_config or "user_accounts" not in self.broker_config:
            return None

        account = self.broker_config["user_accounts"].get(user_id)
        if not account:
            return None

        # Clone to avoid modifying original config
        account = account.copy()

        # Resolve environment variable in password if present
        password = account.get("password", "")
        if password.startswith("${") and password.endswith("}"):
            env_var = password[2:-1]
            account["password"] = os.getenv(env_var, "")

        return account

    def list_configured_users(self) -> list[str]:
        """List all user IDs configured in user_accounts section."""
        if not self.broker_config or "user_accounts" not in self.broker_config:
            return []
        return list(self.broker_config["user_accounts"].keys())

    def get_default_user(self) -> Optional[str]:
        """Get the first configured user ID, or None if no users configured."""
        users = self.list_configured_users()
        return users[0] if users else None


settings = Settings.load()
"""Instancia global de configuración, lista para importar."""
