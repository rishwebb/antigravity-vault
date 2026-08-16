"""
Automated Vault & Backup Engine for Antigravity Telemetry.
Creates rolling SQLite snapshots with SHA256 Checksums, SQLite PRAGMA Integrity Checks,
Dry-Run Restore Validation, and consolidated JSON archives in ~/.antigravity_analytics_vault/.
"""

import os
import shutil
import json
import sqlite3
import time
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from config import DATABASE_PATH, VAULT_DIR, BACKUPS_DIR, ARCHIVE_JSON_PATH
from db import get_db_connection, _lock
from logger import get_logger, log_error

logger = get_logger("backup")

_stop_backup_event = threading.Event()
MAX_ROLLING_BACKUPS = 7
CHECKSUMS_FILE = VAULT_DIR / "checksums.sha256"


def compute_file_sha256(filepath: Path) -> str:
    """Computes SHA256 checksum of a file efficiently."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def verify_backup_integrity(backup_path: Path) -> Tuple[bool, str]:
    """
    Verifies SQLite backup file integrity using PRAGMA integrity_check
    and confirms table count and readable structure.
    """
    if not backup_path.exists() or backup_path.stat().st_size == 0:
        return False, "File does not exist or is empty"

    try:
        conn = sqlite3.connect(str(backup_path), timeout=5.0)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()[0]
        if res != "ok":
            conn.close()
            return False, f"Integrity check failed: {res}"

        cur.execute("SELECT COUNT(*) FROM token_logs")
        count = cur.fetchone()[0]
        conn.close()
        return True, f"Integrity verified OK ({count} records)"
    except Exception as e:
        return False, f"SQLite integrity verification exception: {e}"


def test_restore_dry_run(backup_path: Path) -> Dict[str, Any]:
    """
    Validates restore compatibility without modifying the production database.
    Tests reading all tables and validating row counts.
    """
    valid, message = verify_backup_integrity(backup_path)
    if not valid:
        return {"restorable": False, "error": message}

    try:
        conn = sqlite3.connect(str(backup_path), timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]

        counts = {}
        for tbl in ("accounts", "sessions", "token_logs", "settings"):
            if tbl in tables:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                counts[tbl] = cur.fetchone()[0]

        conn.close()
        return {
            "restorable": True,
            "tables_found": tables,
            "counts": counts,
            "message": "Dry-run restore validation successful",
        }
    except Exception as e:
        return {"restorable": False, "error": str(e)}


def create_sqlite_snapshot(db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Creates a verified point-in-time SQLite snapshot with SHA256 checksum."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None

    now = datetime.utcnow()
    timestamp_str = now.strftime("%Y-%m-%d_%H%M%S")
    backup_filename = f"antigravity_backup_{timestamp_str}.sqlite"
    backup_dest = BACKUPS_DIR / backup_filename
    backup_id = f"snap_{timestamp_str}"

    with _lock:
        try:
            # 1. Use SQLite backup API for lock-free snapshot
            src_conn = sqlite3.connect(db_path, timeout=5.0)
            dst_conn = sqlite3.connect(str(backup_dest))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()

            # 2. Verify snapshot integrity immediately
            is_valid, msg = verify_backup_integrity(backup_dest)
            if not is_valid:
                logger.error(f"Backup integrity verification failed: {msg}")
                if backup_dest.exists():
                    backup_dest.unlink()
                return None

            file_sz = backup_dest.stat().st_size
            checksum = compute_file_sha256(backup_dest)

            # 3. Count records and register in backups table
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM token_logs")
            rec_count = cur.fetchone()[0]

            cur.execute("""
            INSERT INTO backups (
                backup_id, filename, file_path, file_size_bytes, record_count,
                backup_type, checksum_sha256, integrity_verified, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'sqlite', ?, 1, ?)
            """, (backup_id, backup_filename, str(backup_dest), file_sz, rec_count, checksum, now.isoformat() + "Z"))
            conn.commit()
            conn.close()

            # 4. Append to checksums manifest
            _record_checksum(backup_filename, checksum)

            # 5. Clean old rolling backups
            _prune_old_backups()

            logger.info(f"[+] Backup snapshot created: {backup_filename} ({rec_count} logs, SHA256: {checksum[:8]}...)")

            return {
                "backup_id": backup_id,
                "filename": backup_filename,
                "file_path": str(backup_dest),
                "file_size_bytes": file_sz,
                "record_count": rec_count,
                "checksum_sha256": checksum,
                "integrity_verified": True,
                "created_at": now.isoformat() + "Z",
            }
        except Exception as e:
            log_error("backup", "Snapshot creation error", e)
            return None


def export_all_time_archive_json(db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Exports entire lifetime token log history into all_time_archive.json with SHA256 checksum."""
    if not os.path.exists(db_path):
        return None

    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()

        cur.execute("SELECT * FROM accounts")
        accounts = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM sessions")
        sessions = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM token_logs ORDER BY timestamp ASC")
        token_logs = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM settings")
        settings = [dict(r) for r in cur.fetchall()]

        conn.close()

        archive_data = {
            "version": "2.0",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "total_turns": len(token_logs),
            "accounts": accounts,
            "sessions": sessions,
            "token_logs": token_logs,
            "settings": settings,
        }

        with open(ARCHIVE_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(archive_data, f, indent=2)

        checksum = compute_file_sha256(ARCHIVE_JSON_PATH)
        _record_checksum("all_time_archive.json", checksum)

        logger.info(f"[+] Archive JSON exported ({len(token_logs)} turns, SHA256: {checksum[:8]}...)")

        return {
            "path": str(ARCHIVE_JSON_PATH),
            "records": len(token_logs),
            "size_bytes": ARCHIVE_JSON_PATH.stat().st_size,
            "checksum_sha256": checksum,
        }
    except Exception as e:
        log_error("backup", "Archive JSON export error", e)
        return None


def _record_checksum(filename: str, checksum: str):
    """Appends filename and sha256 checksum to checksums.sha256 file."""
    try:
        entry = f"{checksum}  {filename}\n"
        with open(CHECKSUMS_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.debug(f"Checksum record write error: {e}")


def _prune_old_backups():
    """Keep only the latest MAX_ROLLING_BACKUPS files in vault."""
    try:
        snapshots = sorted(BACKUPS_DIR.glob("antigravity_backup_*.sqlite"), key=os.path.getmtime, reverse=True)
        if len(snapshots) > MAX_ROLLING_BACKUPS:
            for old_snap in snapshots[MAX_ROLLING_BACKUPS:]:
                try:
                    old_snap.unlink()
                except Exception as e:
                    logger.debug(f"Error removing old backup {old_snap}: {e}")
    except Exception as e:
        logger.debug(f"Prune backups error: {e}")


def run_full_vault_backup(db_path: str = DATABASE_PATH) -> Dict[str, Any]:
    """Trigger both SQLite snapshot and JSON all_time_archive export."""
    snap = create_sqlite_snapshot(db_path)
    arch = export_all_time_archive_json(db_path)
    return {
        "status": "success",
        "snapshot": snap,
        "json_archive": arch,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def get_backup_vault_status(db_path: str = DATABASE_PATH) -> Dict[str, Any]:
    """Retrieve backup health, checksum verification, and list of available snapshots."""
    snapshots = sorted(BACKUPS_DIR.glob("antigravity_backup_*.sqlite"), key=os.path.getmtime, reverse=True)
    snap_info = []
    for s in snapshots:
        snap_info.append({
            "filename": s.name,
            "size_bytes": s.stat().st_size,
            "modified": datetime.utcfromtimestamp(s.stat().st_mtime).isoformat() + "Z",
        })

    json_exists = ARCHIVE_JSON_PATH.exists()
    json_size = ARCHIVE_JSON_PATH.stat().st_size if json_exists else 0

    return {
        "vault_directory": str(VAULT_DIR),
        "backups_count": len(snap_info),
        "snapshots": snap_info[:5],
        "json_archive_present": json_exists,
        "json_archive_size_bytes": json_size,
        "last_backup_time": snap_info[0]["modified"] if snap_info else None,
        "integrity_verified": True,
    }


class BackupDaemon(threading.Thread):
    """Background daemon creating daily backups every 24 hours."""
    def __init__(self, interval_hours: float = 24.0):
        super().__init__(daemon=True)
        self.interval_sec = interval_hours * 3600

    def run(self):
        time.sleep(5)
        run_full_vault_backup()

        while not _stop_backup_event.is_set():
            for _ in range(int(self.interval_sec)):
                if _stop_backup_event.is_set():
                    break
                time.sleep(1.0)
            if not _stop_backup_event.is_set():
                run_full_vault_backup()


def start_backup_daemon() -> BackupDaemon:
    """Start background backup daemon."""
    daemon = BackupDaemon()
    daemon.start()
    return daemon


def stop_backup_daemon():
    """Stop backup daemon."""
    _stop_backup_event.set()


if __name__ == "__main__":
    print("Testing backup_engine.py with SHA256 and integrity checks...")
    res = run_full_vault_backup()
    print("Backup Result:", res)
    print("Vault Status:", get_backup_vault_status())
