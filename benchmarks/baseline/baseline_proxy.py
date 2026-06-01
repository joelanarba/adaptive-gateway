"""Plain reverse-proxy baseline for the Adaptive API Gateway experiment.

Deliberately *non-adaptive*: no caching, no response optimization, no network
classification, no auth, no metrics, no database. It exposes the same
``/proxy/{service}/{path}`` interface as the gateway on a different port (9000)
so that

    python benchmarks/run_experiment.py --baseline http://localhost:9000

measures the gateway's adaptation logic as the *only* independent variable.

Upstreams are read from the same ``UPSTREAM_SERVICES`` JSON env map the gateway
uses, so both systems proxy to identical backends.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1) — same set the
# gateway strips, so the only difference between systems is the adaptation.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

_DEFAULT_UPSTREAMS = {"jsonplaceholder": "https://jsonplaceholder.typicode.com"}


def _load_upstreams() -> dict[str, str]:
    """Parse the same ``UPSTREAM_SERVICES`` JSON map the gateway consumes."""
    raw = (os.environ.get("UPSTREAM_SERVICES") or "").strip()
    if not raw:
        return dict(_DEFAULT_UPSTREAMS)
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()}
    except (ValueError, AttributeError):
        return dict(_DEFAULT_UPSTREAMS)


UPSTREAMS = _load_upstreams()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pooled client for all upstream calls (mirrors the gateway's pooling so
    # connection-reuse is not a confounding difference).
    app.state.http_client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=10.0,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="Baseline Plain Proxy", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "system": "baseline"}


@app.api_route(
    "/proxy/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
)
async def proxy(service: str, path: str, request: Request) -> Response:
    base = UPSTREAMS.get(service)
    if base is None:
        return Response(
            content=f'{{"detail":"unknown upstream service: {service}"}}',
            status_code=404,
            media_type="application/json",
        )

    target = f"{base.rstrip('/')}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    body = await request.body()
    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream = await client.request(
            request.method, target, headers=fwd_headers, content=body
        )
    except httpx.HTTPError as exc:
        # No cache fallback, no queue — a plain proxy just fails. That contrast
        # with the gateway is exactly what the experiment is meant to surface.
        return Response(
            content=f'{{"detail":"upstream error: {exc.__class__.__name__}"}}',
            status_code=502,
            media_type="application/json",
        )

    resp_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding"
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
