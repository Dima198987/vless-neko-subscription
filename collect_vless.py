```python
import html
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


# ---------- Settings ----------

BASE_URL = "https://outlinekeys.com/protocols/vless/"
OUTPUT_FILE = Path("working_vless.txt")

HTTP_TEST_URL = os.getenv("HTTP_TEST_URL", "https://example.com/").strip()
MAX_NODES = max(1, int(os.getenv("MAX_NODES", "10")))
MAX_LIST_PAGES = max(1, int(os.getenv("MAX_LIST_PAGES", "5")))

PAGE_TIMEOUT = 35
TEST_TIMEOUT = max(5, int(os.getenv("TEST_TIMEOUT", "18")))
RETRIES = 3

SING_BOX = os.getenv("SING_BOX_BIN", "sing-box")
CURL = shutil.which("curl")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}

VLESS_RE = re.compile(r"vless://[^\s\"'<>]+", re.IGNORECASE)


# ---------- HTTP helpers ----------

def fetch_page(url):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=PAGE_TIMEOUT,
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            print(
                f"[WARN] Request attempt {attempt}/{RETRIES} failed "
                f"for {url}: {type(exc).__name__}"
            )
            time.sleep(attempt)

    print(f"[WARN] Could not fetch page: {url}")
    if last_error:
        print(f"[WARN] Last error: {last_error}")

    return None


def find_key_pages(page_html, page_url):
    soup = BeautifulSoup(page_html, "html.parser")
    found = set()

    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        absolute = urljoin(page_url, href)
        parsed = urlsplit(absolute)

        if parsed.netloc.lower() != "outlinekeys.com":
            continue

        if re.search(r"/key/[^/]+/?$", parsed.path):
            found.add(absolute.split("#", 1)[0])

    return found


def collect_detail_pages():
    all_pages = set()

    for page_number in range(1, MAX_LIST_PAGES + 1):
        listing_url = f"{BASE_URL}?page={page_number}"
        page_html = fetch_page(listing_url)

        if not page_html:
            continue

        page_keys = find_key_pages(page_html, listing_url)
        all_pages.update(page_keys)

        print(
            f"[INFO] Listing page {page_number}: "
            f"{len(all_pages)} key pages found."
        )

        if not page_keys:
            print(
                "[INFO] No key pages found on this listing page; "
                "stopping pagination."
            )
            break

    return sorted(all_pages)


def extract_uris(detail_html):
    decoded = html.unescape(detail_html)
    soup = BeautifulSoup(decoded, "html.parser")

    text_parts = [decoded, soup.get_text(" ", strip=True)]

    for element in soup.select("[href], [data-clipboard-text]"):
        for attr in ("href", "data-clipboard-text"):
            value = element.get(attr)
            if value:
                text_parts.append(html.unescape(value))

    found = set()

    for text in text_parts:
        for match in VLESS_RE.findall(text):
            uri = match.rstrip(".,;)]}")
            if uri.lower().startswith("vless://"):
                found.add(uri)

    return found


def collect_vless_uris(detail_pages):
    uris = set()

    for index, detail_url in enumerate(detail_pages, start=1):
        page_html = fetch_page(detail_url)

        if page_html:
            uris.update(extract_uris(page_html))

        if index % 10 == 0 or index == len(detail_pages):
            print(
                f"[INFO] Checked {index} detail pages; "
                f"found {len(uris)} URI(s)."
            )

    return sorted(uris)


# ---------- VLESS URI -> sing-box ----------

def first_param(params, *names, default=""):
    for name in names:
        values = params.get(name)
        if values and values[0] != "":
            return values[0]
    return default


def to_singbox_outbound(uri):
    try:
        parsed = urlsplit(uri)
        if parsed.scheme.lower() != "vless":
            raise ValueError("URI scheme is not vless")

        if not parsed.hostname or not parsed.port:
            raise ValueError("URI must contain a server hostname and port")

        uuid = unquote(parsed.username or "")
        if not uuid:
            raise ValueError("URI does not contain a UUID")

        params = parse_qs(parsed.query, keep_blank_values=True)

        security = first_param(params, "security", default="none").lower()
        transport_type = first_param(
            params, "type", "network", default="tcp"
        ).lower()

        outbound = {
            "type": "vless",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port,
            "uuid": uuid,
        }

        flow = first_param(params, "flow")
        if flow:
            outbound["flow"] = flow

        if security in ("tls", "reality"):
            server_name = first_param(
                params, "sni", "serverName", "servername",
                default=parsed.hostname,
            )

            tls = {
                "enabled": True,
                "server_name": server_name,
            }

            alpn = first_param(params, "alpn")
            if alpn:
                tls["alpn"] = [
                    item.strip() for item in alpn.split(",") if item.strip()
                ]

            fingerprint = first_param(params, "fp", "fingerprint")
            if fingerprint:
                tls["utls"] = {
                    "enabled": True,
                    "fingerprint": fingerprint,
                }

            if security == "reality":
                public_key = first_param(params, "pbk", "publicKey")
                short_id = first_param(params, "sid", "shortId")

                if not public_key:
                    raise ValueError(
                        "Reality URI is missing its public key (pbk)"
                    )

                reality = {
                    "enabled": True,
                    "public_key": public_key,
                }
                if short_id:
                    reality["short_id"] = short_id

                tls["reality"] = reality

            outbound["tls"] = tls

        elif security not in ("", "none"):
            raise ValueError(f"Unsupported security type: {security}")

        if transport_type in ("tcp", "raw", "none", ""):
            pass

        elif transport_type == "ws":
            transport = {"type": "ws"}

            path = first_param(params, "path")
            if path:
                transport["path"] = unquote(path)

            host = first_param(params, "host")
            if host:
                transport["headers"] = {"Host": host}

            outbound["transport"] = transport

        elif transport_type == "grpc":
            transport = {"type": "grpc"}
            service_name = first_param(
                params, "serviceName", "service_name"
            )
            if service_name:
                transport["service_name"] = service_name

            outbound["transport"] = transport

        elif transport_type == "httpupgrade":
            transport = {"type": "httpupgrade"}

            path = first_param(params, "path")
            if path:
                transport["path"] = unquote(path)

            host = first_param(params, "host")
            if host:
                transport["headers"] = {"Host": host}

            outbound["transport"] = transport

        else:
            raise ValueError(
                f"Unsupported transport type: {transport_type}"
            )

        return outbound

    except (ValueError, TypeError) as exc:
        # Do not include the original URI in the error message.
        raise ValueError(str(exc)) from None


# ---------- sing-box test ----------

def reserve_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_listener(process, port, timeout=5):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False

        try:
            with socket.create_connection(
                ("127.0.0.1", port), timeout=0.25
            ):
                return True
        except OSError:
            time.sleep(0.1)

    return False


def test_uri(uri, index):
    if not CURL:
        print("[ERROR] curl is not installed.")
        return False

    try:
        outbound = to_singbox_outbound(uri)
    except ValueError as exc:
        print(f"[INFO] Node {index}: unsupported URI settings: {exc}")
        return False

    port = reserve_local_port()

    config = {
        "log": {
            "level": "debug",
            "timestamp": True,
        },
        "inbounds": [
            {
                "type": "socks",
                "tag": "local-socks",
                "listen": "127.0.0.1",
                "listen_port": port,
            }
        ],
        "outbounds": [
            outbound,
            {
                "type": "direct",
                "tag": "direct",
            },
        ],
        "route": {
            "final": "proxy",
        },
    }

    with tempfile.TemporaryDirectory(prefix="vless-test-") as temp_dir:
        config_path = Path(temp_dir) / "config.json"
        log_path = Path(temp_dir) / "sing-box.log"

        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        check = subprocess.run(
            [SING_BOX, "check", "-c", str(config_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if check.returncode != 0:
            print(f"[INFO] Node {index}: sing-box config rejected.")
            details = (check.stderr or check.stdout or "").strip()
            if details:
                print("[DEBUG] " + details[-1800:])
            return False

        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                [SING_BOX, "run", "-c", str(config_path)],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )

            try:
                if not wait_for_listener(process, port):
                    print(
                        f"[INFO] Node {index}: "
                        "sing-box did not start its SOCKS listener."
                    )
                    return False

                result = subprocess.run(
                    [
                        CURL,
                        "--silent",
                        "--show-error",
                        "--output", os.devnull,
                        "--write-out", "%{http_code}",
                        "--connect-timeout", "8",
                        "--max-time", str(TEST_TIMEOUT),
                        "--socks5-hostname", f"127.0.0.1:{port}",
                        HTTP_TEST_URL,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=TEST_TIMEOUT + 5,
                )

                status_text = (result.stdout or "").strip()
                try:
                    status = int(status_text[-3:])
                except (ValueError, IndexError):
                    status = 0

                # A real HTTP response is required. 2xx and 3xx count
                # as successful; curl exit code alone is not enough.
                if result.returncode == 0 and 200 <= status < 400:
                    print(
                        f"[INFO] Node {index}: PASS "
                        f"(HTTP {status})"
                    )
                    return True

                print(
                    f"[DEBUG] Node {index}: HTTP test failed "
                    f"(curl exit={result.returncode}, "
                    f"HTTP={status or 'none'})."
                )

                error_text = (result.stderr or "").strip()
                if error_text:
                    print("[DEBUG] curl: " + error_text[-1000:])

                return False

            except subprocess.TimeoutExpired:
                print(f"[DEBUG] Node {index}: HTTP test timed out.")
                return False

            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)

                try:
                    log_text = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    if log_text:
                        print("[DEBUG] sing-box log:")
                        print(log_text[-2500:])
                except OSError:
                    pass


# ---------- Main ----------

def main():
    print(f"[INFO] HTTP_TEST_URL={HTTP_TEST_URL}")
    print(f"[INFO] MAX_NODES={MAX_NODES}")
    print(f"[INFO] MAX_LIST_PAGES={MAX_LIST_PAGES}")

    if not shutil.which(SING_BOX):
        raise SystemExit(
            f"[ERROR] sing-box executable not found: {SING_BOX}"
        )

    if not CURL:
        raise SystemExit("[ERROR] curl executable not found.")

    detail_pages = collect_detail_pages()
    print(f"[INFO] Total key pages: {len(detail_pages)}")

    if not detail_pages:
        raise SystemExit(
            "[ERROR] No detail pages found. Check the source website."
        )

    uris = collect_vless_uris(detail_pages)
    print(f"[INFO] Found {len(uris)} unique URI(s).")

    if not uris:
        OUTPUT_FILE.write_text("", encoding="utf-8")
        raise SystemExit("[ERROR] No VLESS URIs were extracted.")

    working = []
    test_count = min(MAX_NODES, len(uris))

    for index, uri in enumerate(uris[:test_count], start=1):
        print(f"[INFO] Testing node {index}/{test_count}.")

        if test_uri(uri, index):
            working.append(uri)

    OUTPUT_FILE.write_text(
        "".join(uri + "\n" for uri in working),
        encoding="utf-8",
    )

    print(
        f"[INFO] Finished: {len(working)} working node(s) "
        f"out of {test_count} tested."
    )
    print(f"[INFO] Results saved to {OUTPUT_FILE}")

    if not working:
        print(
            "[WARN] No nodes passed the HTTP test. "
            "The output file is empty; no unverified nodes were added."
        )


if __name__ == "__main__":
    main()
```
