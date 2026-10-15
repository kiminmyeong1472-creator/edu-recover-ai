"""Offline transport tests: DNS, TLS sockets and HTTP are all replaced."""

from __future__ import annotations

import http.client
import io
import json
import os
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch

from app.services import open_model_http as transport


URL = "https://inference.cloudflare.com/v1/chat/completions"
HOST = "inference.cloudflare.com"
TOKEN = "private-model-token"
PUBLIC = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443))
PUBLIC_V6 = (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
             "", ("2606:4700:4700::1111", 443, 0, 0))


class FakeResponse:
    def __init__(self, body=b'{"ok":true}', headers=None, status=200, error=None):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = headers or []
        self.error = error
        self.read_sizes = []
        self.closed = False

    def getheaders(self):
        return self.headers

    def read1(self, amount):
        self.read_sizes.append(amount)
        if self.error:
            raise self.error
        return self.body.read(min(amount, 7))

    def close(self):
        self.closed = True
        self.body.close()


class FakeConnection:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else FakeResponse()
        self.error = error
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class URLValidationTests(unittest.TestCase):
    def test_url_normalizes_without_network_access(self):
        with patch.object(socket, "getaddrinfo") as resolve:
            self.assertEqual(URL, transport.validate_endpoint_url(
                "HTTPS://INFERENCE.CLOUDFLARE.COM:443/v1/chat/completions"))
            self.assertEqual("https://inference.cloudflare.com/",
                             transport.validate_endpoint_url("https://inference.cloudflare.com"))
            resolve.assert_not_called()

    def test_invalid_endpoints_fail_without_echoing_configuration(self):
        cases = [
            None, 1, "", " " + URL, URL + " ", "http://" + HOST,
            "ftp://" + HOST, "https://", "https://localhost", "https://host",
            "https://user:secret@" + HOST, "https://user@" + HOST,
            "https://" + HOST + ":444/v1", "https://" + HOST + ":secret/v1",
            URL + "?secret=token", URL + "?", URL + "#", URL + "#fragment",
            "https://127.0.0.1/v1", "https://1.1.1.1/v1", "https://[::1]/v1",
            "https://[2606:4700:4700::1111]/v1", "https://127.1/v1",
            "https://0x7f000001/v1", "https://0177.0.0.1/v1",
            "https://metadata.google.internal/v1", "https://instance.local/v1",
            "https://foo.localhost/v1", "https://foo.localdomain/v1",
            "https://server.lan/v1", "https://server.home.arpa/v1",
            "https://model.test/v1", "https://model.invalid/v1",
            "https://model.example/v1", "https://example.com/v1",
            "https://private.example.org/v1", "https://example.net/v1",
            "https://" + HOST + ".", "https://" + HOST + "\\@other.com/v1",
            "https://" + HOST + "/%0d%0aHEADER", "https://" + HOST + "/%zz",
            "https://" + HOST + "/%ff", "https://" + HOST + "/\x00",
            "https://a..com/v1", "https://-a.com/v1", "https://a-.com/v1",
            "https://" + "a" * 64 + ".com/v1", "https://모델.kr/v1",
        ]
        for value in cases:
            with self.subTest(value=value), patch.object(socket, "getaddrinfo") as resolve:
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport.validate_endpoint_url(value)
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)
                self.assertEqual(error.exception.code, str(error.exception))
                self.assertNotIn("secret", str(error.exception))
                resolve.assert_not_called()


class DNSAndTLSValidationTests(unittest.TestCase):
    def test_all_public_addresses_are_retained_without_a_second_resolution(self):
        with patch.object(socket, "getaddrinfo", return_value=[PUBLIC, PUBLIC_V6]) as resolve:
            self.assertEqual([
                (PUBLIC[0], PUBLIC[2], PUBLIC[4]),
                (PUBLIC_V6[0], PUBLIC_V6[2], PUBLIC_V6[4]),
            ], transport._public_addresses(HOST))
        resolve.assert_called_once_with(HOST, 443, type=socket.SOCK_STREAM)

    def test_mixed_public_private_and_special_addresses_are_rejected(self):
        addresses = [
            "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
            "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1",
            "240.0.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.1",
            "::1", "::", "fe80::1", "fc00::1", "ff02::1",
            "::ffff:1.1.1.1", "2002:0101:0101::1", "2001:db8::1",
            "64:ff9b::a9fe:a9fe", "64:ff9b::101:101", "64:ff9b:1::a9fe:a9fe",
        ]
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
            record = (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
            with self.subTest(address=address), patch.object(socket, "getaddrinfo", return_value=[PUBLIC, record]):
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport._public_addresses(HOST)
                self.assertEqual("OPEN_MODEL_CONFIG_INVALID", error.exception.code)

    def test_dns_failure_and_timeout_are_sanitized(self):
        for exception, code in [
            (socket.gaierror("PRIVATE raw resolver value"), "SERVICE_UNAVAILABLE"),
            (socket.timeout("PRIVATE timeout"), "TIMEOUT"),
        ]:
            with self.subTest(code=code), patch.object(socket, "getaddrinfo", side_effect=exception):
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport._public_addresses(HOST)
                self.assertEqual("OPEN_MODEL_" + code, error.exception.code)
                self.assertNotIn("PRIVATE", str(error.exception))
        with patch.object(socket, "getaddrinfo", return_value=[]):
            with self.assertRaises(transport.OpenModelHTTPError):
                transport._public_addresses(HOST)

    def test_socket_is_pinned_but_tls_uses_original_hostname_and_bounded_timeouts(self):
        raw_socket = MagicMock()
        tls_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = tls_socket
        address = (PUBLIC[0], PUBLIC[2], PUBLIC[4])
        with patch.object(ssl, "create_default_context", return_value=context), \
                patch.object(socket, "socket", return_value=raw_socket) as create_socket, \
                patch.object(socket, "getaddrinfo") as resolve:
            connection = transport._PinnedHTTPSConnection(HOST, address)
            connection.connect()
        create_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        raw_socket.settimeout.assert_called_once_with(5)
        raw_socket.connect.assert_called_once_with(("1.1.1.1", 443))
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname=HOST)
        tls_socket.settimeout.assert_called_once_with(120)
        self.assertIs(connection.sock, tls_socket)
        resolve.assert_not_called()

    def test_failed_tls_handshake_closes_raw_socket(self):
        raw_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLError("PRIVATE certificate detail")
        with patch.object(ssl, "create_default_context", return_value=context), \
                patch.object(socket, "socket", return_value=raw_socket):
            connection = transport._PinnedHTTPSConnection(HOST, (PUBLIC[0], PUBLIC[2], PUBLIC[4]))
            with self.assertRaises(ssl.SSLError):
                connection.connect()
        raw_socket.close.assert_called_once()


class RequestTests(unittest.TestCase):
    def request(self, response=None, error=None, payload=None, token=TOKEN):
        connection = FakeConnection(response, error)
        with patch.object(socket, "getaddrinfo", return_value=[PUBLIC]) as resolve, \
                patch.object(transport, "_PinnedHTTPSConnection", return_value=connection) as create:
            result = transport.post_json(URL, {"prompt": "질문"} if payload is None else payload, token)
        return result, connection, create, resolve

    def test_one_request_only_with_bearer_header_and_no_environment_proxy(self):
        response = FakeResponse(b'{"answer":"2"}', [("Content-Length", "14")])
        with patch.dict(os.environ, {
            "HTTP_PROXY": "http://localhost:9999", "HTTPS_PROXY": "http://localhost:9999",
            "ALL_PROXY": "http://localhost:9999", "AWS_ENDPOINT_URL": "http://localhost:9999",
        }):
            result, connection, create, resolve = self.request(response)
        self.assertEqual({"answer": "2"}, result)
        create.assert_called_once_with(HOST, (PUBLIC[0], PUBLIC[2], PUBLIC[4]))
        resolve.assert_called_once()
        self.assertEqual(1, len(connection.requests))
        args, kwargs = connection.requests[0]
        self.assertEqual(("POST", "/v1/chat/completions"), args)
        self.assertEqual({"prompt": "질문"}, json.loads(kwargs["body"]))
        self.assertEqual("Bearer " + TOKEN, kwargs["headers"]["Authorization"])
        self.assertNotIn(TOKEN.encode(), kwargs["body"])
        self.assertEqual("identity", kwargs["headers"]["Accept-Encoding"])
        self.assertTrue(response.closed and connection.closed)

    def test_invalid_credentials_payload_or_size_never_reaches_dns(self):
        cases = [
            ({"prompt": "x"}, ""), ({"prompt": "x"}, "secret\nheader"),
            ({"prompt": "x"}, "secret token"), ({"prompt": "x"}, "비밀"),
            ([], TOKEN), ({"number": float("nan")}, TOKEN),
            ({"number": float("inf")}, TOKEN), ({"prompt": "\ud800"}, TOKEN),
            ({"prompt": "x" * transport.MAX_INPUT_BYTES}, TOKEN),
        ]
        for payload, token in cases:
            with self.subTest(payload_type=type(payload), token_type=type(token)), \
                    patch.object(socket, "getaddrinfo") as resolve:
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport.post_json(URL, payload, token)
                self.assertNotIn("secret", str(error.exception))
                self.assertNotIn(TOKEN, str(error.exception))
                resolve.assert_not_called()

    def test_private_resolution_prevents_connection_creation(self):
        private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 443))
        with patch.object(socket, "getaddrinfo", return_value=[private]), \
                patch.object(transport, "_PinnedHTTPSConnection") as create:
            with self.assertRaises(transport.OpenModelHTTPError):
                transport.post_json(URL, {}, TOKEN)
            create.assert_not_called()

    def test_http_errors_never_read_or_echo_error_body_and_never_retry(self):
        codes = {
            301: "REQUEST_REJECTED", 302: "REQUEST_REJECTED", 307: "REQUEST_REJECTED",
            400: "REQUEST_REJECTED", 401: "AUTHENTICATION_FAILED", 403: "ACCESS_DENIED",
            404: "RESOURCE_NOT_FOUND", 408: "TIMEOUT", 413: "INPUT_TOO_LARGE",
            429: "QUOTA_EXCEEDED", 500: "SERVICE_UNAVAILABLE", 503: "SERVICE_UNAVAILABLE",
            504: "TIMEOUT",
        }
        for status, code in codes.items():
            response = FakeResponse(b'{"secret":"' + TOKEN.encode() + b'"}',
                                    [("Location", "http://169.254.169.254")], status)
            connection = FakeConnection(response)
            with self.subTest(status=status), patch.object(socket, "getaddrinfo", return_value=[PUBLIC]), \
                    patch.object(transport, "_PinnedHTTPSConnection", return_value=connection) as create:
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport.post_json(URL, {}, TOKEN)
                self.assertEqual("OPEN_MODEL_" + code, error.exception.code)
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertEqual([], response.read_sizes)
                self.assertEqual(1, len(connection.requests))
                self.assertTrue(response.closed and connection.closed)
                create.assert_called_once()

    def test_socket_tls_and_protocol_errors_are_sanitized_and_connections_close(self):
        for exception, code in [
            (socket.timeout(TOKEN), "TIMEOUT"),
            (OSError(TOKEN), "SERVICE_UNAVAILABLE"),
            (ssl.SSLCertVerificationError(TOKEN), "SERVICE_UNAVAILABLE"),
            (http.client.BadStatusLine(TOKEN), "RESPONSE_INVALID"),
        ]:
            connection = FakeConnection(error=exception)
            with self.subTest(exception=type(exception)), patch.object(socket, "getaddrinfo", return_value=[PUBLIC]), \
                    patch.object(transport, "_PinnedHTTPSConnection", return_value=connection):
                with self.assertRaises(transport.OpenModelHTTPError) as error:
                    transport.post_json(URL, {}, TOKEN)
                self.assertEqual("OPEN_MODEL_" + code, error.exception.code)
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertEqual(1, len(connection.requests))
                self.assertTrue(connection.closed)


class ResponseTests(unittest.TestCase):
    def assert_response_error(self, body=b'{}', headers=None, code="RESPONSE_INVALID", error=None):
        response = FakeResponse(body, headers, error=error)
        connection = FakeConnection(response)
        with patch.object(socket, "getaddrinfo", return_value=[PUBLIC]), \
                patch.object(transport, "_PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(transport.OpenModelHTTPError) as caught:
                transport.post_json(URL, {}, TOKEN)
        self.assertEqual("OPEN_MODEL_" + code, caught.exception.code)
        self.assertEqual(caught.exception.code, str(caught.exception))
        self.assertTrue(response.closed and connection.closed)
        return response

    def test_strict_json_rejects_duplicates_nonfinite_nonobject_invalid_utf8_and_truncation(self):
        for body in [
            b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}',
            b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}',
            b'[]', b'null', b'42', b'', b'{', b'{}private', b'{"a":"\xff"}',
            b'\xef\xbb\xbf{}',
        ]:
            with self.subTest(body=body):
                self.assert_response_error(body)

    def test_malformed_duplicate_lengths_transfer_encodings_and_compression_rejected(self):
        for headers in [
            [("Content-Length", "-1")], [("Content-Length", "+2")],
            [("Content-Length", "2, 2")], [("Content-Length", "")],
            [("Content-Length", "2.0")], [("Content-Length", "2"), ("content-length", "2")],
            [("Content-Length", "3")], [("Content-Length", "1")],
            [("Transfer-Encoding", "gzip")], [("Transfer-Encoding", "gzip, chunked")],
            [("Transfer-Encoding", "chunked"), ("Content-Length", "2")],
            [("Transfer-Encoding", "chunked"), ("transfer-encoding", "chunked")],
            [("Content-Encoding", "gzip")], [("Content-Encoding", "br")],
            [("Content-Encoding", "identity"), ("content-encoding", "identity")],
        ]:
            with self.subTest(headers=headers):
                self.assert_response_error(headers=headers)

    def test_declared_and_actual_oversize_are_rejected_with_bounded_reads(self):
        response = self.assert_response_error(
            headers=[("Content-Length", str(transport.MAX_RESPONSE_BYTES + 1))],
            code="RESPONSE_TOO_LARGE")
        self.assertEqual([], response.read_sizes)
        with patch.object(transport, "MAX_RESPONSE_BYTES", 64):
            response = self.assert_response_error(b'{"large":"' + b'x' * 100 + b'"}',
                                                 code="RESPONSE_TOO_LARGE")
        self.assertTrue(all(0 < size <= 65 for size in response.read_sizes))

    def test_incomplete_reads_timeouts_and_bad_chunk_framing_are_sanitized(self):
        self.assert_response_error(error=http.client.IncompleteRead(TOKEN.encode(), 42))
        self.assert_response_error(error=socket.timeout(TOKEN), code="TIMEOUT")
        self.assert_response_error(error=http.client.HTTPException(TOKEN))

    def test_continuously_dripped_body_stops_after_elapsed_time_budget(self):
        response = FakeResponse(b'{"answer":"2"}')
        connection = FakeConnection(response)
        with patch.object(socket, "getaddrinfo", return_value=[PUBLIC]), \
                patch.object(transport, "_PinnedHTTPSConnection", return_value=connection), \
                patch.object(transport.time, "monotonic", side_effect=[10.0, 10.0, 131.0]):
            with self.assertRaises(transport.OpenModelHTTPError) as error:
                transport.post_json(URL, {}, TOKEN)
        self.assertEqual("OPEN_MODEL_TIMEOUT", error.exception.code)
        self.assertEqual(1, len(response.read_sizes))
        self.assertTrue(response.closed and connection.closed)

    def test_identity_and_chunked_json_responses_are_allowed(self):
        for headers in [[("Content-Encoding", "identity")], [("Transfer-Encoding", "chunked")], []]:
            response = FakeResponse(b'{"usage":{"tokens":1.5}}', headers)
            connection = FakeConnection(response)
            with self.subTest(headers=headers), patch.object(socket, "getaddrinfo", return_value=[PUBLIC]), \
                    patch.object(transport, "_PinnedHTTPSConnection", return_value=connection):
                self.assertEqual({"usage": {"tokens": 1.5}}, transport.post_json(URL, {}, TOKEN))
            self.assertTrue(response.closed and connection.closed)


if __name__ == "__main__":
    unittest.main()
