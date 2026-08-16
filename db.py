"""
SQLite Database Layer for Antigravity Multi-Account Token & Cost Analytics.
Thread-safe, WAL-enabled, with SHA256 Turn Deduplication & Explicit Estimation Metadata.
Includes Persistent Security Rate Limiting, Audit Logs, and System Error Observability Tables.
"""

import sqlite3
import json
import os
import time
import hashlib
import threading
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from config import DATABASE_PATH, DEFAULT_ACCOUNTS, DEFAULT_USD_TO_INR
from pricing_engine import calculate_turn_cost
from logger import get_logger, log_error

logger = get_logger("db")
_lock = threading.RLock()


def compute_turn_hash(
    session_id: str,
    timestamp: str,
    prompt_tokens: int,
    output_tokens: int,
    model_name: str,
    step_index: int = 0,
    prompt_preview: str = "",
) -> str:
    """Compute an immutable SHA256 hash for a specific conversation turn to prevent duplication."""
    raw = f"{session_id}_{timestamp}_{prompt_tokens}_{output_tokens}_{model_name}_{step_index}_{prompt_preview[:50]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_db_connection(db_path: str = DATABASE_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection with Row factory and WAL mode."""
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(db_path: str = DATABASE_PATH):
    """Initializes schema, migrations, and default accounts."""
    with _lock:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()

        # Accounts table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            account_alias_or_hash TEXT NOT NULL,
            email TEXT,
            first_seen TEXT,
            last_active TEXT,
            status TEXT DEFAULT 'active',
            color TEXT DEFAULT '#6366f1'
        );
        """)

        # Sessions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            thinking_level TEXT DEFAULT 'None',
            workspace_path TEXT,
            timestamp TEXT NOT NULL,
            turn_count INTEGER DEFAULT 0,
            FOREIGN KEY (account_id) REFERENCES accounts (account_id)
        );
        """)

        # Token logs table with estimation confidence and data source metadata
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS token_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn_hash TEXT UNIQUE,
            session_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            model_name TEXT NOT NULL,
            thinking_level TEXT DEFAULT 'None',
            prompt_tokens INTEGER DEFAULT 0,
            cached_tokens INTEGER DEFAULT 0,
            reasoning_thinking_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            cost_inr REAL DEFAULT 0.0,
            step_index INTEGER DEFAULT 0,
            prompt_preview TEXT,
            metadata_json TEXT,
            is_estimated INTEGER DEFAULT 1,
            estimation_confidence TEXT DEFAULT 'heuristic_char',
            data_source TEXT DEFAULT 'live_transcript',
            account_attribution_mode TEXT DEFAULT 'workspace_bucket',
            FOREIGN KEY (session_id) REFERENCES sessions (session_id),
            FOREIGN KEY (account_id) REFERENCES accounts (account_id)
        );
        """)

        # Schema Migrations: Add missing columns if upgrading existing db
        cursor.execute("PRAGMA table_info(token_logs);")
        columns = [col[1] for col in cursor.fetchall()]
        
        if "turn_hash" not in columns:
            cursor.execute("ALTER TABLE token_logs ADD COLUMN turn_hash TEXT;")
            cursor.execute("SELECT id, session_id, timestamp, prompt_tokens, output_tokens, model_name, step_index, prompt_preview FROM token_logs")
            for row in cursor.fetchall():
                th = compute_turn_hash(
                    session_id=row["session_id"],
                    timestamp=row["timestamp"],
                    prompt_tokens=row["prompt_tokens"],
                    output_tokens=row["output_tokens"],
                    model_name=row["model_name"],
                    step_index=row["step_index"],
                    prompt_preview=row["prompt_preview"] or "",
                )
                cursor.execute("UPDATE token_logs SET turn_hash = ? WHERE id = ?", (th, row["id"]))

        if "is_estimated" not in columns:
            cursor.execute("ALTER TABLE token_logs ADD COLUMN is_estimated INTEGER DEFAULT 1;")
        if "estimation_confidence" not in columns:
            cursor.execute("ALTER TABLE token_logs ADD COLUMN estimation_confidence TEXT DEFAULT 'heuristic_char';")
        if "data_source" not in columns:
            cursor.execute("ALTER TABLE token_logs ADD COLUMN data_source TEXT DEFAULT 'live_transcript';")
        if "account_attribution_mode" not in columns:
            cursor.execute("ALTER TABLE token_logs ADD COLUMN account_attribution_mode TEXT DEFAULT 'workspace_bucket';")

        # Indexes
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_turn_hash ON token_logs (turn_hash);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON token_logs (timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_account ON token_logs (account_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_model ON token_logs (model_name);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_session ON token_logs (session_id);")

        # Sync state table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            file_path TEXT PRIMARY KEY,
            file_hash TEXT,
            last_byte_offset INTEGER DEFAULT 0,
            last_mtime REAL DEFAULT 0,
            last_synced_at TEXT,
            last_error TEXT
        );
        """)
        cursor.execute("PRAGMA table_info(sync_state);")
        sync_cols = [col[1] for col in cursor.fetchall()]
        if "last_error" not in sync_cols:
            cursor.execute("ALTER TABLE sync_state ADD COLUMN last_error TEXT;")

        # Backups Audit table with checksum column
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS backups (
            backup_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER DEFAULT 0,
            record_count INTEGER DEFAULT 0,
            backup_type TEXT DEFAULT 'sqlite',
            checksum_sha256 TEXT,
            integrity_verified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        """)

        # Forex Rates Cache table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS forex_rates (
            currency TEXT PRIMARY KEY,
            rate REAL NOT NULL,
            source TEXT,
            updated_at TEXT NOT NULL
        );
        """)

        # Settings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

        # Auth Audit Logs Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            client_ip TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_audit_ts ON auth_audit_logs (timestamp);")

        # Persistent Auth Rate Limits Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_rate_limits (
            client_ip TEXT PRIMARY KEY,
            failed_attempts INTEGER DEFAULT 0,
            locked_until REAL DEFAULT 0,
            last_attempt REAL NOT NULL
        );
        """)

        # System Errors Observability Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            module TEXT NOT NULL,
            error_message TEXT NOT NULL,
            error_type TEXT,
            stack_trace TEXT
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_errors_ts ON system_errors (timestamp);")

        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('usd_to_inr', ?)", (str(DEFAULT_USD_TO_INR),))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daemon_status', 'active')")

        # Seed default 5 accounts if empty
        cursor.execute("SELECT COUNT(*) FROM accounts")
        if cursor.fetchone()[0] == 0:
            now_iso = datetime.utcnow().isoformat() + "Z"
            for acc in DEFAULT_ACCOUNTS:
                cursor.execute("""
                INSERT INTO accounts (account_id, account_alias_or_hash, email, first_seen, last_active, status, color)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    acc["account_id"],
                    acc["account_alias_or_hash"],
                    acc["email"],
                    now_iso,
                    now_iso,
                    acc["status"],
                    acc["color"],
                ))

        conn.commit()
        conn.close()


def verify_db_integrity(db_path: str = DATABASE_PATH) -> bool:
    """Run SQLite PRAGMA integrity_check."""
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()[0]
        conn.close()
        return res == "ok"
    except Exception as e:
        logger.error(f"DB integrity check failed: {e}")
        return False


def record_failed_auth_attempt_db(
    client_ip: str,
    max_attempts: int = 5,
    lockout_seconds: int = 300,
    db_path: str = DATABASE_PATH,
) -> Tuple[int, bool, int]:
    """
    Persistently records a failed authentication attempt in SQLite.
    Returns: (failed_attempts_count, is_now_locked, remaining_lockout_seconds)
    """
    now = time.time()
    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_rate_limits (
            client_ip TEXT PRIMARY KEY,
            failed_attempts INTEGER DEFAULT 0,
            locked_until REAL DEFAULT 0,
            last_attempt REAL NOT NULL
        );
        """)
        cur.execute("SELECT failed_attempts, locked_until, last_attempt FROM auth_rate_limits WHERE client_ip = ?", (client_ip,))
        row = cur.fetchone()

        if row:
            prev_attempts, locked_until, last_attempt = row["failed_attempts"], row["locked_until"], row["last_attempt"]
            # If previous lockout expired, reset attempts
            if now > locked_until and (now - last_attempt) > lockout_seconds:
                new_attempts = 1
                new_locked_until = 0
            else:
                new_attempts = prev_attempts + 1
                new_locked_until = (now + lockout_seconds) if new_attempts >= max_attempts else 0

            cur.execute("""
            UPDATE auth_rate_limits
            SET failed_attempts = ?, locked_until = ?, last_attempt = ?
            WHERE client_ip = ?
            """, (new_attempts, new_locked_until, now, client_ip))
        else:
            new_attempts = 1
            new_locked_until = (now + lockout_seconds) if new_attempts >= max_attempts else 0
            cur.execute("""
            INSERT INTO auth_rate_limits (client_ip, failed_attempts, locked_until, last_attempt)
            VALUES (?, ?, ?, ?)
            """, (client_ip, new_attempts, new_locked_until, now))

        conn.commit()
        conn.close()

        is_locked = new_attempts >= max_attempts or now < new_locked_until
        remaining = max(0, int(new_locked_until - now)) if is_locked else 0
        return new_attempts, is_locked, remaining


def check_ip_lockout_db(
    client_ip: str,
    lockout_seconds: int = 300,
    db_path: str = DATABASE_PATH,
) -> Tuple[bool, int]:
    """
    Persistently checks if an IP is currently locked out in SQLite.
    Returns: (is_locked, remaining_seconds)
    """
    now = time.time()
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_rate_limits (
            client_ip TEXT PRIMARY KEY,
            failed_attempts INTEGER DEFAULT 0,
            locked_until REAL DEFAULT 0,
            last_attempt REAL NOT NULL
        );
        """)
        cur.execute("SELECT failed_attempts, locked_until FROM auth_rate_limits WHERE client_ip = ?", (client_ip,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False, 0

        locked_until = row["locked_until"]
        if locked_until > now:
            return True, max(1, int(locked_until - now))
        return False, 0
    except Exception as e:
        logger.debug(f"Rate limit check notice: {e}")
        return False, 0


def clear_ip_rate_limit_db(client_ip: str, db_path: str = DATABASE_PATH):
    """Clears rate limit record upon successful login."""
    try:
        with _lock:
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("DELETE FROM auth_rate_limits WHERE client_ip = ?", (client_ip,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.debug(f"Rate limit clear notice: {e}")


def get_current_forex_rate(db_path: str = DATABASE_PATH) -> float:
    """Get active USD/INR rate from settings or forex cache."""
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT rate FROM forex_rates WHERE currency = 'INR'")
        row = cur.fetchone()
        if row and row[0] > 0:
            rate = float(row[0])
        else:
            cur.execute("SELECT value FROM settings WHERE key = 'usd_to_inr'")
            s_row = cur.fetchone()
            rate = float(s_row[0]) if s_row else DEFAULT_USD_TO_INR
        conn.close()
        return rate
    except Exception:
        return DEFAULT_USD_TO_INR


def upsert_account(
    account_id: str,
    alias: str,
    email: Optional[str] = None,
    color: Optional[str] = None,
    status: str = "active",
    timestamp: Optional[str] = None,
    db_path: str = DATABASE_PATH,
):
    """Upsert account record thread-safely."""
    now_iso = timestamp or (datetime.utcnow().isoformat() + "Z")
    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO accounts (account_id, account_alias_or_hash, email, first_seen, last_active, status, color)
        VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, '#6366f1'))
        ON CONFLICT(account_id) DO UPDATE SET
            last_active = excluded.last_active,
            status = 'active',
            email = COALESCE(excluded.email, accounts.email)
        """, (account_id, alias, email, now_iso, now_iso, status, color))
        conn.commit()
        conn.close()


def upsert_session(
    session_id: str,
    account_id: str,
    model_name: str,
    thinking_level: str,
    workspace_path: Optional[str],
    timestamp: str,
    db_path: str = DATABASE_PATH,
):
    """Upsert session metadata."""
    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO sessions (session_id, account_id, model_name, thinking_level, workspace_path, timestamp, turn_count)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(session_id) DO UPDATE SET
            model_name = excluded.model_name,
            thinking_level = excluded.thinking_level,
            turn_count = sessions.turn_count + 1
        """, (session_id, account_id, model_name, thinking_level, workspace_path, timestamp))
        conn.commit()
        conn.close()


def insert_token_log(log_entry: Dict[str, Any], db_path: str = DATABASE_PATH) -> Optional[int]:
    """Insert a single turn token log using immutable SHA256 turn_hash with explicit estimation tags."""
    turn_hash = log_entry.get("turn_hash")
    if not turn_hash:
        turn_hash = compute_turn_hash(
            session_id=log_entry.get("session_id", ""),
            timestamp=log_entry.get("timestamp", ""),
            prompt_tokens=log_entry.get("prompt_tokens", 0),
            output_tokens=log_entry.get("output_tokens", 0),
            model_name=log_entry.get("model_name", ""),
            step_index=log_entry.get("step_index", 0),
            prompt_preview=log_entry.get("prompt_preview", ""),
        )

    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        INSERT OR IGNORE INTO token_logs (
            turn_hash, session_id, account_id, timestamp, model_name, thinking_level,
            prompt_tokens, cached_tokens, reasoning_thinking_tokens, output_tokens,
            total_tokens, cost_usd, cost_inr, step_index, prompt_preview, metadata_json,
            is_estimated, estimation_confidence, data_source, account_attribution_mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            turn_hash,
            log_entry.get("session_id"),
            log_entry.get("account_id"),
            log_entry.get("timestamp"),
            log_entry.get("model_name"),
            log_entry.get("thinking_level", "None"),
            log_entry.get("prompt_tokens", 0),
            log_entry.get("cached_tokens", 0),
            log_entry.get("reasoning_thinking_tokens", 0),
            log_entry.get("output_tokens", 0),
            log_entry.get("total_tokens", 0),
            log_entry.get("cost_usd", 0.0),
            log_entry.get("cost_inr", 0.0),
            log_entry.get("step_index", 0),
            log_entry.get("prompt_preview", ""),
            json.dumps(log_entry.get("metadata", {})),
            1 if log_entry.get("is_estimated", True) else 0,
            log_entry.get("estimation_confidence", "heuristic_char"),
            log_entry.get("data_source", "live_transcript"),
            log_entry.get("account_attribution_mode", "workspace_bucket"),
        ))
        log_id = cur.lastrowid
        conn.commit()
        conn.close()
        return log_id


def get_sync_state(file_path: str, db_path: str = DATABASE_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve sync offset and hash for a file."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM sync_state WHERE file_path = ?", (file_path,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def update_sync_state(
    file_path: str,
    file_hash: str,
    byte_offset: int,
    mtime: float,
    last_error: Optional[str] = None,
    db_path: str = DATABASE_PATH,
):
    """Update file sync offset and hash with optional error tracking."""
    now_iso = datetime.utcnow().isoformat() + "Z"
    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO sync_state (file_path, file_hash, last_byte_offset, last_mtime, last_synced_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_hash = excluded.file_hash,
            last_byte_offset = excluded.last_byte_offset,
            last_mtime = excluded.last_mtime,
            last_synced_at = excluded.last_synced_at,
            last_error = excluded.last_error
        """, (file_path, file_hash, byte_offset, mtime, now_iso, last_error))
        conn.commit()
        conn.close()


def record_auth_audit_event(
    client_ip: str,
    endpoint: str,
    status: str,
    details: Optional[str] = None,
    db_path: str = DATABASE_PATH,
):
    """Records an authentication or authorization event for security auditing."""
    try:
        now_iso = datetime.utcnow().isoformat() + "Z"
        with _lock:
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO auth_audit_logs (timestamp, client_ip, endpoint, status, details)
            VALUES (?, ?, ?, ?, ?)
            """, (now_iso, client_ip, endpoint, status, details))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning(f"Could not record auth audit: {e}")


def get_recent_auth_audits(limit: int = 50, db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """Retrieve recent authentication audit records."""
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        SELECT * FROM auth_audit_logs ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def record_system_error_to_db(
    module: str,
    error_message: str,
    error_type: Optional[str] = None,
    stack_trace: Optional[str] = None,
    db_path: str = DATABASE_PATH,
):
    """Records a system or parsing error to SQLite for persistent observability."""
    try:
        now_iso = datetime.utcnow().isoformat() + "Z"
        with _lock:
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO system_errors (timestamp, module, error_message, error_type, stack_trace)
            VALUES (?, ?, ?, ?, ?)
            """, (now_iso, module, error_message, error_type, stack_trace))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Could not persist system error: {e}")


def get_recent_db_errors(limit: int = 50, db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """Retrieve recent recorded system errors from the database."""
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
        SELECT * FROM system_errors ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _build_filter_clause(
    account_filter: Optional[str] = None,
    date_range: Optional[str] = None,
    model_filter: Optional[str] = None,
    search: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """Helper to build SQL WHERE clause and parameters."""
    clauses = ["1=1"]
    params = []

    if account_filter and account_filter != "all":
        clauses.append("account_id = ?")
        params.append(account_filter)

    if model_filter and model_filter != "all":
        clauses.append("model_name LIKE ?")
        params.append(f"%{model_filter}%")

    if search:
        clauses.append("(prompt_preview LIKE ? OR session_id LIKE ? OR model_name LIKE ?)")
        search_pattern = f"%{search}%"
        params.extend([search_pattern, search_pattern, search_pattern])

    if date_range and date_range != "all":
        now = datetime.utcnow()
        if date_range in ("24h", "1d"):
            start_date = (now - timedelta(days=1)).isoformat() + "Z"
        elif date_range == "7d":
            start_date = (now - timedelta(days=7)).isoformat() + "Z"
        elif date_range == "30d":
            start_date = (now - timedelta(days=30)).isoformat() + "Z"
        elif date_range == "90d":
            start_date = (now - timedelta(days=90)).isoformat() + "Z"
        else:
            start_date = None

        if start_date:
            clauses.append("timestamp >= ?")
            params.append(start_date)

    return " AND ".join(clauses), params


def get_summary_stats(
    account_filter: Optional[str] = None,
    date_range: Optional[str] = None,
    db_path: str = DATABASE_PATH,
) -> Dict[str, Any]:
    """Calculate hero summary KPIs with dynamic live forex conversion."""
    where_sql, params = _build_filter_clause(account_filter=account_filter, date_range=date_range)
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    sql = f"""
    SELECT
        COUNT(*) AS total_turns,
        COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
        COALESCE(SUM(cached_tokens), 0) AS total_cached_tokens,
        COALESCE(SUM(reasoning_thinking_tokens), 0) AS total_thinking_tokens,
        COALESCE(SUM(output_tokens), 0) AS total_standard_output_tokens,
        COALESCE(SUM(output_tokens + reasoning_thinking_tokens), 0) AS total_output_and_thinking_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
        COUNT(DISTINCT session_id) AS active_sessions_count,
        COUNT(DISTINCT account_id) AS active_accounts_count
    FROM token_logs
    WHERE {where_sql}
    """
    cur.execute(sql, params)
    row = dict(cur.fetchone())

    # Dynamic Forex Rate applied to USD total
    usd_to_inr = get_current_forex_rate(db_path)
    row["usd_to_inr"] = usd_to_inr
    row["total_cost_inr"] = round(row["total_cost_usd"] * usd_to_inr, 2)

    total_out_and_think = row["total_output_and_thinking_tokens"]
    if total_out_and_think > 0:
        row["thinking_intensity_pct"] = round((row["total_thinking_tokens"] / total_out_and_think) * 100.0, 2)
    else:
        row["thinking_intensity_pct"] = 0.0

    row["total_savings_usd"] = row["total_cost_usd"]
    row["total_savings_inr"] = row["total_cost_inr"]
    row["cost_is_estimated"] = True
    row["billing_measured"] = False
    row["cost_type"] = "estimated_model"

    row["db_size_bytes"] = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    conn.close()
    return row


def get_accounts_breakdown(db_path: str = DATABASE_PATH) -> List[Dict[str, Any]]:
    """Retrieve full per-account breakdown across all 5 accounts."""
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    usd_to_inr = get_current_forex_rate(db_path)

    cur.execute("""
    SELECT
        a.account_id,
        a.account_alias_or_hash,
        a.email,
        a.first_seen,
        a.last_active,
        a.status,
        a.color,
        COUNT(t.id) AS turn_count,
        COALESCE(SUM(t.prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(t.cached_tokens), 0) AS cached_tokens,
        COALESCE(SUM(t.reasoning_thinking_tokens), 0) AS thinking_tokens,
        COALESCE(SUM(t.output_tokens), 0) AS output_tokens,
        COALESCE(SUM(t.total_tokens), 0) AS total_tokens,
        COALESCE(SUM(t.cost_usd), 0.0) AS cost_usd,
        COUNT(DISTINCT t.session_id) AS session_count
    FROM accounts a
    LEFT JOIN token_logs t ON a.account_id = t.account_id
    GROUP BY a.account_id
    ORDER BY total_tokens DESC, a.account_id ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]

    grand_total_tokens = sum(r["total_tokens"] for r in rows)
    for r in rows:
        r["cost_inr"] = round(r["cost_usd"] * usd_to_inr, 2)
        if grand_total_tokens > 0:
            r["load_pct"] = round((r["total_tokens"] / grand_total_tokens) * 100.0, 1)
        else:
            r["load_pct"] = 0.0

        out_and_think = r["output_tokens"] + r["thinking_tokens"]
        if out_and_think > 0:
            r["thinking_pct"] = round((r["thinking_tokens"] / out_and_think) * 100.0, 1)
        else:
            r["thinking_pct"] = 0.0

        r["attribution_type"] = "email_verified" if r.get("email") and "@" in r["email"] and "gemini.local" not in r["email"] else "workspace_bucket"

    conn.close()
    return rows


def get_models_breakdown(
    account_filter: Optional[str] = None,
    date_range: Optional[str] = None,
    db_path: str = DATABASE_PATH,
) -> Dict[str, Any]:
    """Retrieve model usage shares and thinking budget distribution."""
    where_sql, params = _build_filter_clause(account_filter=account_filter, date_range=date_range)
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    cur.execute(f"""
    SELECT
        model_name,
        COUNT(*) AS turns,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
        COALESCE(SUM(reasoning_thinking_tokens), 0) AS thinking_tokens
    FROM token_logs
    WHERE {where_sql}
    GROUP BY model_name
    ORDER BY total_tokens DESC
    """, params)
    model_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
    SELECT
        COALESCE(thinking_level, 'None') AS thinking_level,
        COUNT(*) AS turn_count,
        COALESCE(SUM(reasoning_thinking_tokens), 0) AS total_thinking_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(SUM(cost_usd), 0.0) AS cost_usd
    FROM token_logs
    WHERE {where_sql}
    GROUP BY thinking_level
    ORDER BY total_thinking_tokens DESC
    """, params)
    thinking_rows = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        "models": model_rows,
        "thinking_budgets": thinking_rows,
    }


def get_timeline_stats(
    range_type: str = "7d",
    account_filter: Optional[str] = None,
    db_path: str = DATABASE_PATH,
) -> List[Dict[str, Any]]:
    """Retrieve daily time-series burn rate and spend."""
    where_sql, params = _build_filter_clause(account_filter=account_filter, date_range=range_type)
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    usd_to_inr = get_current_forex_rate(db_path)

    sql = f"""
    SELECT
        SUBSTR(timestamp, 1, 10) AS date,
        COUNT(*) AS turns,
        COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
        COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
        COALESCE(SUM(output_tokens), 0) AS standard_output_tokens,
        COALESCE(SUM(reasoning_thinking_tokens), 0) AS thinking_tokens,
        COALESCE(SUM(total_tokens), 0) AS total_tokens,
        COALESCE(SUM(cost_usd), 0.0) AS cost_usd
    FROM token_logs
    WHERE {where_sql}
    GROUP BY SUBSTR(timestamp, 1, 10)
    ORDER BY date ASC
    """
    cur.execute(sql, params)
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["cost_inr"] = round(d["cost_usd"] * usd_to_inr, 2)
        rows.append(d)

    conn.close()
    return rows


def get_recent_logs(
    limit: int = 50,
    offset: int = 0,
    account_filter: Optional[str] = None,
    model_filter: Optional[str] = None,
    search: Optional[str] = None,
    sanitize_paths: bool = True,
    privacy_mode: bool = False,
    db_path: str = DATABASE_PATH,
) -> Dict[str, Any]:
    """Retrieve paginated live log feed with optional path sanitization and privacy mode."""
    where_sql, params = _build_filter_clause(
        account_filter=account_filter,
        model_filter=model_filter,
        search=search,
    )
    conn = get_db_connection(db_path)
    cur = conn.cursor()
    usd_to_inr = get_current_forex_rate(db_path)

    cur.execute(f"SELECT COUNT(*) FROM token_logs WHERE {where_sql}", params)
    total_count = cur.fetchone()[0]

    sql = f"""
    SELECT
        t.*,
        a.account_alias_or_hash,
        a.color AS account_color
    FROM token_logs t
    LEFT JOIN accounts a ON t.account_id = a.account_id
    WHERE {where_sql}
    ORDER BY t.timestamp DESC, t.id DESC
    LIMIT ? OFFSET ?
    """
    cur.execute(sql, params + [limit, offset])
    rows = []
    user_home = str(os.path.expanduser("~"))

    for r in cur.fetchall():
        d = dict(r)
        d["cost_inr"] = round(d["cost_usd"] * usd_to_inr, 4)

        # Privacy mode: Redact prompt text to abstract action tag
        if privacy_mode:
            d["prompt_preview"] = "[Redacted in Privacy Mode]"

        # Sanitize personal directory paths in metadata
        if sanitize_paths and d.get("metadata_json"):
            try:
                meta = json.loads(d["metadata_json"])
                for k, v in list(meta.items()):
                    if isinstance(v, str) and user_home in v:
                        meta[k] = v.replace(user_home, "~")
                d["metadata_json"] = json.dumps(meta)
            except Exception:
                pass

        rows.append(d)

    conn.close()
    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "logs": rows,
    }


def update_account_alias(
    account_id: str,
    alias: str,
    color: Optional[str] = None,
    db_path: str = DATABASE_PATH,
):
    """Updates account alias and color."""
    with _lock:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        if color:
            cur.execute("UPDATE accounts SET account_alias_or_hash = ?, color = ? WHERE account_id = ?", (alias, color, account_id))
        else:
            cur.execute("UPDATE accounts SET account_alias_or_hash = ? WHERE account_id = ?", (alias, account_id))
        conn.commit()
        conn.close()


def seed_synthetic_data(db_path: str = DATABASE_PATH):
    """Seed realistic multi-account Gemini 3.5 / 3.6 / 3.7 telemetry across 5 accounts."""
    import random
    now = datetime.utcnow()
    init_db(db_path)

    models_and_thinking = [
        ("Gemini 3.7 Flash (High)", "High"),
        ("Gemini 3.7 Flash (Medium)", "Medium"),
        ("Gemini 3.6 Flash (High)", "High"),
        ("Gemini 3.6 Flash (Low)", "Low"),
        ("Gemini 3.5 Pro (High)", "High"),
        ("Gemini 3.5 Pro (Medium)", "Medium"),
    ]

    sample_tasks = [
        "Implement full-stack multi-account token & cost analytics dashboard",
        "Refactor Shopify Liquid theme sections and product recommendations CTA",
        "Deep reasoning: Optimize database connection pool with WAL and busy timeout",
        "Architect background debounced filesystem watcher with asyncio",
        "Write comprehensive test suite for pricing engine and currency conversion",
        "Optimize WebGL canvas renderer and responsive CSS layout",
        "Debug async IPC socket connection and memory leak in telemetry daemon",
        "Analyze Gemini 3.7 Flash vs 3.5 Pro output token distribution",
    ]

    for day_offset in range(14, -1, -1):
        day_date = now - timedelta(days=day_offset)
        turns_for_day = random.randint(8, 25)

        for _ in range(turns_for_day):
            acc = random.choice(DEFAULT_ACCOUNTS)
            model_info, thinking_lvl = random.choice(models_and_thinking)
            
            if thinking_lvl == "High":
                think_tok = random.randint(5000, 14000)
            elif thinking_lvl == "Medium":
                think_tok = random.randint(2000, 4500)
            else:
                think_tok = random.randint(600, 1500)

            prompt_tok = random.randint(1200, 18000)
            cached_tok = random.randint(5000, 65000) if random.random() > 0.3 else 0
            out_tok = random.randint(400, 2500)

            t_tot, t_out, c_usd, c_inr = calculate_turn_cost(
                model_name=model_info,
                prompt_tokens=prompt_tok,
                cached_tokens=cached_tok,
                output_tokens=out_tok,
                reasoning_thinking_tokens=think_tok,
            )

            minute_offset = random.randint(0, 1400)
            log_time = (day_date.replace(hour=0, minute=0, second=0) + timedelta(minutes=minute_offset)).isoformat() + "Z"
            session_id = f"sess_{acc['account_id']}_{day_offset}_{random.randint(100, 999)}"

            upsert_session(
                session_id=session_id,
                account_id=acc["account_id"],
                model_name=model_info,
                thinking_level=thinking_lvl,
                workspace_path=str(os.path.expanduser("~") + f"/Projects/{acc['account_alias_or_hash'].split()[0]}"),
                timestamp=log_time,
                db_path=db_path,
            )

            task_snippet = random.choice(sample_tasks)
            insert_token_log({
                "session_id": session_id,
                "account_id": acc["account_id"],
                "timestamp": log_time,
                "model_name": model_info,
                "thinking_level": thinking_lvl,
                "prompt_tokens": prompt_tok,
                "cached_tokens": cached_tok,
                "reasoning_thinking_tokens": think_tok,
                "output_tokens": out_tok,
                "total_tokens": t_tot,
                "cost_usd": c_usd,
                "cost_inr": c_inr,
                "step_index": random.randint(1, 20),
                "prompt_preview": task_snippet,
                "metadata": {"synthetic": True, "task": task_snippet},
                "is_estimated": 1,
                "estimation_confidence": "heuristic_char",
                "data_source": "synthetic_seed",
                "account_attribution_mode": "workspace_bucket",
            }, db_path=db_path)


if __name__ == "__main__":
    init_db()
    print("Database initialized and integrity verified:", verify_db_integrity())
    print("Summary stats:", get_summary_stats())
