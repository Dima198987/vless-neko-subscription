
#!/usr/bin/env python3
"""Collect VLESS links, test them with sing-box, write working_vless.txt."""

from __future__ import annotations

import json
import os
import re
import shutil
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
MAX_NODES = max(1, int(os.getenv("MAX_NODES", "10")))
PAGE_TIMEOUT = max(5, int(os.getenv("PAGE_TIMEOUT", "35")))
TEST_TIMEOUT = max(5, int(os.getenv("TEST_TIMEOUT", "15")))

HTTP_TEST_URL = os.getenv(
    "HTTP_TEST_URL",
    "https://example.com/",
)

OUTPUT = Path("working_vless.txt")
SING_BOX = os.getenv("SING_BOX_BIN", "sing-box")

VLESS_RE = re.compile(r"vless://[^\s<>\"'`]+", re.IGNORECASE)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        )
    })
    return session


def fetch_page(
    session: requests.Session,
    url: str,
    description: str,
) -> requests.Response | None:
    """Fetch a page with retries."""
    for attempt in range(1, 4):
        try:
            response = session.get(
                url,
                timeout=(10, PAGE_TIMEOUT),
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            print(
                f"[WARN] {description}, attempt "
                f"{attempt}/3: {type(exc).__name__}"
            )
            if attempt < 3:
                time.sleep(attempt * 2)

    return None


def collect_uris() -> list[str]:
    """Collect VLESS URIs from key detail pages."""
    session = make_session()
    detail_pages: list[str] = []
    seen_pages: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
        response = fetch_page(session, url, f"Listing page {page}")

        if response is None:
            continue

        soup = BeautifulSoup(response.text, "html.parser")

        for link in soup.find_all("a", href=True):
            full_url = requests.compat.urljoin(url, link["href"])
            parsed = urlparse(full_url)

            if (
                parsed.netloc.lower() == "outlinekeys.com"
                and re.fullmatch(r"/key/\d+/?", parsed.path)
                and full_url not in seen_pages
            ):
                seen_pages.add(full_url)
                detail_pages.append(full_url)

        print(
            f"[INFO] Listing page {page}: "
            f"{len(detail_pages)} key pages found."
        )

    print(f"[INFO] Total key pages: {len(detail_pages)}")

    uris: list[str] = []
    seen_uris: set[str] = set()

    for index, detail_url in enumerate(detail_pages, start=1):
        if len(uris) >= MAX_NODES:
            break

        response = fetch_page(
            session,
            detail_url,
            f"Detail page {index}",
        )

        if response is None:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        chunks = [response.text, soup.get_text(" ", strip=True)]

        for tag in soup.find_all(True):
            for attr in (
                "href",
                "data-clipboard-text",
                "data-copy",
                "value",
            ):
                value = tag.get(attr)
                if isinstance(value, str):
                    chunks.append(value)

        for chunk in chunks:
            for match in VLESS_RE.findall(chunk):
                uri = match.rstrip(".,;)]}").replace("&amp;", "&")

                if uri not in seen_uris:
                    seen_uris.add(uri)
                    uris.append(uri)

                    if len(uris) >= MAX_NODES:
                        break

            if len(uris) >= MAX_NODES:
                break

        if index % 10 == 0:
            print(
                f"[INFO] Checked {index} detail pages; "
                f"found {len(uris)} URI(s)."
            )

    print(f"[INFO] Found {len(uris)} unique URI(s).")
    return uris[:MAX_NODES]


def to_singbox_outbound(uri: str) -> dict | None:
    """Convert a VLESS URI into a sing-box outbound."""
    try:
        parsed = urlparse(uri)

        if (
            parsed.scheme.lower() != "vless"
            or not parsed.hostname
            or not parsed.username
            or not parsed.port
        ):
            print("[DEBUG] Invalid VLESS URI structure.")
            return None

        params = {
            key.lower(): values[-1]
            for key, values in parse_qs(
                parsed.query,
                keep_blank_values=True,
            ).items()
        }

        outbound: dict = {
            "type": "vless",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port,
            "uuid": unquote(parsed.username),
        }

        flow = params.get("flow", "").strip()
        if flow:
            outbound["flow"] = flow

        security = params.get("security", "").lower()
        sni = (
            params.get("sni")
            or params.get("servername")
            or params.get("host")
        )
        fingerprint = params.get("fp") or params.get("fingerprint")

        if security in ("tls", "reality"):
            tls: dict = {"enabled": True}

            if sni:
                tls["server_name"] = sni

            if fingerprint:
                tls["utls"] = {
                    "enabled": True,
                    "fingerprint": fingerprint,
                }

            if security == "reality":
                public_key = params.get("pbk") or params.get("publickey")
                short_id = params.get("sid") or params.get("shortid", "")

                if not public_key:
                    print("[DEBUG] Reality public key is missing.")
                    return None

                tls["reality"] = {
                    "enabled": True,
                    "public_key": public_key,
                }

                if short_id:
                    tls["reality"]["short_id"] = short_id

            outbound["tls"] = tls

        elif security not in ("", "none"):
            print("[DEBUG] Unsupported security type.")
            return None

        transport = params.get("type", "tcp").lower()
        path = params.get("path", "")
        host = params.get("host", "")

        if transport == "ws":
            item: dict = {"type": "ws"}

            if path:
                item["path"] = path

            if host:
                item["headers"] = {"Host": host}

            outbound["transport"] = item

        elif transport == "grpc":
            item = {"type": "grpc"}
            service_name = (
                params.get("servicename")
                or params.get("service_name")
            )

            if service_name:
                item["service_name"] = service_name

            outbound["transport"] = item

        elif transport in ("tcp", "raw"):
            header = params.get("header", "none").lower()

            if header not in ("", "none"):
                print("[DEBUG] Unsupported TCP header mode.")
                return None

        elif transport in ("httpupgrade", "http-upgrade"):
            item = {"type": "httpupgrade"}

            if path:
                item["path"] = path

            if host:
                item["host"] = host

            outbound["transport"] = item

        else:
            print("[DEBUG] Unsupported transport type.")
            return None

        return outbound

    except (ValueError, TypeError, KeyError):
        print("[DEBUG] Could not parse VLESS URI.")
        return None


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_uri(uri: str) -> bool:
    """Test a VLESS profile without printing the URI."""
    outbound = to_singbox_outbound(uri)

    if outbound is None:
        return False

    port = available_port()

    config = {
        "log": {"level": "debug"},
        "inbounds": [{
            "type": "socks",
            "tag": "local-socks",
            "listen": "127.0.0.1",
            "listen_port": port,
        }],
        "outbounds": [
            outbound,
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"final": "proxy"},
    }

    with tempfile.TemporaryDirectory(prefix="vless-check-") as tmp:
        config_path = Path(tmp) / "config.json"
        log_path = Path(tmp) / "sing-box.log"

        config_path.write_text(
            json.dumps(config),
            encoding="utf-8",
        )

        try:
            check = subprocess.run(
                [SING_BOX, "check", "-c", str(config_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

            if check.returncode != 0:
                message = (check.stderr or check.stdout).strip()
                print(
                    "[DEBUG] Config validation failed: "
                    + (message[:600] or "no diagnostic output")
                )
                return False

            with log_path.open("w", encoding="utf-8") as log_file:
                proc = subprocess.Popen(
                    [SING_BOX, "run", "-c", str(config_path)],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

                try:
                    deadline = time.monotonic() + 10
                    ready = False

                    while time.monotonic() < deadline:
                        if proc.poll() is not None:
                            print(
                                "[DEBUG] sing-box exited early "
                                f"(code {proc.returncode})."
                            )
                            break

                        try:
                            with socket.create_connection(
                                ("127.0.0.1", port),
                                timeout=0.5,
                            ):
                                ready = True
                                break
                        except OSError:
                            time.sleep(0.2)

                    if not ready:
                        print("[DEBUG] SOCKS listener did not start.")
                        return False

                    result = subprocess.run(
                        [
                            "curl",
                            "--silent",
                            "--show-error",
                            "--fail",
                            "--max-time",
                            str(TEST_TIMEOUT),
                            "--proxy",
                            f"socks5h://127.0.0.1:{port}",
                            HTTP_TEST_URL,
                            "--output",
                            os.devnull,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=TEST_TIMEOUT + 5,
                        check=False,
                    )

                    if result.returncode != 0:
                        message = (
                            result.stderr.strip()
                            or f"curl exit code {result.returncode}"
                        )

                        print(
                            f"[DEBUG] HTTP test failed "
                            f"(curl exit={result.returncode}): "
                            f"{message[:300]}"
                        )

                        log_file.flush()

                        try:
                            log_text = log_path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            )
                            if log_text.strip():
                                print(
                                    "[DEBUG] sing-box log:\n"
                                    + log_text[-2500:]
                                )
                        except OSError:
                            pass

                        return False

                    print("[DEBUG] HTTP connectivity test passed.")
                    return True

                finally:
                    if proc.poll() is None:
                        proc.terminate()

                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2)

        except subprocess.TimeoutExpired:
            print("[DEBUG] A subprocess timed out.")
            return False
        except OSError as exc:
            print(f"[DEBUG] Test exception: {type(exc).__name__}")
            return False


def main() -> None:
    if not shutil.which(SING_BOX):
        raise SystemExit("sing-box binary not found in PATH")

    if not shutil.which("curl"):
        raise SystemExit("curl binary not found in PATH")

    candidates = collect_uris()
    working: list[str] = []

    for index, uri in enumerate(candidates, start=1):
        passed = test_uri(uri)

        print(
            f"[INFO] Tested {index}/{len(candidates)}: "
            f"{'PASS' if passed else 'FAIL'}"
        )

        if passed:
            working.append(uri)

    OUTPUT.write_text(
        "\n".join(working) + ("\n" if working else ""),
        encoding="utf-8",
    )

    print(
        f"[INFO] Wrote {len(working)} working profile(s) "
        f"to {OUTPUT}."
    )


if __name__ == "__main__":
    main()
