"""Bounded HTTPS JSON requests to explicitly configured model deployments.

This module neither reads credentials nor chooses a model/provider. The caller
supplies a deployment URL, request object and bearer token. Configuration
validation does not resolve DNS or make requests. Actual calls resolve once,
reject non-public addresses, then connect to one validated address while TLS
still authenticates the original hostname. No proxies, redirects or retries
are used.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


MAX_INPUT_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 120
RESPONSE_BODY_TIMEOUT_SECONDS = 120
_READ_CHUNK_BYTES = 64 * 1024
_BLOCKED_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".lan", ".home",
    ".home.arpa", ".onion", ".test", ".invalid", ".example",
)
_BLOCKED_NAMES = {"localhost", "example.com", "example.net", "example.org"}
_TRANSLATED_V6_NETWORKS = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


class OpenModelHTTPError(Exception):
    """Only a stable public code; never retain upstream text or credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _error(code: str) -> OpenModelHTTPError:
    return OpenModelHTTPError("OPEN_MODEL_" + code)


def validate_endpoint_url(value: str) -> str:
    """Normalize an HTTPS/443 endpoint without network or credential access."""
    try:
        if (not isinstance(value, str) or not value or len(value) > 2048
                or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in value)
                or any(ch in value for ch in ("\\", "?", "#"))):
            raise ValueError
        parts = urlsplit(value)
        host = parts.hostname
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or host.endswith(".") or len(host) > 253):
            raise ValueError
        # IP literals and alternate numeric address forms are not deployment
        # hostnames. They must not bypass domain or DNS address validation.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        labels = host.split(".")
        if (len(labels) < 2 or labels[-1].isdigit()
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in labels)
                or host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES)
                or any(host.endswith("." + name) for name in _BLOCKED_NAMES)):
            raise ValueError
        path = parts.path or "/"
        if re.search(r"%(?![0-9a-fA-F]{2})", path):
            raise ValueError
        decoded_path = unquote(path, errors="strict")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded_path):
            raise ValueError
        return urlunsplit(("https", host, path, "", ""))
    except (ValueError, TypeError, UnicodeError):
        raise _error("CONFIG_INVALID") from None


def _public_addresses(host: str) -> list[tuple[int, int, tuple]]:
    """Resolve once, rejecting mixed public/private and transition addresses."""
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.timeout, TimeoutError):
        raise _error("TIMEOUT") from None
    except (OSError, UnicodeError):
        raise _error("SERVICE_UNAVAILABLE") from None
    if not records:
        raise _error("SERVICE_UNAVAILABLE")
    addresses = []
    try:
        for family, socktype, protocol, _, sockaddr in records:
            if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
                raise ValueError
            address = ipaddress.ip_address(sockaddr[0])
            if (not address.is_global or address.is_multicast or address.is_reserved
                    or address.is_unspecified or address.is_loopback or address.is_link_local):
                raise ValueError
            if isinstance(address, ipaddress.IPv6Address) and (
                address.ipv4_mapped is not None or address.sixtofour is not None
                or address.teredo is not None or "%" in sockaddr[0]
                or any(address in network for network in _TRANSLATED_V6_NETWORKS)
            ):
                raise ValueError
            if sockaddr[1] != 443 or (family == socket.AF_INET) != (address.version == 4):
                raise ValueError
            addresses.append((family, protocol, sockaddr))
    except (ValueError, TypeError, IndexError):
        raise _error("CONFIG_INVALID") from None
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: tuple[int, int, tuple]) -> None:
        super().__init__(host, port=443, timeout=CONNECT_TIMEOUT_SECONDS,
                         context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        # Bypass a second name resolution. Host headers and certificate/SNI
        # remain bound to self.host, rather than the selected numeric address.
        family, protocol, sockaddr = self._validated_address
        raw_socket = socket.socket(family, socket.SOCK_STREAM, protocol)
        try:
            raw_socket.settimeout(CONNECT_TIMEOUT_SECONDS)
            raw_socket.connect(sockaddr)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
            self.sock.settimeout(READ_TIMEOUT_SECONDS)
        except BaseException:
            raw_socket.close()
            raise


def _response_length(response: http.client.HTTPResponse) -> int | None:
    headers = response.getheaders()
    lengths = [value.strip() for name, value in headers if name.lower() == "content-length"]
    transfers = [value.strip().lower() for name, value in headers if name.lower() == "transfer-encoding"]
    encodings = [value.strip().lower() for name, value in headers if name.lower() == "content-encoding"]
    if (len(lengths) > 1 or len(transfers) > 1 or len(encodings) > 1
            or (transfers and (transfers != ["chunked"] or lengths))
            or (encodings and encodings != ["identity"])):
        raise _error("RESPONSE_INVALID")
    if not lengths:
        return None
    if not re.fullmatch(r"[0-9]+", lengths[0]) or len(lengths[0]) > 12:
        raise _error("RESPONSE_INVALID")
    length = int(lengths[0])
    if length > MAX_RESPONSE_BYTES:
        raise _error("RESPONSE_TOO_LARGE")
    return length


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


def _read_json(response: http.client.HTTPResponse) -> dict:
    length = _response_length(response)
    # The socket timeout is an inactivity limit. Also stop a peer that keeps
    # returning tiny chunks indefinitely. A single pending read is still
    # bounded by READ_TIMEOUT_SECONDS before this elapsed-time check resumes.
    deadline = time.monotonic() + RESPONSE_BODY_TIMEOUT_SECONDS
    chunks = []
    size = 0
    while True:
        if time.monotonic() >= deadline:
            raise _error("TIMEOUT")
        chunk = response.read1(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - size))
        if not isinstance(chunk, bytes):
            raise _error("RESPONSE_INVALID")
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise _error("RESPONSE_TOO_LARGE")
        chunks.append(chunk)
    if length is not None and length != size:
        raise _error("RESPONSE_INVALID")
    try:
        result = json.loads(b"".join(chunks).decode("utf-8"),
                            object_pairs_hook=_strict_object, parse_constant=_reject_constant,
                            parse_float=_finite_float)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _error("RESPONSE_INVALID") from None


def _status_error(status: int) -> OpenModelHTTPError:
    if status == 401:
        return _error("AUTHENTICATION_FAILED")
    if status == 403:
        return _error("ACCESS_DENIED")
    if status == 404:
        return _error("RESOURCE_NOT_FOUND")
    if status in (408, 504):
        return _error("TIMEOUT")
    if status == 413:
        return _error("INPUT_TOO_LARGE")
    if status == 429:
        return _error("QUOTA_EXCEEDED")
    return _error("SERVICE_UNAVAILABLE" if status >= 500 else "REQUEST_REJECTED")


def post_json(endpoint_url: str, payload: dict, bearer_token: str) -> dict:
    """Make one HTTPS request, returning strict bounded JSON or a safe code."""
    endpoint = urlsplit(validate_endpoint_url(endpoint_url))
    if (not isinstance(bearer_token, str) or not bearer_token or len(bearer_token) > 8192
            or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in bearer_token)):
        raise _error("CONFIG_INVALID")
    try:
        if not isinstance(payload, dict):
            raise ValueError
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _error("INPUT_INVALID") from None
    if len(data) > MAX_INPUT_BYTES:
        raise _error("INPUT_TOO_LARGE")
    addresses = _public_addresses(endpoint.hostname)
    connection = None
    response = None
    try:
        connection = _PinnedHTTPSConnection(endpoint.hostname, addresses[0])
        connection.request("POST", endpoint.path, body=data, headers={
            "Authorization": "Bearer " + bearer_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        })
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise _status_error(response.status)
        return _read_json(response)
    except OpenModelHTTPError:
        raise
    except (socket.timeout, TimeoutError):
        raise _error("TIMEOUT") from None
    except http.client.IncompleteRead:
        raise _error("RESPONSE_INVALID") from None
    except http.client.HTTPException:
        raise _error("RESPONSE_INVALID") from None
    except (OSError, ValueError, TypeError, UnicodeError):
        raise _error("SERVICE_UNAVAILABLE") from None
    finally:
        for resource in (response, connection):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
