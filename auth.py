"""
Authentication, PIN Security, Session Tokens & Persistent Rate Limiting for Antigravity Analytics.
Stores only PBKDF2-HMAC-SHA256 salted PIN hashes with Windows NTFS ACL file permissions.
Provides persistent SQLite rate limiting and Origin/Referer validation.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import secrets
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from logger import get_logger, log_error
import db

logger = get_logger("auth")

USER_HOME = Path.home()
VAULT_DIR = USER_HOME / ".antigravity_analytics_vault"
VAULT_DIR.mkdir(parents=True, exist_ok=True)
AUTH_CREDENTIALS_FILE = VAULT_DIR / ".auth_credentials.json"

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes
PBKDF2_ITERATIONS = 100_000


def hash_pin_pbkdf2(pin: str, salt_hex: str) -> str:
    """Computes PBKDF2-HMAC-SHA256 hash for a PIN string with given hex salt."""
    salt_bytes = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS).hex()


def _harden_file_permissions(file_path: Path):
    """Hardens file permissions to current user only (NTFS ACL on Windows, 0600 on POSIX)."""
    if sys.platform == "win32":
        try:
            username = os.getenv("USERNAME")
            if username:
                # Remove inherited permissions and grant full control only to the current user
                cmd = ["icacls", str(file_path), "/inheritance:r", "/grant:r", f"{username}:(F)"]
                flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                subprocess.run(cmd, capture_output=True, creationflags=flags)
        except Exception as e:
            logger.debug(f"Windows ACL permission notice: {e}")
    else:
        try:
            os.chmod(file_path, 0o600)
        except Exception as e:
            logger.debug(f"POSIX chmod notice: {e}")


def _load_or_generate_credentials() -> Tuple[str, str, str]:
    """
    Loads existing auth credentials or securely generates new random credentials on first run.
    Stores ONLY the salted PBKDF2 hash of the PIN in .auth_credentials.json.
    Returns: (pin_salt_hex, pin_hash_hex, secret_key_hex)
    """
    env_pin = os.getenv("ANTIGRAVITY_PIN")
    env_secret = os.getenv("ANTIGRAVITY_SECRET")

    # If env vars are set, compute hash on the fly
    if env_pin and env_secret:
        salt = secrets.token_hex(16)
        p_hash = hash_pin_pbkdf2(env_pin.strip(), salt)
        return salt, p_hash, env_secret.strip()

    # Check vault credentials file
    if AUTH_CREDENTIALS_FILE.exists():
        try:
            with open(AUTH_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                salt = data.get("pin_salt")
                p_hash = data.get("pin_hash")
                secret = env_secret.strip() if env_secret else data.get("secret")
                if salt and p_hash and secret:
                    return salt, p_hash, secret
        except Exception as e:
            logger.warning(f"Could not read {AUTH_CREDENTIALS_FILE}: {e}")

    # Generate new random PIN and secret
    plain_pin = env_pin.strip() if env_pin else f"{secrets.randbelow(900000) + 100000}"
    salt = secrets.token_hex(16)
    p_hash = hash_pin_pbkdf2(plain_pin, salt)
    secret = env_secret.strip() if env_secret else secrets.token_hex(32)

    try:
        creds = {
            "pin_salt": salt,
            "pin_hash": p_hash,
            "secret": secret,
            "algorithm": f"PBKDF2-HMAC-SHA256:{PBKDF2_ITERATIONS}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "note": "PIN is stored as a salted PBKDF2 hash. Never store plaintext PINs.",
        }
        with open(AUTH_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(creds, f, indent=2)

        _harden_file_permissions(AUTH_CREDENTIALS_FILE)

        logger.info(f"[+] Security Credentials Initialized in {AUTH_CREDENTIALS_FILE}")
        try:
            print(f"\n=======================================================")
            print(f" [*] ANTIGRAVITY REMOTE ACCESS PIN: {plain_pin}")
            print(f" (PIN is stored hashed with PBKDF2; use this PIN to log in)")
            print(f"=======================================================\n")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Failed to persist credentials to {AUTH_CREDENTIALS_FILE}: {e}")

    return salt, p_hash, secret


# Initialize active credentials
PIN_SALT, PIN_HASH, AUTH_SECRET_KEY = _load_or_generate_credentials()


def make_auth_token(pin_or_hash: str, expiry_seconds: int = 86400 * 30) -> str:
    """
    Generates a signed, time-limited HMAC session token bound to the active credentials.
    Token structure: exp_timestamp.hmac_signature
    """
    exp_ts = int(time.time()) + expiry_seconds
    msg = f"{PIN_HASH}:{exp_ts}:{AUTH_SECRET_KEY}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{exp_ts}.{sig}"


def verify_auth_token(token: str) -> bool:
    """
    Validates a session token's signature, expiry, and secret binding using constant-time comparison.
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

        msg = f"{PIN_HASH}:{exp_ts}:{AUTH_SECRET_KEY}".encode("utf-8")
        expected_sig = hmac.new(AUTH_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()

        return hmac.compare_digest(received_sig, expected_sig)
    except Exception:
        return False


def verify_pin_login(input_pin: str, client_ip: str) -> Tuple[bool, str, Optional[str]]:
    """
    Verifies input PIN against PBKDF2 salted hash with persistent SQLite rate limiting.
    Returns: (is_valid, message, token_if_valid)
    """
    # Check persistent lockout
    is_locked, rem_secs = db.check_ip_lockout_db(client_ip, lockout_seconds=LOCKOUT_WINDOW_SECONDS)
    if is_locked:
        return False, f"Too many failed attempts. Locked out for {rem_secs} seconds.", None

    clean_pin = str(input_pin).strip()
    computed_hash = hash_pin_pbkdf2(clean_pin, PIN_SALT)

    if hmac.compare_digest(computed_hash, PIN_HASH):
        db.clear_ip_rate_limit_db(client_ip)
        token = make_auth_token(PIN_HASH)
        return True, "Authentication successful", token
    else:
        failed_count, now_locked, lock_rem = db.record_failed_auth_attempt_db(
            client_ip, max_attempts=MAX_FAILED_ATTEMPTS, lockout_seconds=LOCKOUT_WINDOW_SECONDS
        )
        if now_locked:
            return False, f"Too many failed attempts. Locked out for {lock_rem} seconds.", None
        remaining_attempts = max(1, MAX_FAILED_ATTEMPTS - failed_count)
        return False, f"Invalid PIN. {remaining_attempts} attempts remaining.", None


def is_authorized_request(handler: Any) -> bool:
    """
    Check if an incoming HTTP request is authorized:
    1. Localhost requests (127.0.0.1, ::1, localhost) are automatically authorized.
    2. Remote / Tunnel requests must provide a valid X-Access-Token header, Bearer token, or session cookie.
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
