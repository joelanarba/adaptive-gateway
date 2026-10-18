"""Versioned synthetic workload shared by the upstream and study validator."""

import hashlib
import json

WORKLOAD = "optional-heavy-items-v1"
REQUIRED_FIELDS = ("id", "title", "meta.category")
OPTIONAL_FIELDS = ("body", "meta.debug")
PAYLOAD = [
    {
        "id": index,
        "title": f"item {index}",
        "body": "optional-text-" * 160,
        "meta": {"debug": "diagnostic-" * 30, "category": "synthetic"},
    }
    for index in range(100)
]
FIXTURE_SHA256 = hashlib.sha256(
    json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
