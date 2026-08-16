"""
Integration test script for verifying all active REST API endpoints, auth flow, and observability feeds.
"""

import urllib.request
import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

endpoints = [
    "/api/auth/status",
    "/api/summary",
    "/api/accounts",
    "/api/models",
    "/api/timeline?range=7d",
    "/api/recent-logs?limit=3",
    "/api/tunnel",
    "/api/forex",
    "/api/backup",
    "/api/status",
    "/api/status/audit-log",
    "/api/status/errors",
]

print("=== VERIFYING SECURED & UPGRADED REST API ENDPOINTS ===")
for ep in endpoints:
    url = f"http://127.0.0.1:4848{ep}"
    try:
        req = urllib.request.urlopen(url, timeout=5.0)
        data = json.loads(req.read().decode("utf-8"))
        print(f"[PASS] {ep} -> Status 200 OK")
        if ep == "/api/summary":
            print(f"       Total Lifetime Turns: {data.get('total_turns')}")
            print(f"       Total Tokens: {data.get('total_tokens'):,}")
            print(f"       Thinking Tokens: {data.get('total_thinking_tokens'):,}")
            print(f"       Estimated Value: ${data.get('total_cost_usd'):.2f} USD (Rs. {data.get('total_cost_inr'):.2f} INR @ Rs. {data.get('usd_to_inr'):.2f})")
        elif ep == "/api/forex":
            print(f"       Live Forex Rate: 1 USD = Rs. {data.get('rate'):.2f} INR")
        elif ep == "/api/tunnel":
            print(f"       Active Remote URL: {data.get('active_remote_url')}")
            print(f"       LAN IP: {data.get('lan_ip')}:{data.get('port')}")
        elif ep == "/api/backup":
            print(f"       Vault Backups Count: {data.get('backups_count')}, JSON Archive: {data.get('json_archive_present')}")
        elif ep == "/api/status/audit-log":
            print(f"       Audit Logs Available: {len(data.get('audit_logs', []))} entries")
        elif ep == "/api/status/errors":
            print(f"       Recent DB Errors: {len(data.get('db_errors', []))}, Memory Errors: {len(data.get('recent_runtime_errors', []))}")
    except Exception as e:
        print(f"[FAIL] {ep} -> {e}")
        sys.exit(1)

print("\n=== ALL UPGRADED ENDPOINTS VERIFIED SUCCESSFULLY ===")
