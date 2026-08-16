"""
Centralized Structured Logging and Error Tracking for Antigravity Analytics & Vault.
Provides thread-safe log rotation, console output, and in-memory error capture for API observability.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from collections import deque

USER_HOME = Path.home()
VAULT_DIR = USER_HOME / ".antigravity_analytics_vault"
LOG_DIR = VAULT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "antigravity.log"

# In-memory recent error ring buffer (last 50 errors)
_RECENT_ERRORS: deque = deque(maxlen=50)

# Logger singleton
_LOGGER: logging.Logger = None


def get_logger(name: str = "antigravity") -> logging.Logger:
    """Returns the shared, configured application logger."""
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger("antigravity")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Clear existing handlers if re-initializing
    if logger.handlers:
        logger.handlers.clear()

    # Formatter
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    # 1. Rotating File Handler (10MB max, 5 backups)
    try:
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[!] Warning: Could not initialize rotating file logger: {e}", file=sys.stderr)

    # 2. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    _LOGGER = logger
    return _LOGGER


def log_error(module: str, message: str, exception: Exception = None, details: Dict[str, Any] = None):
    """Log an error to the logger and append to the in-memory error buffer."""
    logger = get_logger()
    err_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "module": module,
        "message": message,
        "error": str(exception) if exception else None,
        "error_type": exception.__class__.__name__ if exception else None,
        "details": details or {},
    }
    _RECENT_ERRORS.append(err_entry)
    if exception:
        logger.error(f"[{module}] {message}: {exception}", exc_info=True)
    else:
        logger.error(f"[{module}] {message}")


def get_recent_system_errors() -> List[Dict[str, Any]]:
    """Retrieve list of recent system errors for observability endpoints."""
    return list(_RECENT_ERRORS)
