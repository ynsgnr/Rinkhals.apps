#!/usr/bin/env python3
"""Obico HTTP API for Rinkhals - Link/Relink/Status endpoints on port 7136"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RINKHALS_HOME = os.environ.get("RINKHALS_HOME", "/useremain/home/rinkhals")
OBICO_CFG = f"{RINKHALS_HOME}/printer_data/config/moonraker-obico.cfg"
LINK_SH = f"{APP_ROOT}/link.sh"
LINK_OUTPUT = f"{RINKHALS_HOME}/printer_data/logs/obico-link.log"
API_PORT = 7136

# Global state for current link code
current_link_code: str = ""
link_process: Optional[subprocess.Popen] = None


def log(msg: str) -> None:
    """Print timestamped log message"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}: {msg}", flush=True)


def set_app_property(prop: str, value: str) -> None:
    try:
        result = subprocess.run(
            [
                "sh",
                "-c",
                f". /useremain/rinkhals/.current/tools.sh && set_app_property obico {prop} '{value}'",
            ],
            timeout=5,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log(f"set_app_property failed: {result.stderr}")
    except Exception as e:
        log(f"set_app_property error: {e}")


def read_config() -> Dict[str, Dict[str, str]]:
    config: Dict[str, Dict[str, str]] = {}
    if not os.path.exists(OBICO_CFG):
        return config
    section = ""
    with open(OBICO_CFG, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                config[section] = {}
            elif "=" in line and section:
                key, value = line.split("=", 1)
                config[section][key.strip()] = value.strip()
    return config


def get_status() -> Dict[str, Any]:
    config = read_config()
    server = config.get("server", {})
    is_linked = bool(server.get("auth_token", ""))
    return {
        "is_linked": is_linked,
        "link_code": "" if is_linked else current_link_code,
        "server_url": server.get("url", "https://app.obico.io"),
    }


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text"""
    # Handle both \x1b[...m and \x1b[?...h/l formats
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    # Also handle literal [0;96m etc if escape char is stripped
    text = re.sub(r"\[[\d;]*m", "", text)
    text = re.sub(r"\[\?[\d]*[hl]", "", text)
    return text


def parse_link_code(text: str) -> str:
    """Parse manual link code from link.sh output"""
    # Strip ANSI color codes first
    clean = strip_ansi(text)
    # Look for: "switch to manual linking and enter:  XXXXX"
    match = re.search(r"manual linking and enter:\s+(\w{4,6})", clean)
    return match.group(1) if match else ""


def read_link_code_from_file() -> str:
    """Read link code from link output file"""
    if not os.path.exists(LINK_OUTPUT):
        return ""
    try:
        with open(LINK_OUTPUT, "r") as f:
            return parse_link_code(f.read())
    except Exception:
        return ""


def is_link_running() -> bool:
    """Check if link.sh is currently running"""
    return link_process is not None and link_process.poll() is None


def run_link_background(relink: bool = False) -> None:
    """Run link.sh in background, parse output, update state"""
    global current_link_code, link_process

    log(f"run_link_background called (relink={relink})")

    # Don't start if already running
    if is_link_running():
        log("link.sh already running, skipping")
        return

    # Check if already linked
    status = get_status()
    if status["is_linked"] and not relink:
        log("Already linked, skipping link (use relink to re-link)")
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
        return

    # Clear token if relink (so Obico doesn't ask for confirmation)
    if relink or status["is_linked"]:
        log("Clearing auth_token for relink")
        if os.path.exists(OBICO_CFG):
            with open(OBICO_CFG, "r") as f:
                lines = [line for line in f if "auth_token" not in line]
            with open(OBICO_CFG, "w") as f:
                f.writelines(lines)

    current_link_code = ""
    set_app_property("is_linked", "Linking...")
    set_app_property("link_code", "Starting...")

    def monitor_link():
        global current_link_code, link_process
        try:
            cmd = ["sh", LINK_SH]
            if relink:
                cmd.append("--relink")

            log(f"Running: {' '.join(cmd)}")

            # Run link.sh with output to file, stdin pipe keeps process alive
            log_file = open(LINK_OUTPUT, "w")
            link_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,  # Keep stdin open (prevents EOF crash)
                stdout=log_file,
                stderr=log_file,
            )
            log(f"Popen started, PID: {link_process.pid}")

            # Poll for link code while process runs
            while link_process.poll() is None:
                time.sleep(1)
                code = read_link_code_from_file()
                if code and code != current_link_code:
                    current_link_code = code
                    set_app_property("link_code", code)
                    set_app_property("is_linked", "Use code")
                    log(f"Link code: {code}")

            exit_code = link_process.returncode
            log(f"link.sh exited with code {exit_code}")

            # Final read of link code
            code = read_link_code_from_file()
            if code:
                current_link_code = code

            # Check if linked after process exits
            cfg_status = get_status()
            log(f"Post-link status: is_linked={cfg_status['is_linked']}")

            if cfg_status["is_linked"]:
                current_link_code = ""
                set_app_property("is_linked", "Linked")
                set_app_property("link_code", "-")
                log("Linking completed!")
            elif current_link_code:
                set_app_property("is_linked", "Use code")
                set_app_property("link_code", current_link_code)
                log(f"Link code available: {current_link_code}")
            else:
                set_app_property("is_linked", "Not linked")
                log(f"Link failed, no code found")

        except Exception as e:
            log(f"Link error: {e}")
            traceback.print_exc()
            set_app_property("is_linked", "Error")
            set_app_property("link_code", "Error")
        finally:
            log("monitor_link thread exiting")
            if link_process and link_process.stdin:
                try:
                    link_process.stdin.close()
                except Exception:
                    pass
            try:
                log_file.close()
            except Exception:
                pass
            link_process = None

    log("Starting monitor_link thread")
    threading.Thread(target=monitor_link, daemon=True).start()


def start_link_if_needed() -> None:
    """Start link process if not linked and not already running"""
    log("start_link_if_needed called")
    log(f"APP_ROOT: {APP_ROOT}")
    log(f"OBICO_CFG: {OBICO_CFG}")
    log(f"LINK_SH: {LINK_SH}")

    # Ensure config exists
    if not os.path.exists(OBICO_CFG):
        set_app_property("is_linked", "No config")
        set_app_property("link_code", "Run install.sh")
        log(f"Config not found: {OBICO_CFG}")
        return

    log(f"Config exists: {OBICO_CFG}")
    status = get_status()
    log(f"Current status: {status}")

    if status["is_linked"]:
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
        log("Already linked")
    elif not is_link_running():
        set_app_property("is_linked", "Not linked")
        set_app_property("link_code", "Starting...")
        log("Not linked, starting link.sh...")
        run_link_background(relink=False)
    else:
        log("Link already running")


class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        log(f"GET {path}")
        if path in ("/", "/status"):
            status = get_status()
            log(f"Status: {status}")
            self.send_json(status)
        elif path == "/health":
            self.send_json({"status": "ok"})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        log(f"POST {path}")
        if path == "/link":
            already_running = is_link_running()
            log(f"Link requested, already_running={already_running}")
            if not already_running:
                run_link_background(relink=False)
            self.send_json(
                {
                    "started": not already_running,
                    "already_running": already_running,
                    **get_status(),
                }
            )
        elif path == "/relink":
            log("Relink requested")
            run_link_background(relink=True)
            self.send_json({"started": True, **get_status()})
        else:
            self.send_json({"error": "Not found"}, 404)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    log("=" * 50)
    log("Obico API starting")
    log(f"Python: {sys.version}")
    log(f"Port: {API_PORT}")
    log(f"Endpoints: GET /status, POST /link, POST /relink")
    log("=" * 50)
    start_link_if_needed()
    log("Starting HTTP server...")
    ReusableHTTPServer(("0.0.0.0", API_PORT), APIHandler).serve_forever()
