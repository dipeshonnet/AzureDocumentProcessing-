"""Offline tests exercise the actual HTTP transport without external requests."""
import io
import socket
from types import SimpleNamespace

import pytest

from app.services import webhooks
from test_intake_operations import intake_client, auth_headers


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/",
    "https://168.63.129.16/", "https://[::1]/", "https://[::ffff:127.0.0.1]/",
    "https://[64:ff9b::7f00:1]/", "https://[2002:7f00:1::]/",
    "https://[fe80::1%25eth0]/", "ftp://example.edu/", "file:///etc/passwd",
    "http://localhost/", "https://user:password@example.edu/", "https://example.edu/#secret",
    "https://example.edu:0/", "https://example.edu:65536/", "https://example.edu/\r\nInjected: header",
])
def test_unsafe_url_rejected_before_resolution(monkeypatch, url):
    def unexpected(*args, **kwargs):
        pytest.fail("Unsafe URL attempted network resolution.")
    monkeypatch.setattr(socket, "getaddrinfo", unexpected)
    with pytest.raises(ValueError):
        webhooks.post_webhook(url, b"{}")


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["169.254.169.254"], ["93.184.216.34", "10.0.0.1"],
    ["::ffff:192.168.1.1"], ["168.63.129.16"], ["224.0.0.1"],
])
def test_private_or_mixed_dns_answers_rejected(monkeypatch, addresses):
    def resolve(*args, **kwargs):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, "", (ip, 443, 0, 0) if ":" in ip else (ip, 443)) for ip in addresses]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *args: pytest.fail("Unsafe DNS answer attempted connection."))
    with pytest.raises(ValueError):
        webhooks.post_webhook("https://webhook.example.edu/results", b"{}")


def fake_transport(monkeypatch, response=b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n", ip="93.184.216.34"):
    calls = {"dns": [], "connected": [], "sent": [], "closed": 0, "sni": []}

    class FakeSocket:
        def settimeout(self, timeout):
            assert timeout == 10
        def connect(self, address):
            calls["connected"].append(address)
        def sendall(self, data):
            calls["sent"].append(bytes(data))
        def makefile(self, mode):
            return io.BytesIO(response)
        def close(self):
            calls["closed"] += 1

    def resolve(host, port, **kwargs):
        calls["dns"].append((host, port))
        if len(calls["dns"]) > 1:
            pytest.fail("DNS rebinding: a second resolution must never occur.")
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (ip, port, 0, 0) if ":" in ip else (ip, port))]

    def wrap(sock, *, server_hostname):
        calls["sni"].append(server_hostname)
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(webhooks.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    return calls


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_public_delivery_pins_socket_preserves_host_and_ignores_proxies(monkeypatch, scheme, port):
    calls = fake_transport(monkeypatch)
    assert webhooks.post_webhook(f"{scheme}://webhook.example.edu/events?key=private", b'{"event":"completed"}') == 204
    assert calls["dns"] == [("webhook.example.edu", port)]
    assert calls["connected"] == [("93.184.216.34", port)]
    sent = b"".join(calls["sent"])
    assert b"POST /events?key=private HTTP/1.1\r\n" in sent
    assert b"Host: webhook.example.edu\r\n" in sent
    assert sent.endswith(b'{"event":"completed"}')
    assert calls["sni"] == (["webhook.example.edu"] if scheme == "https" else [])
    assert calls["closed"] >= 1


def test_public_ipv6_authority_preserved(monkeypatch):
    ip = "2606:4700:4700::1111"
    calls = fake_transport(monkeypatch, ip=ip)
    assert webhooks.post_webhook(f"http://[{ip}]:8080/events", b"{}") == 204
    assert calls["connected"] == [(ip, 8080, 0, 0)]
    assert f"Host: [{ip}]:8080\r\n".encode() in b"".join(calls["sent"])


def test_redirect_is_not_followed(monkeypatch):
    calls = fake_transport(monkeypatch, response=b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/\r\nContent-Length: 0\r\n\r\n")
    with pytest.raises(ValueError):
        webhooks.post_webhook("https://webhook.example.edu/redirect", b"{}")
    assert len(calls["connected"]) == 1
    assert len(calls["dns"]) == 1
    assert calls["closed"] >= 1


def test_configuration_rejects_private_urls_and_allows_disable(intake_client):
    client, _, _ = intake_client
    headers = auth_headers(client)
    for url in ("http://127.0.0.1/internal", "file:///etc/passwd", "https://user:secret@example.edu/"):
        assert client.put("/api/auth/universities/integration", headers=headers, json={"webhook_url": url}).status_code == 400
    assert client.put("/api/auth/universities/integration", headers=headers, json={"webhook_url": "https://example.edu/events"}).status_code == 200
    assert client.put("/api/auth/universities/integration", headers=headers, json={"webhook_url": None}).status_code == 200


def test_dispatch_revalidates_persisted_url_without_logging_secrets(monkeypatch, caplog):
    from app.services.intake_jobs import trigger_university_webhook_direct
    import threading

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target
        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("Private stored URL must not resolve."))
    job = SimpleNamespace(job_id="job-1", status="completed", status_message="Complete", finished_at=None,
                          application=None, application_id="app-1", extracted_record={}, section_analysis=[], error_metadata={})
    trigger_university_webhook_direct(job, "http://127.0.0.1/?key=confidential")
    assert "Webhook delivery failed for job job-1" in caplog.text
    assert "confidential" not in caplog.text and "127.0.0.1" not in caplog.text
