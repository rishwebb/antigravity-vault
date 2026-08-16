"""
Remote Cloudflare Tunnel & LAN Network Manager for Antigravity.
Provides optional public HTTPS tunneling with PIN security, local LAN pairing,
and 100% offline pure-Python SVG QR code generation (zero data leakage).
"""

import os
import sys
import re
import time
import subprocess
import threading
import urllib.request
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from config import SERVER_PORT, VAULT_DIR, get_local_lan_ip, ENABLE_TUNNEL
from qr_generator import OfflineQR
from logger import get_logger, log_error

logger = get_logger("tunnel")

CLOUDFLARED_BIN_DIR = VAULT_DIR / "bin"
CLOUDFLARED_BIN_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == "win32":
    CLOUDFLARED_EXE = CLOUDFLARED_BIN_DIR / "cloudflared.exe"
    CLOUDFLARED_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
elif sys.platform == "darwin":
    CLOUDFLARED_EXE = CLOUDFLARED_BIN_DIR / "cloudflared"
    CLOUDFLARED_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
else:
    CLOUDFLARED_EXE = CLOUDFLARED_BIN_DIR / "cloudflared"
    CLOUDFLARED_DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

_tunnel_process: Optional[subprocess.Popen] = None
_public_https_url: Optional[str] = None
_tunnel_status: str = "disabled" if not ENABLE_TUNNEL else "stopped"
_tunnel_lock = threading.Lock()
_stop_tunnel_event = threading.Event()


def find_cloudflared_binary() -> Optional[str]:
    """Finds cloudflared in system PATH or in vault bin without unsolicited downloads."""
    path_bin = shutil.which("cloudflared")
    if path_bin:
        return path_bin

    if CLOUDFLARED_EXE.exists() and CLOUDFLARED_EXE.stat().st_size > 10000:
        return str(CLOUDFLARED_EXE)

    return None


def download_cloudflared_if_permitted() -> Optional[str]:
    """Downloads standalone cloudflared binary into vault bin only when tunnel is explicitly enabled."""
    existing = find_cloudflared_binary()
    if existing:
        return existing

    if not ENABLE_TUNNEL:
        logger.info("Tunnel is disabled; skipping cloudflared binary download.")
        return None

    try:
        logger.info("[*] Downloading cloudflared standalone tunnel binary...")
        req = urllib.request.Request(
            CLOUDFLARED_DOWNLOAD_URL,
            headers={"User-Agent": "AntigravityAnalytics/2.0"},
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp, open(CLOUDFLARED_EXE, "wb") as f:
            shutil.copyfileobj(resp, f)
        
        if sys.platform != "win32":
            os.chmod(CLOUDFLARED_EXE, 0o755)

        logger.info("[+] cloudflared ready in vault.")
        return str(CLOUDFLARED_EXE)
    except Exception as e:
        log_error("tunnel", "Could not download cloudflared; falling back to LAN mode", e)
        return None


def generate_qr_svg_data_uri(text: str) -> str:
    """Generates a 100% offline SVG QR code data URI locally without third-party requests."""
    return OfflineQR.generate_data_uri(text, fg_color="#6366f1", bg_color="#0f172a")


class CloudflareTunnelThread(threading.Thread):
    """Monitors cloudflared process and parses public trycloudflare.com URL."""
    def __init__(self, port: int = SERVER_PORT):
        super().__init__(daemon=True)
        self.port = port

    def run(self):
        global _tunnel_process, _public_https_url, _tunnel_status

        if not ENABLE_TUNNEL:
            with _tunnel_lock:
                _tunnel_status = "disabled"
            logger.info("Cloudflare tunnel is disabled by configuration (ANTIGRAVITY_ENABLE_TUNNEL=false).")
            return

        bin_path = download_cloudflared_if_permitted()
        if not bin_path:
            with _tunnel_lock:
                _tunnel_status = "local_only"
            return

        cmd = [
            bin_path,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.port}",
            "--no-autoupdate",
        ]
        try:
            with _tunnel_lock:
                _tunnel_status = "starting"
                _tunnel_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )

            for line in _tunnel_process.stderr:
                if _stop_tunnel_event.is_set():
                    break
                match = re.search(r'(https://[a-zA-Z0-9-]+\.trycloudflare\.com)', line)
                if match:
                    with _tunnel_lock:
                        _public_https_url = match.group(1)
                        _tunnel_status = "online"
                    logger.warning(f"[!] Public Cloudflare Tunnel Active: {_public_https_url} (PIN Protected)")
                    print(f"\n[+] CLOUDFLARE PUBLIC HTTPS TUNNEL ONLINE: {_public_https_url} (PIN Required)\n")
                    break

            for _ in _tunnel_process.stderr:
                if _stop_tunnel_event.is_set():
                    break

        except Exception as e:
            with _tunnel_lock:
                _tunnel_status = f"error: {str(e)}"
            log_error("tunnel", "Tunnel process execution failed", e)


def start_tunnel_daemon(port: int = SERVER_PORT) -> Dict[str, Any]:
    """Start cloudflare tunnel in background asynchronously if enabled."""
    if ENABLE_TUNNEL:
        tunnel_thread = CloudflareTunnelThread(port=port)
        tunnel_thread.start()
    return get_tunnel_status()


def stop_tunnel_daemon():
    """Stops the active tunnel process."""
    global _tunnel_process, _tunnel_status, _public_https_url
    _stop_tunnel_event.set()
    with _tunnel_lock:
        if _tunnel_process:
            try:
                _tunnel_process.terminate()
                _tunnel_process.kill()
            except Exception as e:
                logger.debug(f"Tunnel kill exception: {e}")
            _tunnel_process = None
        _public_https_url = None
        _tunnel_status = "stopped"


def get_tunnel_status() -> Dict[str, Any]:
    """Retrieve active remote URL, LAN pairing QR code, and tunnel health."""
    with _tunnel_lock:
        status = _tunnel_status
        pub_url = _public_https_url

    lan_ip = get_local_lan_ip()
    local_url = f"http://{lan_ip}:{SERVER_PORT}"
    pairing_url = pub_url if pub_url else local_url
    qr_uri = generate_qr_svg_data_uri(pairing_url)

    return {
        "status": status,
        "enabled": ENABLE_TUNNEL,
        "public_tunnel_url": pub_url,
        "active_remote_url": pairing_url,
        "lan_ip": lan_ip,
        "port": SERVER_PORT,
        "local_network_url": local_url,
        "qr_code_svg_data_uri": qr_uri,
        "security_note": "PIN authentication required for all non-localhost access."
    }
