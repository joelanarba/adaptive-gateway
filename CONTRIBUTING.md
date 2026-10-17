# Contributing

Use Python 3.11 or 3.12, a virtual environment and `requirements-dev.txt`.
Run `python -m ruff check .`, `python -m black --check .` and `python -m pytest -q`
before proposing changes. JavaScript checks are `node --test sdks/tests/*.test.js`,
then `npm ci`, `npm run lint` and `npm run build` inside `dashboard/`.

The default Python tests use SQLite and fake Redis; they must never point at a
developer's PostgreSQL database or real Redis. Real-service checks belong in a
disposable Compose project. Preserve raw benchmark evidence and describe the
environment, cache state, impairment placement and response completeness.

Keep changes focused. Include a local reproduction, regression test and public
API compatibility notes for fixes. Do not commit secrets, claim unmeasured
performance, or run destructive tests against public services.
See [the security policy](SECURITY.md) for vulnerability reports.

Suggested repository description: “Experimental network-aware API gateway for
constrained connectivity, with reproducible adaptation and failure tests.”
Suggested topics: `api-gateway`, `fastapi`, `redis`, `distributed-systems`,
`network-aware`, `reproducible-research`. Remote repository metadata is not
modified by local work.
