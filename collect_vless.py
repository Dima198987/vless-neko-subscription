#!/usr/bin/env python3
"""Collect VLESS URIs, test with sing-box and HTTP, write working_vless.txt."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://outlinekeys.com/protocols/vless/"
MAX_PAGES = max(1, int(os.getenv("MAX_PAGES", "5")))
MAX_NODES = max(1, int(os.getenv("MAX_NODES", "100")))
TEST_TIMEOUT = max(4, int(os.getenv("TEST_TIMEOUT", "12")))
HTTP_TEST_URL = os.getenv("HTTP_TEST_URL", "https://www.gstatic.com/generate_204")
OUTPUT = Path("working_vless.txt")
SING_BOX = os.getenv("SING_BOX_BIN", "sing-box")
VLESS_RE = re.compile(r"vless://[^\s<>\"'`]+", re.IGNORECASE)


def collect_uris() -> list[str]:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; VlessSubscriptionUpdater/1.0)"
    uris: list[str] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
        try:
            response = session.get(url, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"[WARN] Source page {page} unavailable ({type(exc).__name__}).")
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        chunks = [response.text, soup.get_text(" ", strip=True)]
        for tag in soup.find_all(True):
            for attr in ("href", "data-clipboard-text", "data-copy", "onclick"):
                value = tag.get(attr)
                if isinstance(value, str):
                    chunks.append(value)
        for chunk in chunks:
            for match in VLESS_RE.findall(chunk):
                uri = match.rstrip(".,;)]}").replace("&amp;", "&")
                if uri not in seen:
                    seen.add(uri)
                    uris.append(uri)
    print(f"[INFO] Found {len(uris)} unique URI(s).")
    return uris[:MAX_NODES]


def to_singbox_outbound(uri: str) -> dict | None:
    try:
        parsed = urlparse(uri)
        if parsed.scheme.lower() != "vless" or not parsed.hostname or not parsed.username or not parsed.port:
            return None
        params = {key.lower(): values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        outbound: dict = {
            "type": "vless", "tag": "proxy", "server": parsed.hostname,
            "server_port": parsed.port, "uuid": unquote(parsed.username),
        }
        if params.get("flow"):
            outbound["flow"] = params["flow"]

        security = params.get("security", "").lower()
        sni = params.get("sni") or params.get("servername") or params.get("host")
        fingerprint = params.get("fp") or params.get("fingerprint")
        if security in ("tls", "reality"):
            tls: dict = {"enabled": True}
            if sni:
                tls["server_name"] = sni
            if fingerprint:
                tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
            if security == "reality":
                public_key = params.get("pbk") or params.get("publickey")
                short_id = params.get("sid") or params.get("shortid", "")
                if not public_key:
                    return None
                tls["reality"] = {"enabled": True, "public_key": public_key}
                if short_id:
                    tls["reality"]["short_id"] = short_id
            outbound["tls"] = tls
        elif security not in ("", "none"):
            return None

        transport = params.get("type", "tcp").lower()
        path, host = params.get("path", ""), params.get("host", "")
        if transport == "ws":
            item: dict = {"type": "ws"}
            if path:
                item["path"] = path
            if host:
                item["headers"] = {"Host": host}
            outbound["transport"] = item
        elif transport == "grpc":
            item = {"type": "grpc"}
            service_name = params.get("servicename") or params.get("service_name")
            if service_name:
                item["service_name"] = service_name
            outbound["transport"] = item
        elif transport in ("tcp", "raw"):
            if params.get("header", "none").lower() not in ("", "none"):
                return None
        elif transport in ("httpupgrade", "http-upgrade"):
            item = {"type": "httpupgrade"}
            if path:
                item["path"] = path
            if host:
                item["host"] = host
            outbound["transport"] = item
        else:
            return None
        return outbound
    except (ValueError, TypeError, KeyError):
        return None


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_uri(uri: str) -> bool:
    outbound = to_singbox_outbound(uri)
    if outbound is None:
        return False
    port = available_port()
    config = {
        "log": {"level": "error"},
        "inbounds": [{"type": "socks", "tag": "local-socks", "listen": "127.0.0.1", "listen_port": port}],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        "route": {"final": "proxy", "auto_detect_interface": True},
    }
    with tempfile.TemporaryDirectory(prefix="vless-check-") as tmp:
        config_path = Path(tmp) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        try:
            check = subprocess.run([SING_BOX, "check", "-c", str(config_path)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5, check=False)
            if check.returncode:
                return False
            proc = subprocess.Popen([SING_BOX, "run", "-c", str(config_path)], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                deadline, ready = time.monotonic() + 4, False
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        return False
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                            ready = True
                            break
                    except OSError:
                        time.sleep(0.1)
                if not ready:
                    return False
                result = subprocess.run(
                    ["curl", "--silent", "--show-error", "--fail", "--max-time", str(TEST_TIMEOUT),
                     "--proxy", f"socks5h://127.0.0.1:{port}", HTTP_TEST_URL, "--output", os.devnull],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TEST_TIMEOUT + 3, check=False)
                return result.returncode == 0
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                        proc.wait(timeout=2)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        except (OSError, subprocess.TimeoutExpired):
            return False


def main() -> None:
    if not shutil.which(SING_BOX):
        raise SystemExit("sing-box binary not found in PATH")
    if not shutil.which("curl"):
        raise SystemExit("curl binary not found in PATH")
    candidates = collect_uris()
    working: list[str] = []
    for index, uri in enumerate(candidates, 1):
        passed = test_uri(uri)
        print(f"[INFO] Tested {index}/{len(candidates)}: {'PASS' if passed else 'FAIL'}")
        if passed:
            working.append(uri)
    OUTPUT.write_text("\n".join(working) + ("\n" if working else ""), encoding="utf-8")
    print(f"[INFO] Wrote {len(working)} working profile(s) to {OUTPUT}.")


if __name__ == "__main__":
    main()
