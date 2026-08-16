"""
Authentication, PIN Security, Session Tokens & Audit Logging for Antigravity Analytics.
Provides secure dynamic secret generation, HMAC-SHA256 signed session tokens,
brute-force rate limiting, and request authorization middleware.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from collections import defaultdict

from logger import get_logger

logger = get_logger("auth")

USER_HOME = Path.home()
VAULT_DIR = USER_HOME / ".antigravity_analytics_vault"
VAULT_DIR.mkdir(parents=True, exist_ok=True)
AUTH_CREDENTIALS_FILE = VAULT_DIR / ".auth_credentials.json"

# In-memory rate limiter for failed PIN attempts: {ip: [timestamps]}
_FAILED_ATTEMPTS: Dict[str, list] = defaultdict(list)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes


def _load_or_generate_credentials() -> Tuple[str, str]:
    """Loads existing auth credentials or securely generates new random credentials on first run."""
    env_pin = os.getenv("ANTIGRAVITY_PIN")
    env_secret = os.getenv("ANTIGRAVITY_SECRET")

    # If both provided via env, use them directly
    if env_pin and env_secret:
        return env_pin.strip(), env_secret.strip()

    # Check vault credentials file
    if AUTH_CREDENTIALS_FILE.exists():
        try:
            with open(AUTH_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                saved_pin = env_pin.strip() if env_pin else data.get("pin")
                saved_secret = env_secret.strip() if env_secret else data.get("secret")
                if saved_pin and saved_secret:
                    return str(saved_pin), str(saved_secret)
        except Exception as e:
            logger.warning(f"Could not read {AUTH_CREDENTIALS_FILE}: {e}")

    # Generate new secure credentials
    generated_pin = env_pin.strip() if env_pin else f"{secrets.randbelow(900000) + 100000}"
    generated_secret = env_secret.strip() if env_secret else secrets.token_hex(32)

    try:
        creds = {
            "pin": generated_pin,
            "secret": generated_secret,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "note": "Keep this file secure. It contains the authentication secret and PIN for remote access.",
        }
        with open(AUTH_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(creds, f, indent=2)

        if sys.platform != "win32":
            try:
                os.chmod(AUTH_CREDENTIALS_FILE, 0o600)
            except Exception:
                pass

        logger.info(f"[+] Security Credentials Initialized in {AUTH_CREDENTIALS_FILE}")
        try:
            print(f"\n=======================================================")
            print(f" [*] ANTIGRAVITY REMOTE ACCESS PIN: {generated_pin}")
            print(f" (Use this 6-digit PIN to authenticate remote/mobile sessions)")
            print(f"=======================================================\n")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to persist credentials to {AUTH_CREDENTIALS_FILE}: {e}")

    return str(generated_pin), str(generated_secret)


# Initialize active credentials
ACTIVE_PIN, AUTH_SECRET_KEY = _load_or_generate_credentials()


def get_active_pin() -> str:
    """Returns the current active security PIN."""
    return ACTIVE_PIN


def is_ip_locked_out(ip: str) -> Tuple[bool, int]:
    """Checks if an IP is currently locked out due to excessive failed attempts."""
    now = time.time()
    # Clean old attempts outside lockout window
    _FAILED_ATTEMPTS[ip] = [t for t in _FAILED_ATTEMPTS[ip] if now - t < LOCKOUT_WINDOW_SECONDS]

    if len(_FAILED_ATTEMPTS[ip]) >= MAX_FAILED_ATTEMPTS:
        oldest = _FAILED_ATTEMPTS[ip][0]
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - oldest))
        return True, max(1, remaining)
    return False, 0


def record_failed_attempt(ip: str):
    """Records a failed PIN authentication attempt for an IP."""
    _FAILED_ATTEMPTS[ip].append(time.time())


def clear_failed_attempts(ip: str):
    """Clears failed attempts upon successful login."""
    if ip in _FAILED_ATTEMPTS:
        del _FAILED_ATTEMPTS[ip]


def make_auth_token(pin: str, expiry_seconds: int = 86400 * 30) -> str:
    """
    Generates a signed, time-limited HMAC session token.
    Token structure: exp_timestamp.hmac_signature
    """
    exp_ts = int(time.time()) + expiry_seconds
    msg = f"{pin}:{exp_ts}:{AUTH_SECRET_KEY}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{exp_ts}.{sig}"


def verify_auth_token(token: str) -> bool:
    """
    Validates a session token's signature, expiry, and secret binding.
    """
    if not token or "." not in token:
        return False

    try:
        parts = token.split(".", 1)
        exp_ts_str, received_sig = parts[0], parts[1]
        exp_ts = int(exp_ts_str)

        # Check token expiration
        if time.time() > exp_ts:
            return False

        msg = f"{ACTIVE_PIN}:{exp_ts}:{AUTH_SECRET_KEY}".encode("utf-8")
        expected_sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()

        return hmac.compare_digest(received_sig, expected_sig)
    except Exception:
        return False


def verify_pin_login(input_pin: str, client_ip: str) -> Tuple[bool, str, Optional[str]]:
    """
    Verifies input PIN with rate limiting and brute force protection.
    Returns: (is_valid, message, token_if_valid)
    """
    locked, remaining = is_ip_locked_out(client_ip)
    if locked:
        return False, f"Too many failed attempts. Locked out for {remaining} seconds.", None

    clean_pin = str(input_pin).strip()
    if hmac.compare_digest(clean_pin, str(ACTIVE_PIN).strip()):
        clear_failed_attempts(client_ip)
        token = make_auth_token(ACTIVE_PIN)
        return True, "Authentication successful", token
    else:
        record_failed_attempt(client_ip)
        attempts_left = MAX_FAILED_ATTEMPTS - len(_FAILED_ATTEMPTS[client_ip])
        if attempts_left <= 0:
            return False, f"Too many failed attempts. Locked out for {LOCKOUT_WINDOW_SECONDS} seconds.", None
        return False, f"Invalid PIN. {attempts_left} attempts remaining.", None


def is_authorized_request(handler: Any) -> bool:
    """
    Check if an incoming HTTP request is authorized:
    1. Localhost requests (127.0.0.1, ::1, localhost) are automatically authorized.
    2. Remote / Tunnel requests must provide a valid X-Access-Token header or session cookie.
    """
    client_ip = handler.client_address[0]
    if client_ip in ("127.0.0.1", "::1", "localhost"):
        return True

    # 1. Check Header
    header_token = handler.headers.get("X-Access-Token")
    if header_token and verify_auth_token(header_token):
        return True

    # 2. Check Authorization Bearer Header
    auth_header = handler.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if verify_auth_token(bearer_token):
            return True

    # 3. Check Cookie
    cookie_header = handler.headers.get("Cookie", "")
    if "antigravity_token=" in cookie_header:
        for cookie in cookie_header.split(";"):
            cookie = cookie.strip()
            if cookie.startswith("antigravity_token="):
                token_val = cookie.split("=", 1)[1]
                if verify_auth_token(token_val):
                    return True

    return False
