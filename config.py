"""
Config and Dynamic Settings for Antigravity Multi-Account Token & Cost Analytics Dashboard.
Supports Gemini 3.5 Pro, Gemini 3.6 Flash, Gemini 3.7 Flash with Deep Thinking / Reasoning budgets.
Includes Localhost Hardening, Dynamic PIN Security, Vault Paths, and Historical Scanner Discovery Roots.
"""

import os
import sys
import socket
from pathlib import Path

# Base Work & Vault Paths
WORKSPACE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(WORKSPACE_DIR / "antigravity_telemetry.db")
TEMPLATES_DIR = WORKSPACE_DIR / "templates"

# Persistent Vault Storage (immune to ~/.gemini/ cache wipes)
USER_HOME = Path.home()
VAULT_DIR = USER_HOME / ".antigravity_analytics_vault"
BACKUPS_DIR = VAULT_DIR / "backups"
ARCHIVE_JSON_PATH = VAULT_DIR / "all_time_archive.json"
VAULT_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Host & Port configuration - Default binds to 127.0.0.1 for local security
SERVER_HOST = os.getenv("ANTIGRAVITY_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("ANTIGRAVITY_PORT", 4848))

# Opt-in Cloudflare Tunnel (disabled by default for security & privacy)
ENABLE_TUNNEL = os.getenv("ANTIGRAVITY_ENABLE_TUNNEL", "false").lower() in ("true", "1", "yes")

# Dynamic Currency & Forex
DEFAULT_USD_TO_INR = 87.00
FOREX_API_URLS = [
    "https://open.er-api.com/v6/latest/USD",
    "https://api.exchangerate-api.com/v4/latest/USD",
]
FOREX_UPDATE_INTERVAL_HOURS = 6

# Deep Historical Ingestion Discovery Roots
APPDATA_ROAMING = Path(os.getenv("APPDATA", str(USER_HOME / "AppData" / "Roaming")))
LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA", str(USER_HOME / "AppData" / "Local")))

HISTORICAL_DISCOVERY_ROOTS = [
    # 1. Antigravity IDE Brain & Knowledge
    USER_HOME / ".gemini" / "antigravity-ide" / "brain",
    USER_HOME / ".gemini" / "antigravity" / "brain",
    USER_HOME / ".gemini" / "antigravity-ide" / "knowledge",
    USER_HOME / ".gemini" / "antigravity" / "knowledge",
    USER_HOME / ".gemini" / "antigravity" / "conversations",
    # 2. Antigravity IDE Storage
    APPDATA_ROAMING / "Antigravity IDE" / "User" / "globalStorage",
    APPDATA_ROAMING / "Antigravity IDE" / "User" / "workspaceStorage",
    APPDATA_ROAMING / "Antigravity IDE" / "User" / "History",
    # 3. Antigravity Global Storage
    APPDATA_ROAMING / "Antigravity" / "User" / "globalStorage",
    APPDATA_ROAMING / "Antigravity" / "User" / "workspaceStorage",
    # 4. VS Code Storage (Cross-compatibility)
    APPDATA_ROAMING / "Code" / "User" / "globalStorage",
    APPDATA_ROAMING / "Code" / "User" / "workspaceStorage",
    # 5. Linux / macOS standard paths
    USER_HOME / ".config" / "antigravity",
    USER_HOME / ".config" / "gemini",
    # 6. Temp cache / crash directories
    LOCALAPPDATA / "Temp",
]

# Standard live watcher paths
DISCOVERY_PATHS = [
    USER_HOME / ".gemini" / "antigravity-ide" / "brain",
    USER_HOME / ".gemini" / "antigravity" / "brain",
    APPDATA_ROAMING / "Antigravity IDE" / "User" / "globalStorage",
    APPDATA_ROAMING / "Antigravity" / "User" / "globalStorage",
]

# 5 Default Accounts Fleet
DEFAULT_ACCOUNTS = [
    {
        "account_id": "acc_1",
        "account_alias_or_hash": "Account 1 (Primary - Dev)",
        "email": "primary.dev@gemini.local",
        "color": "#6366f1",  # Indigo
        "status": "active",
    },
    {
        "account_id": "acc_2",
        "account_alias_or_hash": "Account 2 (Workhorse - Flash)",
        "email": "workhorse.flash@gemini.local",
        "color": "#10b981",  # Emerald
        "status": "active",
    },
    {
        "account_id": "acc_3",
        "account_alias_or_hash": "Account 3 (Architecture - Pro)",
        "email": "arch.pro@gemini.local",
        "color": "#f59e0b",  # Amber
        "status": "active",
    },
    {
        "account_id": "acc_4",
        "account_alias_or_hash": "Account 4 (Research & Reasoning)",
        "email": "research.reasoning@gemini.local",
        "color": "#8b5cf6",  # Purple
        "status": "active",
    },
    {
        "account_id": "acc_5",
        "account_alias_or_hash": "Account 5 (Sandbox / Client)",
        "email": "sandbox.client@gemini.local",
        "color": "#ec4899",  # Pink
        "status": "active",
    },
]

# Re-export Pricing Engine Functions & Constants for seamless compatibility
from pricing_engine import (
    MODEL_PRICING,
    THINKING_BUDGET_ESTIMATES,
    normalize_model_name,
    parse_thinking_level,
    calculate_turn_cost,
    estimate_tokens,
)


def get_local_lan_ip() -> str:
    """Detect local Wi-Fi / Ethernet LAN IP for same-network pairing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
