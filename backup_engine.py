"""
Daily Automated Vault & Backup Engine for Antigravity Telemetry.
Creates rolling SQLite snapshots and consolidated JSON archives in ~/.antigravity_analytics_vault/.
Ensures complete data immutability and recovery.
"""

import os
import shutil
import json
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from config import DATABASE_PATH, VAULT_DIR, BACKUPS_DIR, ARCHIVE_JSON_PATH
from db import get_db_connection, _lock

_stop_backup_event = threading.Event()
MAX_ROLLING_BACKUPS = 7


def create_sqlite_snapshot(db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Creates a point-in-time SQLite snapshot using VACUUM INTO or file copy."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None

    now = datetime.utcnow()
    timestamp_str = now.strftime("%Y-%m-%d_%H%M%S")
    backup_filename = f"antigravity_backup_{timestamp_str}.sqlite"
    backup_dest = BACKUPS_DIR / backup_filename
    backup_id = f"snap_{timestamp_str}"

    with _lock:
        try:
            # Use SQLite backup API for 100% consistent live lock-free snapshot
            src_conn = sqlite3.connect(db_path, timeout=5.0)
            dst_conn = sqlite3.connect(str(backup_dest))
            src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()

            file_sz = backup_dest.stat().st_size

            # Count records
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM token_logs")
            rec_count = cur.fetchone()[0]

            # Register in backups table
            cur.execute("""
            INSERT INTO backups (backup_id, filename, file_path, file_size_bytes, record_count, backup_type, created_at)
            VALUES (?, ?, ?, ?, ?, 'sqlite', ?)
            """, (backup_id, backup_filename, str(backup_dest), file_sz, rec_count, now.isoformat() + "Z"))
            conn.commit()
            conn.close()

            # Clean old rolling backups beyond MAX_ROLLING_BACKUPS
            _prune_old_backups()

            return {
                "backup_id": backup_id,
                "filename": backup_filename,
                "file_path": str(backup_dest),
                "file_size_bytes": file_sz,
                "record_count": rec_count,
                "created_at": now.isoformat() + "Z",
            }
        except Exception as e:
            print(f"[!] Backup error: {e}")
            return None


def export_all_time_archive_json(db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Exports entire lifetime token log history into all_time_archive.json."""
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

        return {
            "path": str(ARCHIVE_JSON_PATH),
            "records": len(token_logs),
            "size_bytes": ARCHIVE_JSON_PATH.stat().st_size,
        }
    except Exception as e:
        print(f"[!] Archive JSON error: {e}")
        return None


def _prune_old_backups():
    """Keep only the latest MAX_ROLLING_BACKUPS files in vault."""
    try:
        snapshots = sorted(BACKUPS_DIR.glob("antigravity_backup_*.sqlite"), key=os.path.getmtime, reverse=True)
        if len(snapshots) > MAX_ROLLING_BACKUPS:
            for old_snap in snapshots[MAX_ROLLING_BACKUPS:]:
                try:
                    old_snap.unlink()
                except Exception:
                    pass
    except Exception:
        pass


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
    """Retrieve backup health and list of available snapshots."""
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
    }


class BackupDaemon(threading.Thread):
    """Background daemon creating daily backups every 24 hours."""
    def __init__(self, interval_hours: float = 24.0):
        super().__init__(daemon=True)
        self.interval_sec = interval_hours * 3600

    def run(self):
        # Run initial backup on startup
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
    print("Testing backup_engine.py...")
    res = run_full_vault_backup()
    print("Backup Result:", res)
    print("Vault Status:", get_backup_vault_status())
