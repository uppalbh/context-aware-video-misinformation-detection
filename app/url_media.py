"""Narrow direct HTTPS downloader with public-address pinning and verified host TLS."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import dns.exception
import dns.resolver

from app.config import Settings
from app.errors import ProcessingError

DOWNLOAD_SECONDS = 120
IO_SECONDS = 10
MAX_REDIRECTS = 3
TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


def validate_url(url: str, hosts: tuple[str, ...]) -> str:
    try:
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise ValueError()
        url.encode("ascii")
        if any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise ValueError()
        if any(char in url for char in ("\\", "?", "#")) or re.search(r"%(?![0-9a-fA-F]{2})", url):
            raise ValueError()
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or "%" in parts.netloc
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or host not in hosts
            or not parts.path.lower().endswith((".mp4", ".mov"))
        ):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()  # literals intentionally unsupported, even if allowlisted
        return urlunsplit(("https", host, parts.path, "", ""))
    except (ValueError, UnicodeError):
        raise ProcessingError(
            "unsupported_url",
            "Use a direct HTTPS .mp4/.mov link on a supported host, without credentials, query, fragment or custom port.",
        )


def public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or any(
            (
                ip.is_private,
                ip.is_reserved,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_multicast,
                ip.is_unspecified,
            )
        ):
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            if (
                ip.ipv4_mapped
                or ip.sixtofour
                or ip.teredo
                or any(ip in net for net in TRANSITION_NETWORKS)
            ):
                return False
        return True
    except ValueError:
        return False


def resolve_public(host: str, deadline: float) -> list[str]:
    addresses = []
    resolver = dns.resolver.Resolver()
    try:
        for family in ("A", "AAAA"):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessingError("url_timeout", "Video download exceeded its time limit.")
            try:
                answers = resolver.resolve(host, family, lifetime=min(5, remaining), search=False)
            except dns.resolver.NoAnswer:
                continue
            addresses.extend(str(answer) for answer in answers)
    except dns.exception.DNSException:
        raise ProcessingError("url_dns", "Video host could not be safely resolved.")
    if not addresses or len(addresses) > 32 or not all(public_address(a) for a in addresses):
        raise ProcessingError(
            "unsafe_url", "Video host must resolve only to public internet addresses."
        )
    return list(dict.fromkeys(addresses))


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a vetted numeric address; never perform hostname resolution in connect()."""

    def __init__(self, host: str, addresses: list[str], deadline: float):
        self.addresses, self.deadline = addresses, deadline
        self.wire_socket = None
        super().__init__(host, port=443, timeout=IO_SECONDS, context=ssl.create_default_context())

    def connect(self):
        # A single pinned target per attempt avoids an unbounded fallback chain.
        address = self.addresses[0]
        if not public_address(address):
            raise ProcessingError("unsafe_url", "Blocked a non-public connection target.")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessingError("url_timeout", "Video download exceeded its time limit.")
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(min(IO_SECONDS, remaining))
            target = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
            self.wire_socket = sock
            sock.connect(target)
            self.sock = self._context.wrap_socket(
                sock, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.wire_socket = self.sock
            self.sock.do_handshake()
        except BaseException:
            sock.close()
            raise

    def abort(self):
        # shutdown also interrupts readers holding makefile references after HTTPConnection closes.
        if self.wire_socket is not None:
            try:
                self.wire_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.wire_socket.close()


def download(url: str, output: Path, cfg: Settings) -> dict:
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    partial = output.with_name(output.name + ".part")
    current = validate_url(url, cfg.url_hosts)
    active = [None]
    timed_out = threading.Event()

    def cancel_transport():
        timed_out.set()
        if active[0] is not None:
            active[0].abort()

    watchdog = threading.Timer(DOWNLOAD_SECONDS, cancel_transport)
    watchdog.daemon = True
    watchdog.start()
    try:
        for redirect in range(MAX_REDIRECTS + 1):
            if timed_out.is_set():
                raise ProcessingError("url_timeout", "Video download exceeded its time limit.")
            current = validate_url(current, cfg.url_hosts)
            parsed = urlsplit(current)
            addresses = resolve_public(parsed.hostname, deadline)
            connection = PinnedHTTPSConnection(parsed.hostname, addresses, deadline)
            active[0] = connection
            try:
                # No proxies, cookies, authorization, user headers, referer, or automatic redirects.
                connection.request(
                    "GET",
                    parsed.path,
                    headers={
                        "Accept-Encoding": "identity",
                        "User-Agent": "ClipContext/0.1",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or redirect == MAX_REDIRECTS:
                        raise ProcessingError(
                            "url_redirect", "Video link exceeded the redirect limit."
                        )
                    current = validate_url(urljoin(current, location), cfg.url_hosts)
                    continue
                if response.status != 200:
                    raise ProcessingError(
                        "url_http", "Video host did not return a complete public file."
                    )
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ProcessingError(
                        "url_encoding", "Compressed HTTP downloads are unsupported."
                    )
                if response.getheader("Transfer-Encoding", "").lower() not in ("", "chunked"):
                    raise ProcessingError("url_encoding", "Unsupported HTTP transfer encoding.")
                declared = response.getheader("Content-Length")
                if declared is not None and response.getheader("Transfer-Encoding"):
                    raise ProcessingError(
                        "url_length", "Ambiguous download framing is unsupported."
                    )
                if declared is not None and not re.fullmatch(r"[0-9]{1,12}", declared):
                    raise ProcessingError(
                        "url_length", "Video host returned an invalid file length."
                    )
                expected = int(declared) if declared is not None else None
                limit = cfg.max_upload_mb * 1024 * 1024
                if expected is not None and expected > limit:
                    raise ProcessingError(
                        "upload_size_limit", "Remote clip exceeds the upload size limit."
                    )
                digest, size = hashlib.sha256(), 0
                with partial.open("xb") as f:
                    while True:
                        remaining = deadline - time.monotonic()
                        if timed_out.is_set() or remaining <= 0:
                            raise ProcessingError(
                                "url_timeout", "Video download exceeded its time limit."
                            )
                        # read1 prevents a slow stream from extending one large read indefinitely.
                        if connection.sock:
                            connection.sock.settimeout(min(IO_SECONDS, remaining))
                        chunk = response.read1(min(64 * 1024, limit - size + 1))
                        if not chunk:
                            if timed_out.is_set() or time.monotonic() >= deadline:
                                raise ProcessingError(
                                    "url_timeout", "Video download exceeded its time limit."
                                )
                            break
                        size += len(chunk)
                        if size > limit:
                            raise ProcessingError(
                                "upload_size_limit", "Remote clip exceeds the upload size limit."
                            )
                        digest.update(chunk)
                        f.write(chunk)
                if not size or (expected is not None and size != expected):
                    raise ProcessingError(
                        "url_incomplete", "Video download was empty or incomplete."
                    )
                partial.replace(output)
                return {"sha256": digest.hexdigest(), "size_bytes": size, "final_url": current}
            finally:
                connection.close()
    except (socket.timeout, TimeoutError):
        raise ProcessingError("url_timeout", "Video download timed out. Retry later.")
    except ssl.SSLError:
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise ProcessingError("url_timeout", "Video download exceeded its time limit.")
        raise ProcessingError("url_tls", "Video host failed verified HTTPS connection.")
    except (OSError, http.client.HTTPException):
        if timed_out.is_set() or time.monotonic() >= deadline:
            raise ProcessingError("url_timeout", "Video download exceeded its time limit.")
        raise ProcessingError("url_download", "Video could not be downloaded. Retry later.")
    finally:
        watchdog.cancel()
        partial.unlink(missing_ok=True)
