#!/usr/bin/env python3
"""
PyRofexSession - Individual user session wrapper for pyRofex API access.

Provides isolated pyRofex REST and WebSocket clients per user to enable multi-tenancy.
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# Add pyRofex to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROFEX_SRC = os.path.abspath(os.path.join(REPO_ROOT, "pyRofex-master", "src"))
if PYROFEX_SRC not in sys.path:
    sys.path.insert(0, PYROFEX_SRC)

try:
    import pyRofex
    from pyRofex.clients.rest_rfx import RestClient
    from pyRofex.clients.websocket_rfx import WebSocketClient
    from pyRofex.components.enums import Environment, Side, OrderType, TimeInForce, MarketDataEntry
    PYROFEX_AVAILABLE = True
except ImportError as e:
    logging.error(f"pyRofex not available: {e}")
    PYROFEX_AVAILABLE = False

logger = logging.getLogger(__name__)
try:
    from config import settings
except Exception:
    settings = None


class PyRofexSession:
    """
    Isolated pyRofex session for a single user.
    
    Maintains separate REST and WebSocket clients to avoid global state conflicts
    in multi-user scenarios.
    """
    
    def __init__(self, user_id: str):
        """
        Initialize session for a specific user.
        
        Args:
            user_id: Unique identifier for this user session
        """
        self.user_id = user_id
        self.environment = None
        self.environment_config = None
        self.rest_client = None
        self.ws_client = None
        
        # Authentication state
        self.token = None
        self.account = None
        self.user = None
        self.authenticated = False
        
        # Session metadata
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()
        self.expires_at = None
        
        # WebSocket subscriptions
        self.active_subscriptions = {}
        
        logger.debug(f"Created PyRofexSession for user {user_id}")
    
    def authenticate(self, user: str, password: str, account: str, environment: str = "LIVE") -> bool:
        """
        Authenticate with ROFEX API and create isolated client instances.
        
        Args:
            user: ROFEX username
            password: ROFEX password  
            account: Trading account
            environment: LIVE or REMARKET
            
        Returns:
            bool: True if authentication successful
        """
        if not PYROFEX_AVAILABLE:
            raise Exception("pyRofex library not available")
        
        try:
            logger.info(f"Authenticating user {self.user_id} with ROFEX {environment}")
            
            # Validate inputs
            if not all([user, password, account]):
                missing_fields = []
                if not user: missing_fields.append("user")
                if not password: missing_fields.append("password")
                if not account: missing_fields.append("account")
                raise ValueError(f"Missing required credentials: {', '.join(missing_fields)}")
            
            # Validate environment
            if environment not in ["LIVE", "REMARKET"]:
                raise ValueError(f"Invalid environment '{environment}'. Must be 'LIVE' or 'REMARKET'")
            
            # Map environment string to enum
            self.environment = Environment.LIVE if environment == "LIVE" else Environment.REMARKET
            
            # Set up the global environment configuration for pyRofex
            self._setup_pyrofex_environment(user, password, account, self.environment)
            
            # Use pyRofex initialization to authenticate and get token
            logger.info(f"Calling pyRofex.initialize for user {self.user_id}")
            # Provide ssl_opt if WS insecure is enabled (dev fallback)
            ssl_opt = None
            try:
                if settings and getattr(settings, 'pyrofx_ws_insecure', False):
                    import ssl
                    ssl_opt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
            except Exception:
                ssl_opt = None

            pyRofex.initialize(
                user=user,
                password=password,
                account=account,
                environment=self.environment,
                ssl_opt=ssl_opt
            )
            
            # Get the REST client from the initialized environment
            from pyRofex.components import globals
            
            if self.environment not in globals.environment_config:
                raise Exception(f"Environment {self.environment} not configured after initialization")
                
            env_config = globals.environment_config[self.environment]
            self.rest_client = env_config.get("rest_client")
            
            if not self.rest_client:
                raise Exception("REST client not created after initialization")
            
            # Store authentication details
            self.user = user
            self.account = account
            self.token = env_config.get("token")
            
            if not self.token:
                raise Exception("No authentication token received from ROFEX")
                
            self.authenticated = True
            self.last_activity = datetime.utcnow()
            self.expires_at = datetime.utcnow() + timedelta(hours=8)  # 8-hour session
            
            logger.info(f"✅ Successfully authenticated user {self.user_id}, token: {self.token[:20]}...")
            return True
            
        except ValueError as e:
            logger.error(f"❌ Invalid input for user {self.user_id}: {e}")
            self.authenticated = False
            raise Exception(f"Invalid credentials: {str(e)}")
        except ConnectionError as e:
            logger.error(f"❌ Connection error for user {self.user_id}: {e}")
            self.authenticated = False
            raise Exception(f"Cannot connect to ROFEX API: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Authentication failed for user {self.user_id}: {e}")
            self.authenticated = False
            
            # Provide more specific error messages based on error content
            error_str = str(e).lower()
            if "401" in error_str or "unauthorized" in error_str:
                raise Exception("Invalid username or password")
            elif "403" in error_str or "forbidden" in error_str:
                raise Exception(f"Account '{account}' not authorized for {environment} environment")
            elif "timeout" in error_str or "connection" in error_str:
                raise Exception("Connection timeout - ROFEX API may be unavailable")
            elif "token not obtained" in error_str:
                raise Exception("Authentication failed - no access token received")
            else:
                raise Exception(f"Authentication error: {str(e)}")
    
    def authenticate_with_retry(self, user: str, password: str, account: str, 
                               environment: str = "LIVE", max_retries: int = 3) -> bool:
        """
        Authenticate with retry logic for transient failures.
        
        Args:
            user: ROFEX username
            password: ROFEX password  
            account: Trading account
            environment: LIVE or REMARKET
            max_retries: Maximum number of retry attempts
            
        Returns:
            bool: True if authentication successful
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
                    logger.info(f"Retry attempt {attempt + 1}/{max_retries} for user {self.user_id} (waiting {delay}s)")
                    time.sleep(delay)
                
                return self.authenticate(user, password, account, environment)
                
            except Exception as e:
                last_error = e
                error_msg = str(e)
                
                # Don't retry for credential errors or validation errors
                if any(phrase in error_msg for phrase in [
                    "Invalid username or password", 
                    "not authorized", 
                    "Invalid credentials",
                    "Missing required credentials",
                    "Invalid environment"
                ]):
                    logger.error(f"Authentication failed with credential error (not retrying): {e}")
                    raise e
                
                # Retry for network/connection issues
                logger.warning(f"Authentication attempt {attempt + 1}/{max_retries} failed: {e}")
        
        # All retries exhausted
        logger.error(f"Authentication failed after {max_retries} attempts for user {self.user_id}")
        raise Exception(f"Authentication failed after {max_retries} attempts: {last_error}")
    
    def _setup_pyrofex_environment(self, user: str, password: str, account: str, environment: Environment):
        """
        Set up pyRofex global environment configuration for this user session.
        
        Args:
            user: ROFEX username
            password: ROFEX password
            account: Trading account
            environment: ROFEX environment enum
        """
        from pyRofex.components import globals
        
        # Configure URLs for EcoValores if LIVE environment
        if environment == Environment.LIVE:
            url = os.getenv("PYROFEX_URL", "https://api.eco.xoms.com.ar/")
            ws = os.getenv("PYROFEX_WS", "wss://api.eco.xoms.com.ar/")
            proprietary = os.getenv("PYROFEX_PROP", "PBCP")
            
            # Update the global configuration with EcoValores settings
            globals.environment_config[environment].update({
                "url": url,
                "ws": ws,
                "proprietary": proprietary
            })
            # Optionally relax SSL verification for WS if configured (development fallback)
            try:
                if settings and getattr(settings, 'pyrofx_ws_insecure', False):
                    import ssl
                    globals.environment_config[environment]["ssl_opt"] = {
                        "cert_reqs": ssl.CERT_NONE,
                        "check_hostname": False,
                    }
                    logger.warning("WS SSL verification disabled via PYROFEX_WS_INSECURE (development mode)")
            except Exception as _e:
                logger.debug(f"Could not set WS ssl_opt: {_e}")
            
            logger.info(f"Configured EcoValores environment: {url}")
        
        # Store user credentials in global config (will be used by pyRofex.initialize)
        globals.environment_config[environment]["user"] = user
        globals.environment_config[environment]["password"] = password
        globals.environment_config[environment]["account"] = account
    
    def is_valid(self) -> bool:
        """
        Check if session is still valid and authenticated.
        
        Returns:
            bool: True if session is valid
        """
        if not self.authenticated:
            return False
        
        if self.expires_at and datetime.utcnow() > self.expires_at:
            logger.warning(f"Session expired for user {self.user_id}")
            return False
        
        if not self.rest_client:
            return False
        
        return True
    
    def refresh_token(self) -> bool:
        """
        Refresh authentication token if needed.
        
        Returns:
            bool: True if refresh successful
        """
        if not self.rest_client:
            return False
        
        try:
            self.rest_client.update_token()
            
            # Get updated token from globals
            from pyRofex.components import globals
            self.token = globals.environment_config[self.environment]["token"]
            self.last_activity = datetime.utcnow()
            self.expires_at = datetime.utcnow() + timedelta(hours=8)
            logger.info(f"Token refreshed for user {self.user_id}")
            return True
        except Exception as e:
            logger.error(f"Token refresh failed for user {self.user_id}: {e}")
            return False
    
    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()
    
    def init_websocket(self, market_data_handler=None, order_report_handler=None, error_handler=None, exception_handler=None):
        """
        Initialize WebSocket client for this user session.
        
        Args:
            market_data_handler: Handler for market data messages
            order_report_handler: Handler for order report messages  
            error_handler: Handler for error messages
            exception_handler: Handler for exceptions
        """
        if not self.authenticated or not PYROFEX_AVAILABLE:
            raise Exception("Session not authenticated or pyRofex not available")
        
        try:
            # Create WebSocket client
            self.ws_client = WebSocketClient(self.environment)
            from pyRofex.components import globals
            globals.environment_config[self.environment]["ws_client"] = self.ws_client
            
            # Initialize connection with handlers
            pyRofex.init_websocket_connection(
                market_data_handler=market_data_handler,
                order_report_handler=order_report_handler, 
                error_handler=error_handler,
                exception_handler=exception_handler
            )
            
            logger.info(f"WebSocket initialized for user {self.user_id}")
        except Exception as e:
            logger.error(f"WebSocket initialization failed for user {self.user_id}: {e}")
            raise e
    
    def close(self):
        """
        Close session and cleanup resources.
        """
        try:
            # Close WebSocket connection if active
            if self.ws_client:
                pyRofex.close_websocket_connection()
                self.ws_client = None
            
            # Clear active subscriptions
            self.active_subscriptions.clear()
            
            # Reset authentication state
            self.authenticated = False
            self.token = None
            self.rest_client = None
            
            logger.info(f"Session closed for user {self.user_id}")
            
        except Exception as e:
            logger.warning(f"Error closing session for user {self.user_id}: {e}")
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert session to dictionary for storage.
        
        Returns:
            Dict with session data
        """
        return {
            "user_id": self.user_id,
            "user": self.user,
            "account": self.account,
            "environment": self.environment.name if self.environment else None,
            "authenticated": self.authenticated,
            "token": self.token,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "active_subscriptions": list(self.active_subscriptions.keys())
        }
