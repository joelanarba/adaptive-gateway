# Security boundaries and operational requirements

The gateway is a security-sensitive reverse proxy. The 2026-10-17 audit fixed
confirmed application defects but did not certify a public deployment.
The [dependency scan record](DEPENDENCY_AUDIT.json) includes the original findings
and a clean Python recheck after the user-approved upgrade. [The reporting policy](../SECURITY.md) explains private reports.

## Upstream and identity boundaries

Configure exact service names/base URLs. Explicit private upstreams are allowed
because internal reverse proxying is intentional; only operators should edit
configuration. Use egress rules to limit configured destinations and protect DNS.
Redirects are never followed. Optional discovery requires domain suffixes and
public DNS addresses and pins the HTTP connection to a checked IP. It does not
support automatic HTTPS discovery; configure TLS upstreams explicitly.

Untrusted Forwarded/X-Forwarded headers do not establish client identity. Set
TRUSTED_PROXY_NETWORKS to precise controlled proxy addresses, not an entire
untrusted network. Compose disables Uvicorn automatic proxy-header trust. Nginx
replaces the incoming forwarded IP with its observed peer; multi-proxy deployments
need a separately reviewed chain. Host/path syntax is checked before middleware
uses request.url, mitigating the documented [Starlette Host parsing advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr).
The framework dependency was also upgraded to Starlette 1.8.0.

Client RTT/ECT/Save-Data values are untrusted representation preferences. They do
not alter authorization, rate limits, body limits or upstream allowlisting.
Network labels are not measurement evidence.

## Credentials and access

Production requires a unique signing key of at least 32 characters and rejects
built-in/example placeholders and disposable PostgreSQL passwords. The production
Grafana entrypoint requires an administrator password of at least 16 characters. Generate secrets with a cryptographic random
source. Do not put them in URLs, source code, screenshots, examples, metrics or
issues. Local secret/private-key contents were not displayed or copied into reports.
Test configuration bypasses .env and overrides datastore/secret settings. They are ignored; historical exposure and credential rotation remain an
operator responsibility if prior exposure is suspected.

All admin routes require a JWT for an active current database administrator.
API keys never grant admin privileges and can be revoked. JWT expiry/issue-time/
subject/type are mandatory and HS256 is the only supported algorithm. Refresh
rotation takes a PostgreSQL row lock; live concurrent validation is still needed.
Logout does not immediately revoke already issued access JWTs. Password creation
is limited to 72 UTF-8 bytes to avoid bcrypt truncation collisions. No email
verification/password reset/MFA workflow exists.

The dashboard uses sessionStorage for browser credentials; this still exposes
tokens to same-origin script/XSS. No automatic token refresh is implemented.
Review CSP, secure delivery and a cookie-backed browser session design before
public administrative use. The browser-decoded role only changes the interface;
the gateway enforces authorization against the database.

## Cache and writes

Shared caching bypasses authenticated, cookie, conditional and range requests.
Response Set-Cookie/cache directives/Vary/Expires prevent storage. Exact target,
query order and representation headers partition keys. Review the anonymous cache
contract for headerless user-dependent endpoints. Stale retention is up to one
day and must be acceptable for each enabled route. Writes do not automatically
invalidate related resources; disable cache for mutable resources or flush it.

Queueing requires an explicit upstream idempotency contract and client key. A
202 is acceptance into Redis, not a successful business operation or fsync
receipt. Timeout outcomes remain ambiguous. Redis retains replay credentials and
request bodies; restrict access, encrypt storage/transport where needed, limit
retention and ensure credential/deduplication lifetimes cover replay. Expired
credentials are dead-lettered, not renewed. PostgreSQL dead letters exclude auth
headers but can still hold sensitive bodies. Define a retention/deletion policy
before using personal data. No dead-letter web UI or client polling contract
exists yet.

## Deployment

Use Compose 2.24.4+ and the base plus production overlay. Supply independent
production secrets/database credentials, correct DATABASE_URL and real TLS
certificates/domain. Apply Alembic before accepting traffic. The overlay removes
source mounts and direct gateway/datastore ports. Management ports stay loopback;
use an authenticated operator tunnel. Nginx denies external /metrics while
Prometheus scrapes internally. Its certificate placeholder must be replaced and
TLS configuration verified; certificate issuance/renewal was not exercised.

Redis uses noeviction to avoid dropping queued writes, but the cache and queue
share memory and can reject writes under pressure. Redis AOF default fsync can
lose a recently accepted entry on power loss. Separate cache/queue Redis
instances, stronger durability and network segmentation are future decisions.
Use container CPU/memory/process/connection limits appropriate to the host.
Application requests are buffered up to 1 MiB and decoded responses up to 8 MiB;
streaming/SSE/WebSocket behavior is not supported. Decompression/parser temporary
allocations can exceed the accumulated body cap. Do not expose the plain
benchmark baseline publicly.

CloudFormation is now host preparation only: restricted SSH CIDR, TLS front-door
ports and IMDSv2; it does not launch a service with copied sample credentials.
The AMI uses AWS's [typed SSM parameter approach](https://docs.aws.amazon.com/linux/al2023/ug/ec2.html).
The template, cloud costs and bootstrapping are unverified. It still needs an
operator-reviewed pinned deployment and TLS/secret provisioning. Nothing was
deployed during this audit.

## Logs, metrics and dependency review

Exception text that may contain URLs/SQL parameters is suppressed on background
failure paths; SQLAlchemy parameter hiding is enabled and HTTPX INFO URL logging
is disabled. Nginx request logs and stored request paths/client IDs can still be
sensitive. Keep logs private, set retention, and avoid credential-bearing query
parameters. Prometheus labels use bounded route templates. Metrics have no
application auth on the direct internal endpoint and are per process.

`pip-audit --local` initially found 28 advisory entries across seven packages in
this virtual environment, including developer tools. After the user-approved
upgrade, the same scan reported no known vulnerabilities. FastAPI/Starlette,
configuration and test tooling were upgraded; PyJWT replaced the unused JOSE/
ECDSA stack while preserving HS256 token claims. See the before/after JSON record.
This is a time-specific advisory lookup, not a security guarantee. Container-image
scans and a complete transitive/license review remain pending. MIT applies to
project code; bundled software retains its own license obligations.

The dashboard upgrade removes the reported critical Next.js finding. Production
npm audit reports zero known vulnerabilities. Full npm audit reports five high
package findings in a development-only glob/braces chain used by ESLint. Keep
lint runs restricted to trusted repository/configuration input. The runtime image
prunes development packages after building; actual image contents still require
verification. See DASHBOARD_DEPENDENCY_AUDIT.json for before/after counts and
remaining advisory details. No audit exclusions or disabled rules mask this result.
