"""
Config and Dynamic Pricing Engine for Antigravity Multi-Account Token & Cost Analytics Dashboard.
Supports Gemini 3.5 Pro, Gemini 3.6 Flash, Gemini 3.7 Flash with Deep Thinking / Reasoning budgets.
Includes Remote Cloud Access, Security PIN, Vault Paths, and Historical Scanner Discovery Roots.
"""

import os
import sys
import socket
from pathlib import Path

# Base Work & Vault Paths
WORKSPACE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = str(WORKSPACE_DIR / "antigravity_telemetry.db")
TEMPLATES_DIR = WORKSPACE_DIR / "templates"

# Persistent Immutable Vault Storage (immune to ~/.gemini/ cache wipes)
USER_HOME = Path.home()
VAULT_DIR = USER_HOME / ".antigravity_analytics_vault"
BACKUPS_DIR = VAULT_DIR / "backups"
ARCHIVE_JSON_PATH = VAULT_DIR / "all_time_archive.json"
VAULT_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Host & Port configuration
SERVER_HOST = os.getenv("ANTIGRAVITY_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("ANTIGRAVITY_PORT", 4848))

# Security PIN for Remote / Mobile Access
# Localhost bypasses PIN by default; remote/tunnel access requires PIN authentication.
DEFAULT_ACCESS_PIN = os.getenv("ANTIGRAVITY_PIN", "4848")
AUTH_SECRET_KEY = os.getenv("ANTIGRAVITY_SECRET", "antigravity_vault_secret_token_key_2026")

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

# Pricing Engine: Rates per 1,000,000 tokens (1M tokens)
# Billed rates: Thinking/reasoning tokens are billed as output tokens.
MODEL_PRICING = {
    # Gemini 3.7 Flash
    "gemini-3.7-flash": {
        "name": "Gemini 3.7 Flash",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Gemini 3.6 Flash (primary workhorse)
    "gemini-3.6-flash": {
        "name": "Gemini 3.6 Flash",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Gemini 3.5 Pro (tiered pricing <= 200k vs > 200k)
    "gemini-3.5-pro": {
        "name": "Gemini 3.5 Pro",
        "family": "pro",
        "input_per_million_standard": 2.00,
        "input_per_million_large": 4.00,
        "output_per_million_standard": 12.00,
        "output_per_million_large": 18.00,
        "cached_per_million": 0.20,
    },
    # Fallback Flash tier
    "fallback-flash": {
        "name": "Gemini Flash (Standard)",
        "family": "flash",
        "input_per_million": 0.75,
        "output_per_million": 3.75,
        "cached_per_million": 0.075,
    },
    # Fallback Pro tier
    "fallback-pro": {
        "name": "Gemini Pro (Standard)",
        "family": "pro",
        "input_per_million_standard": 2.00,
        "input_per_million_large": 4.00,
        "output_per_million_standard": 12.00,
        "output_per_million_large": 18.00,
        "cached_per_million": 0.20,
    },
}

# Thinking Token Budget Heuristics
THINKING_BUDGET_ESTIMATES = {
    "None": 0,
    "Low": 1000,
    "Medium": 3000,
    "High": 8000,
}


def get_local_lan_ip() -> str:
    """Detect local Wi-Fi / Ethernet LAN IP for same-network mobile pairing."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Connect to public DNS address without sending packet to detect outgoing interface IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def normalize_model_name(raw_name: str) -> str:
    """Normalize raw model string from telemetry to standard identifier."""
    if not raw_name:
        return "gemini-3.6-flash"
    raw_lower = str(raw_name).lower().strip()

    if "3.7" in raw_lower:
        if "pro" in raw_lower:
            return "gemini-3.5-pro"
        return "gemini-3.7-flash"
    elif "3.6" in raw_lower:
        if "pro" in raw_lower:
            return "gemini-3.5-pro"
        return "gemini-3.6-flash"
    elif "3.5" in raw_lower:
        if "flash" in raw_lower:
            return "gemini-3.6-flash"
        return "gemini-3.5-pro"
    elif "pro" in raw_lower:
        return "gemini-3.5-pro"
    elif "flash" in raw_lower:
        return "gemini-3.6-flash"
    return "gemini-3.6-flash"


def parse_thinking_level(raw_string: str) -> str:
    """Extract Low, Medium, High, or None from settings change or model string."""
    if not raw_string:
        return "None"
    s = str(raw_string).lower()
    if "(high)" in s or "high" in s:
        return "High"
    if "(medium)" in s or "medium" in s or "med" in s:
        return "Medium"
    if "(low)" in s or "low" in s:
        return "Low"
    return "None"


def calculate_turn_cost(
    model_name: str,
    prompt_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_thinking_tokens: int,
    usd_to_inr: float = DEFAULT_USD_TO_INR,
) -> tuple:
    """
    Calculates exact turn cost according to official Gemini pricing rules.
    1. Turn Output Tokens = Standard Output Tokens + Thinking/Reasoning Tokens
    2. Turn Cost (USD) = (Input * InRate) + (Turn Output * OutRate) + (Cached * CacheRate)
    3. Turn Cost (INR) = Turn Cost (USD) * usd_to_inr
    Returns: (total_tokens, total_output_tokens, cost_usd, cost_inr)
    """
    model_key = normalize_model_name(model_name)
    pricing = MODEL_PRICING.get(model_key, MODEL_PRICING["fallback-flash"])

    total_output = output_tokens + reasoning_thinking_tokens
    total_tokens = prompt_tokens + cached_tokens + total_output

    if pricing["family"] == "pro":
        if prompt_tokens > 200_000:
            in_rate = pricing["input_per_million_large"] / 1_000_000.0
            out_rate = pricing["output_per_million_large"] / 1_000_000.0
        else:
            in_rate = pricing["input_per_million_standard"] / 1_000_000.0
            out_rate = pricing["output_per_million_standard"] / 1_000_000.0
        cache_rate = pricing["cached_per_million"] / 1_000_000.0
    else:
        in_rate = pricing["input_per_million"] / 1_000_000.0
        out_rate = pricing["output_per_million"] / 1_000_000.0
        cache_rate = pricing["cached_per_million"] / 1_000_000.0

    cost_usd = (prompt_tokens * in_rate) + (total_output * out_rate) + (cached_tokens * cache_rate)
    cost_inr = cost_usd * usd_to_inr

    return (total_tokens, total_output, round(cost_usd, 6), round(cost_inr, 4))
