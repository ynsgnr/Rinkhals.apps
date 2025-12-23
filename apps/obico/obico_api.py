#!/usr/bin/env python3
"""Obico HTTP API for Rinkhals - Link/Relink/Status endpoints on port 7136"""

import json
import os
import re
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RINKHALS_HOME = os.environ.get("RINKHALS_HOME", "/useremain/home/rinkhals")
OBICO_CFG = f"{RINKHALS_HOME}/printer_data/config/moonraker-obico.cfg"
LINK_SH = f"{APP_ROOT}/link.sh"
API_PORT = 7136

# Global state for current link code
current_link_code: str = ""
link_process: Optional[subprocess.Popen] = None


def set_app_property(prop: str, value: str) -> None:
    try:
        subprocess.run(
            [
                "sh",
                "-c",
                f". /useremain/rinkhals/.current/tools.sh && set_app_property obico {prop} '{value}'",
            ],
            timeout=5,
            capture_output=True,
        )
    except Exception:
        pass


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


def parse_link_code(text: str) -> str:
    """Parse manual link code from link.sh output"""
    # Look for: "switch to manual linking and enter:  XXXXX"
    match = re.search(r"manual linking and enter:\s+(\w{4,6})", text)
    return match.group(1) if match else ""


def is_link_running() -> bool:
    """Check if link.sh is currently running"""
    return link_process is not None and link_process.poll() is None


def run_link_background(relink: bool = False) -> None:
    """Run link.sh in background, parse output, update state"""
    global current_link_code, link_process

    # Don't start if already running
    if is_link_running():
        print("link.sh already running, skipping")
        return

    # Clear token if relink
    if relink and os.path.exists(OBICO_CFG):
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
            cmd = [LINK_SH]
            if relink:
                cmd.append("--relink")

            link_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )

            output = ""
            for line in link_process.stdout:
                output += line
                print(f"[link.sh] {line.rstrip()}")

                # Parse link code from output
                code = parse_link_code(output)
                if code and code != current_link_code:
                    current_link_code = code
                    set_app_property("link_code", code)
                    print(f"Link code: {code}")

            link_process.wait()

            # Check if linked after process exits
            exit_code = link_process.returncode
            cfg_status = get_status()
            if cfg_status["is_linked"]:
                current_link_code = ""
                set_app_property("is_linked", "Linked")
                set_app_property("link_code", "-")
                print("Linking completed!")
            else:
                set_app_property("is_linked", "Not linked")
                if current_link_code:
                    set_app_property("link_code", current_link_code)
                print(f"Link process exited, code={exit_code}")

        except Exception as e:
            print(f"Link error: {e}")
            set_app_property("is_linked", "Error")
        finally:
            link_process = None

    threading.Thread(target=monitor_link, daemon=True).start()


def start_link_if_needed() -> None:
    """Start link process if not linked and not already running"""
    status = get_status()

    if status["is_linked"]:
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
        print("Already linked")
    elif not is_link_running():
        set_app_property("is_linked", "Not linked")
        set_app_property("link_code", "Starting...")
        print("Not linked, starting link.sh...")
        run_link_background(relink=False)


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
        if path in ("/", "/status"):
            self.send_json(get_status())
        elif path == "/health":
            self.send_json({"status": "ok"})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/link":
            already_running = is_link_running()
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
            run_link_background(relink=True)
            self.send_json({"started": True, **get_status()})
        else:
            self.send_json({"error": "Not found"}, 404)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"Obico API on port {API_PORT}: GET /status, POST /link, POST /relink")
    start_link_if_needed()
    ReusableHTTPServer(("0.0.0.0", API_PORT), APIHandler).serve_forever()
