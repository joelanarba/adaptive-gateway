# Dependency upgrade record — approved and applied

The 2026-10-17 local scan found 28 advisory entries in seven packages, including
developer tools. [The machine-readable record](DEPENDENCY_AUDIT.json) lists IDs,
versions and reported fixes; duplicate/overlapping advisories can occur.
This is not a finding that all 28 are exploitable in this application.

| Package in current environment | Proposed work |
| --- | --- |
| FastAPI 0.111.0 / Starlette 0.37.2 | Move together to compatible supported versions permitting Starlette 1.3.1 or newer, then recheck current advisories. Candidate FastAPI 0.143.3 is listed in official release notes; resolve exact compatible pins before changing the project. |
| python-multipart 0.0.9 | Move to a currently patched release (scan reports fixes through 0.0.31); confirm it is needed and retest form/parser behavior if retained. |
| python-jose 3.3.0 / ecdsa 0.19.2 | Upgrade or evaluate a maintained minimal JWT alternative. HS256-only application usage avoids asymmetric/JWE features, but scan entries have no listed fix and must be triaged rather than blindly ignored. |
| pytest 8.1.1 / pytest-asyncio 0.23.6 | Upgrade together; modern async fixture/event-loop scopes need migration. Scan reports a pytest fix at 9.0.3. |
| Black 24.3.0 | Upgrade formatting tooling and review resulting changes; scan reports fixes at 26.3.1. Keep the established Ruff/Black tool roles. |
| pip 25.0.1 (local bootstrap tool) | Update local/image build tooling in a controlled environment; scan reports fixes through 26.2. This is not a committed runtime dependency. |

Do not force a newer Starlette under the old FastAPI constraint. Verify exact
version metadata, build fresh Python 3.11/3.12 environments, preserve the current
test evidence, migrate fixtures, rerun all gateway/queue/protocol regressions,
rerun dependency checks, and record any remaining unreachable advisories with
explicit rationale. Update both Python requirement entry points and the baseline
image consistently. Add a resolved dependency lock/inventory for repeatability.
Container images and browser dependencies need separate scans.

The original task brief says: “Before ... dependency upgrades with significant
compatibility impact ... ask for approval.” Moving the framework and pytest
stack qualifies because it changes middleware/async fixture behavior. The user approved the upgrades during this task. They have now been applied and
retested. The table above records the original proposal for traceability.

Primary references: [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/),
[Starlette Host advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr),
[python-jose releases](https://github.com/mpdavis/python-jose/releases).


## Applied outcome

- FastAPI 0.143.3, Starlette 1.8.0, Pydantic 2.14.1 and pydantic-settings 2.16.0.
- PyJWT 2.15.1 replaces python-jose; unused ECDSA/RSA/JWE dependencies are removed
  from the runtime contract. HS256 claims and public access-token format remain.
- python-multipart 0.0.32; email-validator is explicit because FastAPI core no
  longer supplies the old standard extras implicitly.
- pytest 9.1.1 / pytest-asyncio 1.4.0 with session loop scopes instead of a custom
  deprecated event_loop fixture; Black 26.10.1; local pip 26.2.1.
- Runtime pins have one source (gateway/requirements.txt); development tools and
  fake services are separate. Container runtime no longer installs pytest/Black.
- Next.js and its ESLint config move from 16.2.9 to 16.4.2; compatible transitive
  lockfile updates address the reported critical Next.js and runtime findings.
- Python audit recheck: zero known findings. npm production audit: zero known
  findings. Full npm audit retains five high package findings in a single
  development-only braces/micromatch/fast-glob/ESLint chain, with no compatible
  automated fix. A forced downgrade to the older Next 14 config was not applied.
  See DASHBOARD_DEPENDENCY_AUDIT.json for the advisory and exact scope.

Validation is local Python 3.12 and Node on Windows. See PROJECT_AUDIT.md for final
commands/counts and VERIFICATION_ENVIRONMENT.json for the observed environment.
Image scans, Linux/Python 3.11 execution and live services remain unverified.
