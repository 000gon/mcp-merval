"""
Authentication and session management tools.

This module contains MCP tools for:
- User login and authentication
- Session management and status
- Logout operations
- Server health monitoring
- API connectivity checks
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional

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
from lib.pyrofex_session import PyRofexSession
from lib.session_registry import session_registry
# Import common utilities
from .common import _safe_json, _get_session, _ensure_authenticated, get_mcp

logger = logging.getLogger(__name__)

# Shared FastMCP instance provided by server
mcp = get_mcp()


@mcp.tool()
def login(
    user: str,
    password: str,
    account: str,
    environment: str = "LIVE",
    user_id: str = "default"
) -> str:
    """
    Authenticate with ROFEX/Primary API.

    Args:
        user: ROFEX username
        password: ROFEX password
        account: Trading account
        environment: LIVE or REMARKET (default: LIVE)
        user_id: Unique user identifier for session management

    Returns:
        JSON string with login result
    """
    if not PYROFEX_AVAILABLE:
        return _safe_json({"success": False, "error": "pyRofex library not available"})

    if settings.force_live_environment and environment.upper() != settings.live_environment:
        return _safe_json({
            "success": False,
            "error": "Solo se admite el entorno LIVE de Matriz"
        })

    try:
        logger.info(f"=== LOGIN ATTEMPT ===")
        logger.info(f"User ID: {user_id}")
        logger.info(f"ROFEX User: {user}")
        logger.info(f"Environment: {settings.live_environment}")

        if settings.require_credentials and not all([user, password, account]):
            return _safe_json({
                "success": False,
                "error": "Faltan credenciales Matriz (usuario, contraseña o cuenta)"
            })

        # Check if user already has a session
        existing_success, _, existing_session = _get_session(user_id)
        if existing_success and existing_session:
            logger.info(f"User {user_id} already has active session")
            return _safe_json({
                "success": True,
                "message": "Already authenticated",
                "session_info": {
                    "user_id": user_id,
                    "account": existing_session.account,
                    "environment": existing_session.environment.name if existing_session.environment else None,
                    "authenticated_at": existing_session.created_at.isoformat(),
                    "expires_at": existing_session.expires_at.isoformat() if existing_session.expires_at else None
                }
            })

        # Create new session
        session = PyRofexSession(user_id)

        # Authenticate with retry logic for network failures
        success = session.authenticate_with_retry(
            user,
            password,
            account,
            settings.live_environment,
            max_retries=3
        )

        if success:
            # Store in memory
            session_registry.store_session(session)

            logger.info(f" Login successful for user {user_id}")
            return _safe_json({
                "success": True,
                "message": "Authentication successful",
                "session_info": {
                    "user_id": user_id,
                    "account": session.account,
                    "environment": session.environment.name,
                    "authenticated_at": session.created_at.isoformat(),
                    "expires_at": session.expires_at.isoformat() if session.expires_at else None,
                    "storage": "Memory"
                }
            })
        else:
            return _safe_json({"success": False, "error": "Authentication failed"})

    except Exception as e:
        error_msg = str(e)
        logger.error(f"L Login failed for user {user_id}: {error_msg}")

        # Map technical errors to user-friendly Spanish messages
        if "Invalid username or password" in error_msg:
            user_error = "Credenciales incorrectas. Verifica tu usuario y contraseña."
        elif "not authorized" in error_msg:
            user_error = f"La cuenta {account} no está autorizada para operar en LIVE."
        elif "Cannot connect to ROFEX API" in error_msg or "Connection timeout" in error_msg:
            user_error = "No se puede conectar con ROFEX. Verifica tu conexión e intenta más tarde."
        elif "Missing required credentials" in error_msg or "Invalid credentials" in error_msg:
            user_error = "Faltan credenciales requeridas. Proporciona usuario, contraseña y cuenta."
        elif "pyRofex library not available" in error_msg:
            user_error = "Servicio de trading no disponible temporalmente."
        elif "Invalid environment" in error_msg:
            user_error = "Se fuerza el entorno LIVE. Verificá la configuración del broker."
        elif "no access token received" in error_msg or "Token not obtained" in error_msg:
            user_error = "Error de autenticación con ROFEX. Verifica tus credenciales."
        else:
            # Generic error message for unexpected issues
            user_error = f"Error de autenticación: {error_msg}"

        return _safe_json({"success": False, "error": user_error})


@mcp.tool()
def check_rofex_connectivity() -> str:
    """
    Check if ROFEX API endpoints are reachable and responsive.

    Returns:
        JSON string con el estado de conectividad LIVE
    """
    import requests

    try:
        logger.info("Checking ROFEX API connectivity")

        # Solo probamos el endpoint LIVE de Matriz
        urls = {
            "LIVE": settings.pyrofx_live_url,
        }

        results = {}
        overall_status = True

        for env_name, base_url in urls.items():
            try:
                # Test a basic endpoint that doesn't require authentication
                test_url = f"{base_url.rstrip('/')}/rest/risk/allowedBalanceByEnvironment"

                logger.info(f"Testing {env_name} connectivity: {test_url}")
                start_time = datetime.utcnow()

                response = requests.get(
                    test_url,
                    timeout=10,  # 10 second timeout
                    headers={"User-Agent": "Liggio-MCP-HealthCheck/1.0"}
                )

                end_time = datetime.utcnow()
                response_time_ms = int((end_time - start_time).total_seconds() * 1000)

                results[env_name] = {
                    "reachable": True,
                    "status_code": response.status_code,
                    "response_time_ms": response_time_ms,
                    "url": test_url,
                    "status": "healthy" if response.status_code < 500 else "degraded"
                }

                logger.info(f" {env_name} reachable: {response.status_code} ({response_time_ms}ms)")

            except requests.exceptions.Timeout:
                results[env_name] = {
                    "reachable": False,
                    "error": "Connection timeout (>10s)",
                    "url": test_url,
                    "status": "unreachable"
                }
                overall_status = False
                logger.warning(f"L {env_name} timeout")

            except requests.exceptions.ConnectionError as e:
                results[env_name] = {
                    "reachable": False,
                    "error": f"Connection error: {str(e)[:100]}",
                    "url": test_url,
                    "status": "unreachable"
                }
                overall_status = False
                logger.warning(f"L {env_name} connection error: {e}")

            except Exception as e:
                results[env_name] = {
                    "reachable": False,
                    "error": f"Unexpected error: {str(e)[:100]}",
                    "url": test_url,
                    "status": "error"
                }
                overall_status = False
                logger.error(f"L {env_name} error: {e}")

        return _safe_json({
            "success": True,
            "overall_status": "healthy" if overall_status else "degraded",
            "environments": results,
            "timestamp": datetime.utcnow().isoformat(),
            "tested_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        })

    except Exception as e:
        logger.error(f"L Connectivity check failed: {e}")
        return _safe_json({
            "success": False,
            "error": f"Connectivity check failed: {str(e)}",
            "timestamp": datetime.utcnow().isoformat()
        })


@mcp.tool()
def logout(user_id: str = "anonymous") -> str:
    """
    Logout and cleanup user session.

    Args:
        user_id: User identifier

    Returns:
        JSON string with logout result
    """
    try:
        logger.info(f"Logout request for user {user_id}")

        # Get session
        success, error, session = _get_session(user_id)
        if success and session:
            # Remove de la memoria
            session_registry.remove_session(user_id)

            logger.info(f" Logout successful for user {user_id}")
            return _safe_json({
                "success": True,
                "message": f"User {user_id} logged out successfully"
            })
        else:
            return _safe_json({
                "success": True,  # Not an error if already logged out
                "message": f"User {user_id} was not logged in"
            })

    except Exception as e:
        logger.error(f"Logout error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


@mcp.tool()
def get_session_status(user_id: str = "anonymous") -> str:
    """
    Get current session status and information.

    Args:
        user_id: User identifier

    Returns:
        JSON string with session status
    """
    try:
        # Try to reuse existing session or fall back to auto-login if configured
        success, error, session = _ensure_authenticated(user_id)

        if success and session:
            # Test session validity with a simple API call
            api_test = False
            try:
                if session.rest_client:
                    # Try to get segments as a simple API test
                    pyRofex.get_segments()
                    api_test = True
            except Exception:
                pass

            return _safe_json({
                "success": True,
                "status": "authenticated",
                "session_info": {
                    "user_id": session.user_id,
                    "account": session.account,
                    "user": session.user,
                    "environment": session.environment.name if session.environment else None,
                    "created_at": session.created_at.isoformat(),
                    "last_activity": session.last_activity.isoformat(),
                    "expires_at": session.expires_at.isoformat() if session.expires_at else None,
                    "api_connection": api_test,
                    "active_subscriptions": list(session.active_subscriptions.keys())
                }
            })
        else:
            return _safe_json({
                "success": True,
                "status": "not_authenticated",
                "message": error or "No active session"
            })

    except Exception as e:
        logger.error(f"Session status error for user {user_id}: {e}")
        return _safe_json({"success": False, "error": str(e)})


@mcp.tool()
def get_server_health() -> str:
    """
    Get server health status and statistics.

    Returns:
        JSON string with health information
    """
    try:
        health = {
            "status": "ok",
            "service": "pyrofex-mcp-server",
            "pyrofex_available": PYROFEX_AVAILABLE,
            "in_memory_sessions": session_registry.session_count(),
            "active_ws_connections": session_registry.connection_count(),
            "quotes_cached": session_registry.quote_count(),
            "session_storage": {
                "strategy": "memory",
                "firestore_available": False,
                "status": "in-memory-only",
            },
        }

        return _safe_json(health)

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return _safe_json({
            "status": "error",
            "service": "pyrofex-mcp-server",
            "error": str(e)
        })
