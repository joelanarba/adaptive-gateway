"""HTTP forwarding boundaries shared by proxy and queued replay."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import quote, urlsplit

import httpx

from config import settings, validate_upstream_url

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
REPRESENTATION_HEADERS = {
    "etag",
    "last-modified",
    "content-md5",
    "digest",
    "content-digest",
    "repr-digest",
    "content-range",
    "accept-ranges",
}


class BodyTooLarge(Exception):
    """A configured buffering limit was exceeded."""


def clean_headers(headers: httpx.Headers | dict) -> dict[str, str]:
    nominated = {
        part.strip().lower() for part in headers.get("connection", "").split(",")
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP | nominated
    }


def build_target(base: str, path: str, query: str = "") -> str:
    validate_upstream_url(base)
    if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("Path traversal is not allowed")
    # The ASGI path is already decoded. Encode it once; never interpret a client
    # path as a URL authority, fragment or query. Preserve the operator's prefix.
    target = base.rstrip("/") + "/" + quote(path, safe="/-._~:@!$&'()*+,;=")
    return target + ("?" + query if query else "")


async def discover_upstream(service: str) -> tuple[str, str]:
    """Opt-in public DNS discovery, restricted by domain and pinned to an IP."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", service):
        raise ValueError("Invalid discovery hostname")
    host = service.lower()
    if not settings.allow_unlisted_upstreams or not any(
        host.endswith("." + domain.lower().lstrip("."))
        for domain in settings.upstream_discovery_domains
    ):
        raise ValueError("Unknown upstream")
    addresses = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, 80, type=socket.SOCK_STREAM),
        timeout=settings.upstream_timeout_seconds,
    )
    ips = {item[4][0] for item in addresses}
    if not ips or any(not ipaddress.ip_address(value).is_global for value in ips):
        raise ValueError("Discovery requires public addresses")
    address = sorted(ips)[0]
    authority = f"[{address}]" if ":" in address else address
    return f"http://{authority}", host


async def bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """Read decoded upstream bytes with a hard cap, closing on every exit."""
    async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes(chunk_size=65536):
            size += len(chunk)
            if size > settings.max_response_body_bytes:
                raise BodyTooLarge
            chunks.append(chunk)
        headers = clean_headers(response.headers)
        # HTTPX decodes content codings; validators for encoded bytes no longer
        # describe the body. Ask upstream for identity, but handle noncompliance.
        if response.headers.get("content-encoding"):
            headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in REPRESENTATION_HEADERS | {"content-encoding"}
            }
        result = httpx.Response(
            response.status_code,
            content=b"".join(chunks),
            headers=headers,
            request=response.request,
        )
        # Headers.items() combines Set-Cookie; retain individual cookies.
        result.extensions["set_cookie"] = response.headers.get_list("set-cookie")
        return result


def destination_service(url: str) -> str | None:
    """Return the configured service for an exact base destination/prefix."""
    parsed = urlsplit(url)
    for service, base in settings.upstream_services.items():
        approved = urlsplit(base)
        prefix = approved.path.rstrip("/") + "/"
        if (parsed.scheme, parsed.netloc) == (approved.scheme, approved.netloc):
            if parsed.path.startswith(prefix):
                return service
    return None
