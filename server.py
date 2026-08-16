"""
Local & Cloud-Accessible REST API & Web Dashboard Server for Antigravity Multi-Account Analytics.
Provides Centralized Authorization, Brute-Force Protected PIN Login, Observability Metrics,
Audit Logging, Dynamic Forex, and Verified Backup Snapshots.
"""

import os
import sys
import json
import time
import threading
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Dict, Any, Optional

from config import (
    SERVER_HOST,
    SERVER_PORT,
    DATABASE_PATH,
    TEMPLATES_DIR,
    DEFAULT_ACCESS_PIN,
    ENABLE_TUNNEL,
    get_local_lan_ip,
)
from auth import (
    is_authorized_request,
    verify_pin_login,
    make_auth_token,
    get_active_pin,
    is_ip_locked_out,
)
import db
import telemetry_parser
import historical_scanner
import forex
import backup_engine
import tunnel
import watcher
from logger import get_logger, log_error, get_recent_system_errors

logger = get_logger("server")
START_TIME = time.time()

# Ensure UTF-8 stdout for Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP Server for concurrent API and static requests."""
    daemon_threads = True
    allow_reuse_address = True


class AnalyticsAPIHandler(SimpleHTTPRequestHandler):
    """Handles REST API routes, PIN authorization, system metrics, and serves dashboard assets."""

    def do_OPTIONS(self):
        """Enable CORS pre-flight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Access-Token, Authorization")
        self.end_headers()

    def _send_json(self, data: Any, status: int = 200, extra_headers: list = None):
        """Helper to send formatted JSON responses."""
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> Dict[str, Any]:
        """Reads and parses JSON POST request body."""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len > 0:
                body = self.rfile.read(content_len).decode("utf-8")
                return json.loads(body)
        except Exception as e:
            logger.debug(f"JSON body parse notice: {e}")
        return {}

    def _require_auth(self, path: str) -> bool:
        """Enforces authentication check. Returns True if authorized, False if rejected."""
        if is_authorized_request(self):
            return True

        client_ip = self.client_address[0]
        db.record_auth_audit_event(client_ip, path, "unauthorized", "Missing or invalid access token")
        logger.warning(f"Unauthorized API request blocked: {path} from IP {client_ip}")
        self._send_json({"error": "Unauthorized. PIN authentication required for remote access."}, status=401)
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        account_id = query.get("account_id", [None])[0]
        if account_id in ("", "all"):
            account_id = None

        date_range = query.get("range", ["all"])[0]
        model_filter = query.get("model", [None])[0]
        search_query = query.get("search", [None])[0]

        try:
            # 1. Single Page Application & Static Assets (Public)
            if path in ("/", "/index.html"):
                index_path = TEMPLATES_DIR / "index.html"
                if not index_path.exists():
                    self.send_error(404, "Dashboard template not found.")
                    return
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            # 2. Auth Status Check (Public)
            elif path == "/api/auth/status":
                authed = is_authorized_request(self)
                is_local = self.client_address[0] in ("127.0.0.1", "::1", "localhost")
                locked, remaining = is_ip_locked_out(self.client_address[0])
                self._send_json({
                    "authenticated": authed,
                    "is_localhost": is_local,
                    "pin_protection_active": True,
                    "is_locked_out": locked,
                    "lockout_remaining_seconds": remaining,
                })
                return

            # --- ALL BELOW API ROUTES REQUIRE AUTHORIZATION ---

            # 3. Tunnel Status & Pairing QR Code (Protected)
            elif path == "/api/tunnel":
                if not self._require_auth(path):
                    return
                tunnel_info = tunnel.get_tunnel_status()
                self._send_json(tunnel_info)
                return

            # 4. Live Forex Status (Protected)
            elif path == "/api/forex":
                if not self._require_auth(path):
                    return
                forex_info = forex.get_forex_status()
                self._send_json(forex_info)
                return

            # 5. Backup Vault Status (Protected)
            elif path == "/api/backup":
                if not self._require_auth(path):
                    return
                b_status = backup_engine.get_backup_vault_status()
                self._send_json(b_status)
                return

            # 6. Hero Summary Statistics (Protected)
            elif path == "/api/summary":
                if not self._require_auth(path):
                    return
                stats = db.get_summary_stats(account_filter=account_id, date_range=date_range)
                self._send_json(stats)
                return

            # 7. Accounts Breakdown Fleet (Protected)
            elif path == "/api/accounts":
                if not self._require_auth(path):
                    return
                accounts = db.get_accounts_breakdown()
                self._send_json({"accounts": accounts})
                return

            # 8. Models Breakdown & Thinking Budgets (Protected)
            elif path == "/api/models":
                if not self._require_auth(path):
                    return
                breakdown = db.get_models_breakdown(account_filter=account_id, date_range=date_range)
                self._send_json(breakdown)
                return

            # 9. Timeline Analytics (Protected)
            elif path == "/api/timeline":
                if not self._require_auth(path):
                    return
                timeline = db.get_timeline_stats(range_type=date_range, account_filter=account_id)
                self._send_json({"timeline": timeline})
                return

            # 10. Recent Logs Activity Feed with Privacy Sanitization (Protected)
            elif path == "/api/recent-logs":
                if not self._require_auth(path):
                    return
                limit = int(query.get("limit", [50])[0])
                offset = int(query.get("offset", [0])[0])
                logs_data = db.get_recent_logs(
                    limit=limit,
                    offset=offset,
                    account_filter=account_id,
                    model_filter=model_filter,
                    search=search_query,
                    sanitize_paths=True,
                )
                self._send_json(logs_data)
                return

            # 11. System Health & Observability Metrics (Protected)
            elif path == "/api/status":
                if not self._require_auth(path):
                    return
                uptime_sec = int(time.time() - START_TIME)
                status_info = {
                    "daemon": "active",
                    "uptime_seconds": uptime_sec,
                    "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "database_size_bytes": os.path.getsize(DATABASE_PATH) if os.path.exists(DATABASE_PATH) else 0,
                    "port": SERVER_PORT,
                    "bind_host": SERVER_HOST,
                    "lan_ip": get_local_lan_ip(),
                    "db_integrity": db.verify_db_integrity(),
                    "tunnel_enabled": ENABLE_TUNNEL,
                    "watcher_metrics": watcher.get_watcher_metrics(),
                }
                self._send_json(status_info)
                return

            # 12. Security Audit Logs Feed (Protected)
            elif path == "/api/status/audit-log":
                if not self._require_auth(path):
                    return
                audits = db.get_recent_auth_audits(limit=50)
                self._send_json({"audit_logs": audits})
                return

            # 13. System Error Observability Feed (Protected)
            elif path == "/api/status/errors":
                if not self._require_auth(path):
                    return
                db_errors = db.get_recent_db_errors(limit=50)
                mem_errors = get_recent_system_errors()
                self._send_json({"db_errors": db_errors, "recent_runtime_errors": mem_errors})
                return

            else:
                self.send_error(404, f"Path {path} not found")

        except Exception as e:
            log_error("server", f"GET Exception on {path}", e)
            db.record_system_error_to_db("server", f"GET error on {path}: {str(e)}", type(e).__name__)
            self._send_json({"error": "Internal Server Error", "details": str(e)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()
        client_ip = self.client_address[0]

        try:
            # 1. PIN Verification & Login (Public Endpoint with Rate Limiting)
            if path == "/api/auth/verify":
                input_pin = str(body.get("pin", "")).strip()
                is_valid, msg, token = verify_pin_login(input_pin, client_ip)

                if is_valid and token:
                    db.record_auth_audit_event(client_ip, path, "login_success")
                    cookie_val = f"antigravity_token={token}; Path=/; SameSite=Lax; HttpOnly; Max-Age=2592000"
                    self._send_json(
                        {"status": "success", "authenticated": True, "token": token, "message": msg},
                        extra_headers=[("Set-Cookie", cookie_val)]
                    )
                else:
                    db.record_auth_audit_event(client_ip, path, "login_failed", msg)
                    self._send_json({"status": "error", "message": msg}, status=401)
                return

            # --- ALL BELOW POST ROUTES REQUIRE AUTHORIZATION ---

            # 2. Deep Historical Telemetry Scan Trigger (Protected)
            elif path == "/api/historical-scan":
                if not self._require_auth(path):
                    return
                scan_res = historical_scanner.run_deep_historical_scan(verbose=False)
                self._send_json(scan_res)
                return

            # 3. Live Sync Trigger (Protected)
            elif path == "/api/sync":
                if not self._require_auth(path):
                    return
                res = telemetry_parser.discover_and_sync_all()
                self._send_json({"status": "success", "sync_result": res})
                return

            # 4. Refresh Live Forex Rate Trigger (Protected)
            elif path == "/api/forex/refresh":
                if not self._require_auth(path):
                    return
                f_res = forex.refresh_forex_rate()
                self._send_json({"status": "success", "forex": f_res})
                return

            # 5. Create Vault Backup Snapshot Trigger (Protected)
            elif path == "/api/backup/create":
                if not self._require_auth(path):
                    return
                b_res = backup_engine.run_full_vault_backup()
                self._send_json(b_res)
                return

            # 6. Verify Backup Snapshot Integrity (Protected)
            elif path == "/api/backup/verify":
                if not self._require_auth(path):
                    return
                snapshots = sorted(backup_engine.BACKUPS_DIR.glob("antigravity_backup_*.sqlite"), key=os.path.getmtime, reverse=True)
                if not snapshots:
                    self._send_json({"status": "error", "message": "No backup snapshots available to verify."}, status=404)
                    return
                latest_backup = snapshots[0]
                restorable_res = backup_engine.test_restore_dry_run(latest_backup)
                self._send_json({"backup": latest_backup.name, "restore_validation": restorable_res})
                return

            # 7. Seed Synthetic Demo Data (Protected)
            elif path == "/api/simulate-data":
                if not self._require_auth(path):
                    return
                db.seed_synthetic_data()
                self._send_json({"status": "success", "message": "Synthetic telemetry seeded."})
                return

            # 8. Update Account Alias & Color (Protected)
            elif path == "/api/accounts/update":
                if not self._require_auth(path):
                    return
                acc_id = body.get("account_id")
                alias = body.get("alias")
                color = body.get("color")
                if not acc_id or not alias:
                    self._send_json({"error": "account_id and alias are required"}, status=400)
                    return
                db.update_account_alias(acc_id, alias, color)
                self._send_json({"status": "success", "account_id": acc_id})
                return

            # 9. Update Custom Currency Rate (Protected)
            elif path == "/api/settings":
                if not self._require_auth(path):
                    return
                rate = body.get("usd_to_inr")
                if rate:
                    with db._lock:
                        conn = db.get_db_connection()
                        cur = conn.cursor()
                        cur.execute("INSERT INTO settings (key, value) VALUES ('usd_to_inr', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(rate),))
                        conn.commit()
                        conn.close()
                self._send_json({"status": "success"})
                return

            else:
                self.send_error(404, f"Path {path} not found")

        except Exception as e:
            log_error("server", f"POST Exception on {path}", e)
            db.record_system_error_to_db("server", f"POST error on {path}: {str(e)}", type(e).__name__)
            self._send_json({"error": "Internal Server Error", "details": str(e)}, status=500)

    def log_message(self, format, *args):
        """Silently suppress default console access logs to prevent noise."""
        pass


def run_server(host: str = SERVER_HOST, port: int = SERVER_PORT):
    """Starts SQLite DB, historical scan, background daemons, and HTTP server."""
    logger.info("Initializing Antigravity Multi-Account Token Analytics...")

    # 1. Initialize SQLite Database & Schema
    db.init_db()
    logger.info("[+] SQLite database initialized.")

    # 2. Start Live Forex Engine
    forex.start_forex_daemon()
    current_rate = forex.get_live_usd_to_inr()
    logger.info(f"[+] Live Forex Engine Active: 1 USD = Rs. {current_rate:.2f} INR")

    # 3. Start Background File Watcher
    watcher_thread = watcher.start_watcher()

    # 4. Start Vault Backup Daemon
    backup_engine.start_backup_daemon()

    # 5. Start Cloudflare Tunnel Daemon (if enabled)
    tunnel_res = tunnel.start_tunnel_daemon(port=port)
    lan_ip = get_local_lan_ip()

    # 6. Deep Historical Scan in Background Thread
    def _bg_scan():
        time.sleep(1.0)
        hist_res = historical_scanner.run_deep_historical_scan(verbose=False)
        logger.info(f"[+] Deep scan complete: {hist_res['total_stored_turns']} lifetime turns in DB.")
    threading.Thread(target=_bg_scan, daemon=True).start()

    # 7. Start HTTP Server
    server_address = (host, port)
    httpd = ThreadedHTTPServer(server_address, AnalyticsAPIHandler)
    
    active_pin = get_active_pin()
    print("\n" + "=" * 70)
    print(f"  [+] Local Dashboard:   http://localhost:{port}")
    print(f"  [+] Local Wi-Fi / LAN:  http://{lan_ip}:{port}")
    print(f"  [+] Security PIN Code: {active_pin}")
    print(f"  [+] Server Bind Host:  {host}")
    print(f"  [+] Cloudflare Tunnel: {'Enabled (Opt-in)' if ENABLE_TUNNEL else 'Disabled (Default Local)'}")
    print("=" * 70 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        tunnel.stop_tunnel_daemon()
        forex.stop_forex_daemon()
        backup_engine.stop_backup_daemon()
        watcher.stop_watcher()
        httpd.shutdown()
        httpd.server_close()
        logger.info("Server shutdown complete.")


if __name__ == "__main__":
    run_server()
