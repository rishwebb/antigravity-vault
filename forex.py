"""
Dynamic Live Forex Rate Engine for Antigravity Analytics.
Fetches real-time USD/INR exchange rates from free public APIs with offline caching & fallbacks.
"""

import urllib.request
import json
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from config import DEFAULT_USD_TO_INR, FOREX_API_URLS, FOREX_UPDATE_INTERVAL_HOURS, DATABASE_PATH
from db import get_db_connection, _lock

_cached_rate: float = DEFAULT_USD_TO_INR
_last_fetched_time: Optional[str] = None
_rate_lock = threading.Lock()
_stop_forex_event = threading.Event()


def fetch_live_forex_rate() -> Tuple[float, str, str]:
    """Fetch live USD to INR rate from free endpoints."""
    for url in FOREX_API_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AntigravityAnalytics/2.0"},
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    rates = data.get("rates", {})
                    inr_rate = rates.get("INR")
                    if inr_rate and float(inr_rate) > 0:
                        return (float(inr_rate), url, datetime.utcnow().isoformat() + "Z")
        except Exception:
            continue

    return (DEFAULT_USD_TO_INR, "offline_fallback", datetime.utcnow().isoformat() + "Z")


def refresh_forex_rate(db_path: str = DATABASE_PATH) -> Dict[str, Any]:
    """Force refresh of forex rate and update database cache."""
    global _cached_rate, _last_fetched_time
    rate, source, updated_at = fetch_live_forex_rate()

    with _rate_lock:
        _cached_rate = rate
        _last_fetched_time = updated_at

    # Persist in SQLite
    try:
        with _lock:
            conn = get_db_connection(db_path)
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO forex_rates (currency, rate, source, updated_at)
            VALUES ('INR', ?, ?, ?)
            ON CONFLICT(currency) DO UPDATE SET
                rate = excluded.rate,
                source = excluded.source,
                updated_at = excluded.updated_at
            """, (rate, source, updated_at))
            cur.execute("UPDATE settings SET value = ? WHERE key = 'usd_to_inr'", (str(rate),))
            conn.commit()
            conn.close()
    except Exception:
        pass

    return {
        "currency": "INR",
        "rate": rate,
        "source": source,
        "updated_at": updated_at,
    }


def get_live_usd_to_inr(db_path: str = DATABASE_PATH) -> float:
    """Get active USD/INR rate."""
    global _cached_rate
    with _rate_lock:
        if _cached_rate > 0:
            return _cached_rate

    # Fallback to DB
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT rate FROM forex_rates WHERE currency = 'INR'")
        row = cur.fetchone()
        conn.close()
        if row and row[0] > 0:
            with _rate_lock:
                _cached_rate = float(row[0])
            return _cached_rate
    except Exception:
        pass

    return DEFAULT_USD_TO_INR


def get_forex_status(db_path: str = DATABASE_PATH) -> Dict[str, Any]:
    """Return status of forex engine."""
    rate = get_live_usd_to_inr(db_path)
    return {
        "rate": rate,
        "currency": "INR",
        "last_updated": _last_fetched_time or datetime.utcnow().isoformat() + "Z",
        "default_rate": DEFAULT_USD_TO_INR,
    }


class ForexWorker(threading.Thread):
    """Background worker updating exchange rates every 6 hours."""
    def __init__(self, interval_hours: float = FOREX_UPDATE_INTERVAL_HOURS):
        super().__init__(daemon=True)
        self.interval_sec = interval_hours * 3600

    def run(self):
        # Initial refresh
        refresh_forex_rate()
        while not _stop_forex_event.is_set():
            # Sleep in chunks
            for _ in range(int(self.interval_sec)):
                if _stop_forex_event.is_set():
                    break
                time.sleep(1.0)
            if not _stop_forex_event.is_set():
                refresh_forex_rate()


def start_forex_daemon() -> ForexWorker:
    """Start background forex daemon."""
    worker = ForexWorker()
    worker.start()
    return worker


def stop_forex_daemon():
    """Stop forex daemon."""
    _stop_forex_event.set()


if __name__ == "__main__":
    print("Testing forex.py...")
    res = refresh_forex_rate()
    print("Live Forex Rate:", res)
