# Architecture and behavioral contracts

This document describes the implementation after the 2026-10-17 stabilization
pass. See [the audit](docs/PROJECT_AUDIT.md) for actual verification and blockers.
The project is experimental; there is no production or research validation claim.

## Request lifecycle

1. CORS uses configured origins. The boundary middleware validates Host/path
   syntax and buffers at most the configured request limit (1 MiB by default).
2. The optional global limiter uses atomic Redis fixed-window increments.
   Registration/login have endpoint limits. Production limiter failures return
   503; development permits requests when Redis is unavailable.
3. Network classification evaluates valid RTT, ECT, Save-Data, shared passive
   state, then GOOD. Hints select representation preferences, never identity or
   authorization. Trusted client identity comes from the socket, or a right-to-left
   walk through explicitly trusted proxy hops. Uvicorn automatic proxy-header
   trust is disabled in Compose.
4. Auth validates credentials when supplied. JWT principals are checked against
   current database user/role status. API keys never grant administrative status.
   Every admin route has the router-wide `require_admin` dependency.
5. The proxy resolves an operator-configured upstream, constructs an encoded path
   without changing authority, strips hop-by-hop and Connection-nominated headers,
   sets canonical client/quality headers and asks upstream for identity encoding.
6. A pooled HTTPX client uses connection limits and an overall timeout. Redirects
   are not followed. Decoded response buffering is limited to 8 MiB by default.
7. The inner optimizer receives successful JSON GETs, strips configured dotted
   paths and optionally truncates arrays for uncached POOR responses. Gzip respects
   quality values and is kept only if it shrinks the body.
8. The detector sets quality/timing headers and records bounded route-template
   metrics. The asynchronous bounded log writer persists batched rows; lost rows
   from overflow or DB failure are counted. Logs are an operational sample,
   not a lossless experiment dataset.

## Routing and upstream trust

`/proxy/{service}/{path}` maps service names to exact configured HTTP(S) bases.
An explicitly configured private backend is permitted: the operator is choosing
that trust boundary. Base credentials/query/fragment are rejected. Redirects are
returned to the client. Dot-segment/backslash paths are rejected and special path
characters are encoded before joining the configured prefix.

Unlisted upstreams are denied by default. If enabled, discovery additionally
requires an operator-supplied domain suffix, resolves all addresses, rejects any
non-global address and pins the HTTP request to a resolved public IP with the
original Host header. Discovery uses HTTP only; use an explicit HTTPS base for
TLS upstreams. It is not a replacement for egress firewall policy. Configured
upstream DNS is trusted and may resolve privately; DNS/network administration
must protect those configured origins.

## Network state

GOOD is `[0, good_threshold)`, DEGRADED is `[good_threshold, degraded_threshold)`,
POOR is the remaining finite nonnegative interval. Invalid hints fall through.
Explicit hints avoid classifier Redis I/O; the rest of the proxy may still use
Redis for cache/rate limits. ECT and Save-Data are case-insensitive.

Passive state is JSON in `netq:{client}` with a one-hour idle TTL, EWMA, current
tier, candidate tier and consecutive sample count. Three samples are needed to
change tier. Down-transitions use thresholds reduced by 30 ms. The state machine
is pure and unit tested. State is shared across workers, and updates use Redis WATCH/MULTI/EXEC transactions to prevent stale overwrites.
Four bounded retries can skip a sample under sustained contention. The observation
is server elapsed time minus upstream time; it does not observe TCP delivery,
end-to-end RTT or actual bandwidth. It must not validate a network-quality claim.

## Cache contract

Only eligible anonymous GETs use the shared cache. Presence of Authorization,
API key, Cookie, conditional or range headers disables lookup/storage; request
no-cache/no-store/max-age=0 also bypasses. Keys under `cache:v2:` hash the exact
upstream target, raw query order and all forwarded representation headers.
Changing an upstream base cannot accidentally reuse the prior destination's entry.

Only HTTP 200 without Set-Cookie, Cache-Control, Expires or Vary is stored. This
is conservative rather than a full HTTP cache implementation. Raw upstream bodies
are cached before adaptation. Envelope timestamps define a fresh route TTL;
physical Redis TTL adds one day of stale grace. Fresh entries return HIT. Stale
entries return STALE immediately while the owner of a token-protected Redis lock
refreshes them. Failed refresh leaves the existing stale copy until its TTL.
Redis read/write failures degrade to an uncached request; they do not turn a
successful upstream read into a gateway failure.

Cold misses are not coalesced. Writes do not invalidate related cache entries.
An operator must disable caching for mutable/user-dependent data or flush the
cache namespace deliberately. Business-specific freshness and outage policies
remain design work; stale responses are not universally correct.

## Representation and HTTP semantics

The default `RESPONSE_MODE=adaptive` policy preserves GOOD JSON bodies.
DEGRADED removes only configured optional paths.
POOR with cache keeps the cached array lengths; POOR without cache additionally
truncates all arrays recursively to ten items. Nested dictionaries/arrays are
handled without mutating the parsed source. Unparseable JSON passes through.
Only 200 GET JSON responses are eligible; error/empty/range/non-JSON/write
responses and `Cache-Control: no-transform` are preserved.

Operator-selected `RESPONSE_MODE=full/compression/removal/combined` provides four
fixed experimental controls independent of tier. Removal uses configured fields
and preserves item counts; only adaptive POOR truncates arrays. Compression respects
Accept-Encoding, uses level 9 and mtime 0, and must reduce size. The study fixture
disables caching. `ADAPTATION_ENABLED=false` overrides the mode to full.
Eligible responses identify the selected `X-Response-Mode` and actual content with
`X-Adaptive-Mode: none/stripped/skeleton` and completeness full/reduced. Full and
compression-only responses now explicitly carry full/none markers. Content-Encoding
identifies actual compression separately. See [the protocol](docs/EXPERIMENT_METHODOLOGY.md).

Skeletons are application representations, not byte ranges, so use 200 plus
`X-Adaptive-Mode: skeleton` and `X-Response-Completeness: reduced`.
[HTTP 206 has range semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7).
Transformed bodies have recomputed Content-Length, no upstream ETag/Last-Modified,
digest or range metadata, and private/no-store downstream cache policy. Existing
Vary is merged with encoding and hint fields. Duplicate Set-Cookie is preserved.
HTTPX decodes unexpected upstream content encodings; metadata for the encoded
representation is removed when that happens.

The proxy deliberately buffers bounded responses; it does not offer transparent
SSE, WebSocket or arbitrarily large streaming support. A response above the cap
returns an upstream-unavailable error. This cap limits accumulated decoded bytes;
HTTPX decompressor/parser transient allocations may exceed that body size.

## Offline queue and delivery semantics

Queueing is off per route unless `queue_writes: true` asserts an upstream
idempotency contract. Eligible methods are POST/PUT/PATCH/DELETE, with a printable
ASCII Idempotency-Key of 1–128 characters. The key is forwarded on the first
attempt and every replay. The upstream must bind it to identity, method,
destination and body, reject conflicting reuse and retain deduplication state
through the entire retry/expiry window. The gateway cannot enforce that upstream
contract and makes **no exactly-once guarantee**.

After an ambiguous timeout/connection failure, eligible writes are added to a
bounded Redis Stream. The client gets 202 and X-Queue-Id only after Redis accepts
XADD. This is not proof of business commit or disk fsync. Without the contract,
the proxy returns 502/504 with an unknown-outcome explanation and does not queue.
Redis rejection/full queue returns 503 with the outcome still unknown.

Entries preserve method, absolute configured destination/query, required headers,
binary body (Latin-1 container), client ID and enqueue time. Worker consumer names
are unique. Group creation is retried after Redis outages. The worker reads new
entries and uses XAUTOCLAIM with an idle threshold to recover abandoned pending
messages, plus a per-entry token lease to avoid simultaneous active replay.
Retry budgets and next-attempt times are persisted **before** sending. Backoff is
5/30/120/600 seconds, subject to claim/poll scheduling (not a precise timer).
Expiry is one hour by default. Changed/removed/ineligible route destinations and
malformed entries are dead-lettered instead of contacted.

Only 2xx is success. 408/429/5xx/transport failures may retry within the budget;
other statuses, including expired credentials, are terminal failures. Dead letters
commit to PostgreSQL before XACK/XDEL/metadata cleanup. Sensitive authentication
headers are excluded from dead letters; request bodies can still be sensitive.
An ack failure/crash can produce another delivery or duplicate dead-letter row;
upstream idempotency is essential. Pending entries retain required credentials
in Redis, so restrict access and ensure expiry/retention policy matches the data.
There is no automatic credential refresh or result-polling endpoint yet.

## Auth, data and lifecycle

Bcrypt uses configured rounds (12 by default); new passwords over 72 UTF-8 bytes
are rejected. Hashing/verification in routes runs in a thread pool. JWTs require
exp/iat/sub/type and restrict the algorithm to HS256. User activity and current
admin status are checked in the database for JWT requests. API key hashes are
looked up server-side, owner activity is checked, and revocation is immediate.

Refresh tokens are random opaque strings stored as SHA-256 hashes. Rotation locks
the row on PostgreSQL before issuing a replacement; reuse revokes all of a user's
refresh tokens. SQLite tests cover sequential behavior, **not PostgreSQL row-lock
concurrency**. Logout revokes refresh tokens; access tokens retain their bounded
lifetime. There is no password-reset/email-verification workflow.

Development `init_models` creates tables; production requires Alembic. Initial
migration: `95b2091b44f6`. Shutdown stops the worker/log writer, cancels background
refreshes, closes HTTPX/Redis and disposes the SQLAlchemy engine. Live restart,
crash and PostgreSQL migration checks remain unverified in this environment.

## Operations and observability

Prometheus exposes request counters, classification counts, pre/post body sizes,
upstream-call latency, cache outcomes and queue depth. Labels use matched route
templates or `unmatched`, never arbitrary unlisted service paths. Metrics are
per process; multiprocess Prometheus aggregation has not been configured.

RequestLog.rtt_ms is a legacy name for **server processing elapsed time**, not
client RTT. Upstream and optimized-size fields are filled after adaptation.
Queue/cache/log failures must not expose URLs, SQL parameters, credentials or
HTTP bodies through exception text. Operator debug logging and Nginx access logs
still need privacy review for real data.

Docker development binds all published services to loopback. Production removes
source mounts and datastore/direct gateway ports, retains loopback management
ports and exposes only Nginx 80/443. Metrics through the production front door are
denied; internal Prometheus scrapes the gateway directly. Configure real TLS
certificates/domain and precise trusted proxy CIDRs before using that overlay.
Redis noeviction protects queue entries from cache-driven eviction, but a shared
Redis memory limit can still reject writes. The Python advisory recheck is clean in this environment; container-image
security review remains a deployment prerequisite.
