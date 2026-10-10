"""Public webhook delivery without redirects, proxies or DNS rebinding."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import SplitResult, urlsplit


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address) and (
        address.sixtofour or address in ipaddress.ip_network("64:ff9b::/96")
    ):
        return False  # Translation/tunnel destinations can hide private IPv4.
    # Azure's platform virtual IP is globally numbered but is an internal
    # host-agent service, not a tenant webhook destination.
    return (address.is_global and not address.is_multicast
            and str(address) != "168.63.129.16")


def validate_webhook_url(value: str) -> SplitResult:
    if not value or len(value) > 4096 or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Invalid webhook URL.")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or "\\" in value or "%" in parsed.hostname):
        raise ValueError("Invalid webhook destination.")
    # Accessing port also checks invalid/out-of-range values.
    if parsed.port == 0:
        raise ValueError("Invalid webhook port.")
    host = parsed.hostname.encode("idna").decode("ascii")
    if host.rstrip(".").lower() == "localhost":
        raise ValueError("Private webhook destination.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # Resolve and check every DNS answer immediately before delivery.
    else:
        if not _public_address(host):
            raise ValueError("Private webhook destination.")
    return parsed


def _resolve_addresses(host: str, port: int) -> list[tuple]:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not addresses:
        raise ValueError("Webhook destination has no addresses.")
    # Reject the whole destination when even one answer is unsafe.
    for family, _, _, _, sockaddr in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6} or not _public_address(sockaddr[0]):
            raise ValueError("Private webhook destination.")
        if family == socket.AF_INET6 and sockaddr[3]:
            raise ValueError("Scoped webhook destination.")
    return addresses


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, addresses: list[tuple]):
        super().__init__(host, port, timeout=10)
        self.addresses = addresses

    def connect(self) -> None:
        # Use the verified numeric sockaddr directly, without a second DNS
        # lookup. Environment proxies and HTTP tunnels are never consulted.
        for family, kind, protocol, _, sockaddr in self.addresses:
            candidate = socket.socket(family, kind, protocol)
            try:
                candidate.settimeout(self.timeout)
                candidate.connect(sockaddr)
            except OSError:
                candidate.close()
                continue
            self.sock = candidate
            return
        raise OSError("Webhook connection failed.")


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    default_port = 443

    def connect(self) -> None:
        super().connect()
        try:
            # Original host supplies SNI and verified certificate identity,
            # while the socket remains pinned to its validated address.
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except Exception:
            self.close()
            raise


def post_webhook(url: str, body: bytes) -> int:
    parsed = validate_webhook_url(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_addresses(host, port)
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(host, port, addresses)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        connection.request("POST", target, body=body, headers={
            "Content-Type": "application/json", "User-Agent": "AdmissionAnalyser-Webhook/1.0",
        })
        response = connection.getresponse()
        try:
            if not 200 <= response.status < 300:
                # In particular, never follow a redirect to another target.
                raise ValueError("Webhook endpoint did not accept delivery.")
            return response.status
        finally:
            response.close()  # No unbounded response-body read.
    finally:
        connection.close()
