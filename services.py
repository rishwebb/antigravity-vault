"""
Modular Domain Services and Application Context Container for Antigravity Analytics.
Decouples business logic from HTTP transport layer for high cohesion, testability, and clean architecture.
"""

import os
import sys
import json
import time
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from config import (
    SERVER_HOST,
    SERVER_PORT,
    DATABASE_PATH,
    DEFAULT_USD_TO_INR,
    ENABLE_TUNNEL,
    get_local_lan_ip,
)
from auth import (
    verify_pin_login,
    make_auth_token,
    verify_auth_token,
    is_authorized_request,
    PIN_HASH,
    AUTH_SECRET_KEY,
)
import db
import telemetry_parser
import historical_scanner
import forex
import backup_engine
import tunnel
import watcher
from qr_generator import OfflineQR
from logger import get_logger, log_error, get_recent_system_errors

logger = get_logger("services")


class AuthService:
    """Handles authentication, session tokens, brute-force rate-limiting, and origin checks."""

    @staticmethod
    def verify_login(pin: str, client_ip: str) -> Tuple[bool, str, Optional[str]]:
        return verify_pin_login(pin, client_ip)

    @staticmethod
    def is_request_authorized(handler: Any) -> bool:
        return is_authorized_request(handler)

    @staticmethod
    def validate_origin(origin_header: Optional[str], host_header: Optional[str]) -> bool:
        """Validates that a cross-origin request originates from an allowed local source."""
        if not origin_header:
            return True  # Direct non-browser or same-origin request
        origin_lower = origin_header.lower()
        allowed_prefixes = (
            "http://localhost",
            "http://127.0.0.1",
            "http://[::1]",
            "https://localhost",
            "https://127.0.0.1",
        )
        if any(origin_lower.startswith(prefix) for prefix in allowed_prefixes):
            return True
        if host_header and host_header in origin_lower:
            return True
        return False


class AnalyticsService:
    """Handles KPI aggregation, account fleet stats, model shares, and paginated logs."""

    @staticmethod
    def get_summary(account_filter: Optional[str] = None, date_range: Optional[str] = None) -> Dict[str, Any]:
        return db.get_summary_stats(account_filter=account_filter, date_range=date_range)

    @staticmethod
    def get_accounts() -> List[Dict[str, Any]]:
        return db.get_accounts_breakdown()

    @staticmethod
    def get_models(account_filter: Optional[str] = None, date_range: Optional[str] = None) -> Dict[str, Any]:
        return db.get_models_breakdown(account_filter=account_filter, date_range=date_range)

    @staticmethod
    def get_timeline(range_type: str = "7d", account_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        return db.get_timeline_stats(range_type=range_type, account_filter=account_filter)

    @staticmethod
    def get_recent_logs(
        limit: int = 50,
        offset: int = 0,
        account_filter: Optional[str] = None,
        model_filter: Optional[str] = None,
        search: Optional[str] = None,
        privacy_mode: bool = False,
    ) -> Dict[str, Any]:
        return db.get_recent_logs(
            limit=limit,
            offset=offset,
            account_filter=account_filter,
            model_filter=model_filter,
            search=search,
            sanitize_paths=True,
            privacy_mode=privacy_mode,
        )

    @staticmethod
    def update_account(account_id: str, alias: str, color: Optional[str] = None):
        db.update_account_alias(account_id, alias, color)

    @staticmethod
    def set_custom_setting(key: str, value: str):
        with db._lock:
            conn = db.get_db_connection()
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))
            conn.commit()
            conn.close()


class IngestionService:
    """Coordinates real-time sync, deep historical scans, and watcher metrics."""

    @staticmethod
    def sync_active_transcripts() -> Dict[str, Any]:
        return telemetry_parser.discover_and_sync_all()

    @staticmethod
    def run_historical_scan(verbose: bool = False) -> Dict[str, Any]:
        return historical_scanner.run_deep_historical_scan(verbose=verbose)

    @staticmethod
    def get_watcher_metrics() -> Dict[str, Any]:
        return watcher.get_watcher_metrics()


class BackupService:
    """Coordinates point-in-time snapshots, PRAGMA integrity verification, and dry-run restores."""

    @staticmethod
    def get_vault_status() -> Dict[str, Any]:
        return backup_engine.get_backup_vault_status()

    @staticmethod
    def create_backup() -> Dict[str, Any]:
        return backup_engine.run_full_vault_backup()

    @staticmethod
    def verify_latest_backup() -> Dict[str, Any]:
        snapshots = sorted(backup_engine.BACKUPS_DIR.glob("antigravity_backup_*.sqlite"), key=os.path.getmtime, reverse=True)
        if not snapshots:
            return {"status": "error", "message": "No snapshots available to verify"}
        latest = snapshots[0]
        restore_res = backup_engine.test_restore_dry_run(latest)
        return {"backup": latest.name, "restore_validation": restore_res}


class TunnelService:
    """Manages optional remote tunnels and offline vector QR code pairing."""

    @staticmethod
    def get_status() -> Dict[str, Any]:
        return tunnel.get_tunnel_status()

    @staticmethod
    def generate_offline_qr(text: str) -> str:
        return OfflineQR.generate_data_uri(text)


class AppContext:
    """Service Locator & Context Container for Antigravity Analytics."""

    auth = AuthService()
    analytics = AnalyticsService()
    ingestion = IngestionService()
    backup = BackupService()
    tunnel = TunnelService()
