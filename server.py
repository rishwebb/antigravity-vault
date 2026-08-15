"""
Local & Cloud-Accessible REST API & Web Dashboard Server for Antigravity Multi-Account Analytics.
Supports Free Cloudflare HTTPS Tunnel, PIN Security, Deep Historical Scans, and Live Forex.
"""

import os
import sys
import json
import time
import hmac
import hashlib
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Dict, Any

from config import (
    SERVER_HOST,
    SERVER_PORT,
    DATABASE_PATH,
    TEMPLATES_DIR,
    DEFAULT_ACCESS_PIN,
    AUTH_SECRET_KEY,
    get_local_lan_ip,
)
import db
import telemetry_parser
import historical_scanner
import forex
import backup_engine
import tunnel
import watcher

START_TIME = time.time()

# Ensure UTF-8 stdout for Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def make_auth_token(pin: str) -> str:
    """Generate a signed HMAC session token for valid PIN entries."""
    msg = f"{pin}_{AUTH_SECRET_KEY}".encode("utf-8")
    return hmac.new(AUTH_SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def is_authorized_request(handler: SimpleHTTPRequestHandler) -> bool:
    """
    Check if request is authorized:
    1. Localhost requests (127.0.0.1, ::1) are automatically authorized.
    2. Remote / Tunnel requests must provide valid X-Access-Token header or cookie.
    """
    client_ip = handler.client_address[0]
    if client_ip in ("127.0.0.1", "::1", "localhost"):
        return True

    expected_token = make_auth_token(DEFAULT_ACCESS_PIN)

    # Check header
    header_token = handler.headers.get("X-Access-Token")
    if header_token and hmac.compare_digest(header_token, expected_token):
        return True

    # Check Cookie
    cookie_header = handler.headers.get("Cookie", "")
    if f"antigravity_token={expected_token}" in cookie_header:
        return True

    return False


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP Server for high-performance concurrent API responses."""
    daemon_threads = True
    allow_reuse_address = True


class AnalyticsAPIHandler(SimpleHTTPRequestHandler):
    """Handles REST API routes, PIN auth, Tunnel data, and serves dashboard static files."""

    def do_OPTIONS(self):
        """Enable CORS pre-flight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Access-Token")
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
        except Exception:
            pass
        return {}

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
            # 1. Root Single Page Application
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

            # 2. Auth Status check (public)
            elif path == "/api/auth/status":
                authed = is_authorized_request(self)
                is_local = self.client_address[0] in ("127.0.0.1", "::1", "localhost")
                self._send_json({
                    "authenticated": authed,
                    "is_localhost": is_local,
                    "pin_protection_active": True,
                })
                return

            # 3. Tunnel Status & QR Code (public)
            elif path == "/api/tunnel":
                tunnel_info = tunnel.get_tunnel_status()
                self._send_json(tunnel_info)
                return

            # 4. Live Forex Status (public)
            elif path == "/api/forex":
                forex_info = forex.get_forex_status()
                self._send_json(forex_info)
                return

            # 5. Backup Vault Status (public / authed)
            elif path == "/api/backup":
                b_status = backup_engine.get_backup_vault_status()
                self._send_json(b_status)
                return

            # 6. API Summary
            elif path == "/api/summary":
                stats = db.get_summary_stats(account_filter=account_id, date_range=date_range)
                self._send_json(stats)
                return

            # 7. API Accounts Breakdown (All 5 accounts)
            elif path == "/api/accounts":
                accounts = db.get_accounts_breakdown()
                self._send_json({"accounts": accounts})
                return

            # 8. API Models Breakdown
            elif path == "/api/models":
                breakdown = db.get_models_breakdown(account_filter=account_id, date_range=date_range)
                self._send_json(breakdown)
                return

            # 9. API Timeline
            elif path == "/api/timeline":
                timeline = db.get_timeline_stats(range_type=date_range, account_filter=account_id)
                self._send_json({"timeline": timeline})
                return

            # 10. API Recent Logs
            elif path == "/api/recent-logs":
                limit = int(query.get("limit", [50])[0])
                offset = int(query.get("offset", [0])[0])
                logs_data = db.get_recent_logs(
                    limit=limit,
                    offset=offset,
                    account_filter=account_id,
                    model_filter=model_filter,
                    search=search_query,
                )
                self._send_json(logs_data)
                return

            # 11. API System Status
            elif path == "/api/status":
                uptime_sec = int(time.time() - START_TIME)
                status_info = {
                    "daemon": "active",
                    "uptime_seconds": uptime_sec,
                    "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "database_size_bytes": os.path.getsize(DATABASE_PATH) if os.path.exists(DATABASE_PATH) else 0,
                    "port": SERVER_PORT,
                    "lan_ip": get_local_lan_ip(),
                    "db_integrity": db.verify_db_integrity(),
                }
                self._send_json(status_info)
                return

            else:
                self.send_error(404, f"Path {path} not found")

        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        try:
            # 1. PIN Verification
            if path == "/api/auth/verify":
                input_pin = str(body.get("pin", "")).strip()
                if input_pin == str(DEFAULT_ACCESS_PIN).strip():
                    token = make_auth_token(DEFAULT_ACCESS_PIN)
                    cookie_val = f"antigravity_token={token}; Path=/; SameSite=Lax; Max-Age=2592000"
                    self._send_json(
                        {"status": "success", "authenticated": True, "token": token},
                        extra_headers=[("Set-Cookie", cookie_val)]
                    )
                else:
                    self._send_json({"status": "error", "message": "Invalid PIN code"}, status=401)
                return

            # 2. Deep Historical Telemetry Scan Trigger
            elif path == "/api/historical-scan":
                scan_res = historical_scanner.run_deep_historical_scan(verbose=False)
                self._send_json(scan_res)
                return

            # 3. Live Sync Trigger
            elif path == "/api/sync":
                res = telemetry_parser.discover_and_sync_all()
                self._send_json({"status": "success", "sync_result": res})
                return

            # 4. Refresh Live Forex Rate Trigger
            elif path == "/api/forex/refresh":
                f_res = forex.refresh_forex_rate()
                self._send_json({"status": "success", "forex": f_res})
                return

            # 5. Create Vault Backup Snapshot Trigger
            elif path == "/api/backup/create":
                b_res = backup_engine.run_full_vault_backup()
                self._send_json(b_res)
                return

            # 6. Seed Synthetic Demo Data
            elif path == "/api/simulate-data":
                db.seed_synthetic_data()
                self._send_json({"status": "success", "message": "Synthetic telemetry seeded."})
                return

            # 7. Update Account Alias & Color
            elif path == "/api/accounts/update":
                acc_id = body.get("account_id")
                alias = body.get("alias")
                color = body.get("color")
                if not acc_id or not alias:
                    self._send_json({"error": "account_id and alias are required"}, status=400)
                    return
                db.update_account_alias(acc_id, alias, color)
                self._send_json({"status": "success", "account_id": acc_id})
                return

            # 8. Update Custom Currency Rate
            elif path == "/api/settings":
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
            self._send_json({"error": str(e)}, status=500)

    def log_message(self, format, *args):
        """Silently suppress default console access logs to prevent noise."""
        pass


def run_server(host: str = SERVER_HOST, port: int = SERVER_PORT):
    """Starts SQLite DB, historical scan, background daemons, tunnel, and server."""
    print("=" * 70)
    print("  [*] Antigravity Multi-Account Token Analytics & Cloud Vault")
    print("=" * 70)
    
    # 1. Initialize SQLite Database & Schema
    db.init_db()
    print("[+] SQLite database initialized (antigravity_telemetry.db)")

    # 2. Start Live Forex Engine
    forex.start_forex_daemon()
    current_rate = forex.get_live_usd_to_inr()
    print(f"[+] Live Forex Engine Active: 1 USD = Rs. {current_rate:.2f} INR")

    # 3. Start Background File Watcher
    watcher_thread = watcher.start_watcher()

    # 4. Start Vault Backup Daemon
    backup_engine.start_backup_daemon()

    # 5. Start Cloudflare Tunnel Daemon
    tunnel_res = tunnel.start_tunnel_daemon(port=port)
    lan_ip = get_local_lan_ip()

    # 6. Deep Historical Scan in Background Thread
    def _bg_scan():
        time.sleep(1.0)
        hist_res = historical_scanner.run_deep_historical_scan(verbose=False)
        print(f"[+] Deep scan complete: {hist_res['total_stored_turns']} lifetime turns in DB.")
    threading.Thread(target=_bg_scan, daemon=True).start()

    # 7. Start HTTP Server
    server_address = (host, port)
    httpd = ThreadedHTTPServer(server_address, AnalyticsAPIHandler)
    
    print("\n" + "=" * 70)
    print(f"  [+] Local Dashboard:   http://localhost:{port}")
    print(f"  [+] Local Wi-Fi / LAN:  http://{lan_ip}:{port}")
    print(f"  [+] Security PIN Code: {DEFAULT_ACCESS_PIN}")
    print("=" * 70 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        tunnel.stop_tunnel_daemon()
        forex.stop_forex_daemon()
        backup_engine.stop_backup_daemon()
        watcher.stop_watcher()
        httpd.shutdown()
        httpd.server_close()
        print("Server shutdown complete.")


if __name__ == "__main__":
    run_server()
