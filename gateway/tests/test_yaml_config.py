"""
Tests for YAML-based route-rule loading.

These tests exercise the config module's ability to load route rules from
a ``gateway.yaml`` file, fall back to built-in defaults when no file is
found, validate rule entries via the ``RouteRule`` Pydantic model, and
reload rules at runtime.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from config import (
    ROUTE_RULES,
    RouteRule,
    _parse_rules,
    get_loaded_yaml_path,
    get_route_rule,
    load_route_rules,
    reload_route_rules,
)


# ---------------------------------------------------------------------------
# _parse_rules
# ---------------------------------------------------------------------------

class TestParseRules:
    """Unit tests for the raw-dict → RouteRule parser."""

    def test_valid_rules(self):
        raw = {
            "svc1": {"cacheable": True, "cache_ttl": 120, "optional_fields": ["x"]},
            "svc2": {"cache_ttl": 30},
        }
        parsed = _parse_rules(raw)
        assert "svc1" in parsed and "svc2" in parsed
        assert parsed["svc1"].cache_ttl == 120
        assert parsed["svc1"].optional_fields == ["x"]
        assert parsed["svc2"].cacheable is True  # default

    def test_defaults_applied(self):
        """A rule with no explicit keys should get all RouteRule defaults."""
        parsed = _parse_rules({"empty": {}})
        rule = parsed["empty"]
        assert rule.cacheable is True
        assert rule.cache_ttl == 60
        assert rule.optional_fields == []
        assert rule.upstream_timeout is None

    def test_non_dict_rule_skipped(self):
        """Non-mapping entries should be skipped with a warning, not crash."""
        parsed = _parse_rules({"bad": "not-a-dict", "good": {"cache_ttl": 10}})
        assert "bad" not in parsed
        assert "good" in parsed

    def test_invalid_types_skipped(self):
        """Wrong field types should skip the entry, not crash."""
        parsed = _parse_rules({"bad": {"cache_ttl": "not-an-int"}})
        assert "bad" not in parsed


# ---------------------------------------------------------------------------
# load_route_rules — with a real YAML file
# ---------------------------------------------------------------------------

class TestLoadRouteRules:
    """Integration tests for loading from a gateway.yaml file."""

    def test_load_from_yaml(self, tmp_path, monkeypatch):
        """A valid gateway.yaml should populate ROUTE_RULES."""
        yaml_content = textwrap.dedent("""\
            upstreams:
              testapi: https://api.test.com

            rules:
              _default:
                cacheable: true
                cache_ttl: 30
                optional_fields: []

              testapi:
                cacheable: true
                cache_ttl: 180
                optional_fields:
                  - debug_info
                  - internal_id
                upstream_timeout: 5.0
        """)
        yaml_file = tmp_path / "gateway.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        # Patch _find_file to return our temp file.
        monkeypatch.setattr("config._find_file", lambda name: yaml_file if name == "gateway.yaml" else None)

        path = load_route_rules()

        assert path == yaml_file
        assert "_default" in ROUTE_RULES
        assert "testapi" in ROUTE_RULES
        assert ROUTE_RULES["testapi"].cache_ttl == 180
        assert ROUTE_RULES["testapi"].optional_fields == ["debug_info", "internal_id"]
        assert ROUTE_RULES["testapi"].upstream_timeout == 5.0

    def test_fallback_when_no_yaml(self, monkeypatch):
        """No gateway.yaml → built-in defaults, no crash."""
        monkeypatch.setattr("config._find_file", lambda name: None)

        path = load_route_rules()

        assert path is None
        assert "_default" in ROUTE_RULES
        assert "jsonplaceholder" in ROUTE_RULES  # built-in default
        assert get_loaded_yaml_path() == "(built-in defaults)"

    def test_malformed_yaml_falls_back(self, tmp_path, monkeypatch):
        """Broken YAML syntax should fall back to defaults, not crash."""
        bad_yaml = tmp_path / "gateway.yaml"
        bad_yaml.write_text("rules:\n  bad: [unmatched", encoding="utf-8")
        monkeypatch.setattr("config._find_file", lambda name: bad_yaml if name == "gateway.yaml" else None)

        path = load_route_rules()

        # Should fall back to defaults.
        assert path is None
        assert "_default" in ROUTE_RULES

    def test_yaml_without_rules_section(self, tmp_path, monkeypatch):
        """YAML with only upstreams and no rules → fall back to defaults."""
        yaml_content = "upstreams:\n  svc: https://example.com\n"
        yaml_file = tmp_path / "gateway.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr("config._find_file", lambda name: yaml_file if name == "gateway.yaml" else None)

        path = load_route_rules()

        assert path is None
        assert "_default" in ROUTE_RULES

    def test_upstreams_merged(self, tmp_path, monkeypatch):
        """YAML upstreams should merge with (and override) env-var upstreams."""
        from config import settings

        # Simulate an env-var upstream.
        original = dict(settings.upstream_services)
        settings.upstream_services = {"existing": "https://existing.com"}

        yaml_content = textwrap.dedent("""\
            upstreams:
              newapi: https://new.com

            rules:
              _default:
                cache_ttl: 60
        """)
        yaml_file = tmp_path / "gateway.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr("config._find_file", lambda name: yaml_file if name == "gateway.yaml" else None)

        load_route_rules()

        # Both should be present.
        assert "newapi" in settings.upstream_services
        assert "existing" in settings.upstream_services

        # Cleanup.
        settings.upstream_services = original


# ---------------------------------------------------------------------------
# reload_route_rules
# ---------------------------------------------------------------------------

class TestReloadRouteRules:
    """Tests for the hot-reload function."""

    def test_reload_returns_rules(self, tmp_path, monkeypatch):
        yaml_content = textwrap.dedent("""\
            rules:
              _default:
                cache_ttl: 99
              hot: 
                cache_ttl: 42
                optional_fields: ["x"]
        """)
        yaml_file = tmp_path / "gateway.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")
        monkeypatch.setattr("config._find_file", lambda name: yaml_file if name == "gateway.yaml" else None)

        path, rules = reload_route_rules()

        assert path == yaml_file
        assert "hot" in rules
        assert rules["hot"].cache_ttl == 42

    def test_reload_updates_global(self, tmp_path, monkeypatch):
        """reload_route_rules should update the module-level ROUTE_RULES dict."""
        yaml_v1 = textwrap.dedent("""\
            rules:
              _default:
                cache_ttl: 10
              alpha:
                cache_ttl: 100
        """)
        yaml_v2 = textwrap.dedent("""\
            rules:
              _default:
                cache_ttl: 20
              beta:
                cache_ttl: 200
        """)
        yaml_file = tmp_path / "gateway.yaml"
        yaml_file.write_text(yaml_v1, encoding="utf-8")
        monkeypatch.setattr("config._find_file", lambda name: yaml_file if name == "gateway.yaml" else None)

        reload_route_rules()
        assert "alpha" in ROUTE_RULES
        assert "beta" not in ROUTE_RULES

        # Simulate editing the file.
        yaml_file.write_text(yaml_v2, encoding="utf-8")
        reload_route_rules()

        assert "beta" in ROUTE_RULES
        assert "alpha" not in ROUTE_RULES  # cleared and replaced


# ---------------------------------------------------------------------------
# get_route_rule — accessor with fallback
# ---------------------------------------------------------------------------

class TestGetRouteRule:
    """Tests for the get_route_rule accessor."""

    def test_known_service(self, monkeypatch):
        monkeypatch.setattr("config._find_file", lambda name: None)
        load_route_rules()  # load defaults
        rule = get_route_rule("jsonplaceholder")
        assert rule.cache_ttl == 300

    def test_unknown_service_returns_default(self, monkeypatch):
        monkeypatch.setattr("config._find_file", lambda name: None)
        load_route_rules()
        rule = get_route_rule("nonexistent")
        assert rule == ROUTE_RULES["_default"]
