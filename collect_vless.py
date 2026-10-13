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
HTTP_TEST_URL = os.getenv(
    "HTTP_TEST_URL",
    "https://www.gstatic.com/generate_204"
)

OUTPUT = Path("working_vless.txt")
SING_BOX = os.getenv("SING_BOX_BIN", "sing-box")

VLESS_RE = re.compile(
    r"vless://[^\s<>\"'`]+",
    re.IGNORECASE
)


def collect_uris() -> list[str]:
    """
    Collect VLESS links from OutlineKeys:
    1. Open protocol listing pages.
    2. Find key detail pages.
    3. Extract vless:// links from detail pages.
    """

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent":
            "Mozilla/5.0 (compatible; VlessSubscriptionUpdater/1.0)"
        }
    )

    detail_pages = []
    seen_pages = set()

    uris = []
    seen_uris = set()


    # Step 1: collect detail pages

    for page in range(1, MAX_PAGES + 1):

        url = (
            BASE_URL
            if page == 1
            else f"{BASE_URL}?page={page}"
        )

        try:
            response = session.get(
                url,
                timeout=20
            )
            response.raise_for_status()

        except requests.RequestException as exc:
            print(
                f"[WARN] Listing page {page} failed: "
                f"{type(exc).__name__}"
            )
            continue


        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        for link in soup.find_all(
            "a",
            href=True
        ):

            href = link["href"]

            full_url = requests.compat.urljoin(
                url,
                href
            )

            parsed = urlparse(full_url)


            if (
                parsed.netloc.lower()
                == "outlinekeys.com"
                and re.fullmatch(
                    r"/key/\d+/?",
                    parsed.path
                )
            ):

                if full_url not in seen_pages:

                    seen_pages.add(full_url)
                    detail_pages.append(full_url)


        print(
            f"[INFO] Page {page}: "
            f"{len(detail_pages)} key pages found."
        )


    print(
        f"[INFO] Total key pages: {len(detail_pages)}"
    )


    # Step 2: extract VLESS links

    for index, detail_url in enumerate(
        detail_pages[:MAX_NODES],
        start=1
    ):

        try:

            response = session.get(
                detail_url,
                timeout=20
            )

            response.raise_for_status()

        except requests.RequestException as exc:

            print(
                f"[WARN] Key page failed: "
                f"{type(exc).__name__}"
            )

            continue


        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        chunks = [
            response.text,
            soup.get_text(
                " ",
                strip=True
            )
        ]


        for tag in soup.find_all(True):

            for attr in (
                "href",
                "data-clipboard-text",
                "data-copy",
                "value"
            ):

                value = tag.get(attr)

                if isinstance(
                    value,
                    str
                ):

                    chunks.append(value)


        for chunk in chunks:

            for match in VLESS_RE.findall(chunk):

                uri = (
                    match
                    .rstrip(".,;)]}")
                    .replace("&amp;", "&")
                )

                if uri not in seen_uris:

                    seen_uris.add(uri)
                    uris.append(uri)


        if index % 10 == 0:

            print(
                f"[INFO] Checked {index} pages, "
                f"found {len(uris)} URI(s)."
            )


    print(
        f"[INFO] Found {len(uris)} unique URI(s)."
    )

    return uris[:MAX_NODES]



def to_singbox_outbound(uri: str) -> dict | None:

    try:

        parsed = urlparse(uri)

        if (
            parsed.scheme.lower() != "vless"
            or not parsed.hostname
            or not parsed.username
            or not parsed.port
        ):
            return None


        params = {
            key.lower(): values[-1]
            for key, values
            in parse_qs(
                parsed.query,
                keep_blank_values=True
            ).items()
        }


        outbound = {

            "type": "vless",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port,
            "uuid": unquote(parsed.username)

        }


        if params.get("flow"):

            outbound["flow"] = params["flow"]


        security = params.get(
            "security",
            ""
        ).lower()


        sni = (
            params.get("sni")
            or params.get("servername")
            or params.get("host")
        )


        fingerprint = (
            params.get("fp")
            or params.get("fingerprint")
        )


        if security in (
            "tls",
            "reality"
        ):

            tls = {
                "enabled": True
            }


            if sni:

                tls["server_name"] = sni


            if fingerprint:

                tls["utls"] = {
                    "enabled": True,
                    "fingerprint": fingerprint
                }


            if security == "reality":

                public_key = (
                    params.get("pbk")
                    or params.get("publickey")
                )


                if not public_key:

                    return None


                tls["reality"] = {
                    "enabled": True,
                    "public_key": public_key
                }


                short_id = (
                    params.get("sid")
                    or params.get("shortid")
                )


                if short_id:

                    tls["reality"]["short_id"] = short_id


            outbound["tls"] = tls


        elif security not in (
            "",
            "none"
        ):

            return None



        transport = params.get(
            "type",
            "tcp"
        ).lower()


        path = params.get(
            "path",
            ""
        )

        host = params.get(
            "host",
            ""
        )


        if transport == "ws":

            item = {
                "type": "ws"
            }

            if path:

                item["path"] = path


            if host:

                item["headers"] = {
                    "Host": host
                }


            outbound["transport"] = item


        elif transport == "grpc":

            item = {
                "type": "grpc"
            }


            service = (
                params.get("servicename")
                or params.get("service_name")
            )


            if service:

                item["service_name"] = service


            outbound["transport"] = item


        elif transport in (
            "tcp",
            "raw"
        ):

            pass


        else:

            return None


        return outbound


    except (
        ValueError,
        TypeError,
        KeyError
    ):

        return None



def available_port():

    with socket.socket() as sock:

        sock.bind(
            (
                "127.0.0.1",
                0
            )
        )

        return sock.getsockname()[1]



def test_uri(uri: str) -> bool:

    outbound = to_singbox_outbound(uri)


    if outbound is None:

        return False


    port = available_port()


    config = {

        "log": {
            "level": "error"
        },

        "inbounds": [

            {
                "type": "socks",
                "listen": "127.0.0.1",
                "listen_port": port
            }

        ],

        "outbounds": [

            outbound

        ]

    }


    with tempfile.TemporaryDirectory() as tmp:

        config_file = Path(tmp) / "config.json"

        config_file.write_text(
            json.dumps(config),
            encoding="utf-8"
        )


        try:

            check = subprocess.run(

                [
                    SING_BOX,
                    "check",
                    "-c",
                    str(config_file)
                ],

                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5

            )


            if check.returncode != 0:

                return False



            proc = subprocess.Popen(

                [
                    SING_BOX,
                    "run",
                    "-c",
                    str(config_file)
                ],

                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL

            )


            time.sleep(2)


            result = subprocess.run(

                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--max-time",
                    str(TEST_TIMEOUT),
                    "--proxy",
                    f"socks5h://127.0.0.1:{port}",
                    HTTP_TEST_URL

                ],

                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL

            )


            proc.terminate()


            return result.returncode == 0



        except Exception:

            return False



def main():

    if not shutil.which(SING_BOX):

        raise SystemExit(
            "sing-box not found"
        )


    candidates = collect_uris()


    working = []


    for index, uri in enumerate(
        candidates,
        start=1
    ):

        ok = test_uri(uri)


        print(
            f"[INFO] Tested {index}/{len(candidates)}: "
            f"{'PASS' if ok else 'FAIL'}"
        )


        if ok:

            working.append(uri)



    OUTPUT.write_text(

        "\n".join(working)
        +
        ("\n" if working else ""),

        encoding="utf-8"

    )


    print(
        f"[INFO] Wrote {len(working)} working profile(s)."
    )



if __name__ == "__main__":

    main()
