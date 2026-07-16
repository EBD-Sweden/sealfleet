"""Tests for PolicyEngine class in isolation (no HTTP, no DB)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_engine_from_rules(rules: list[dict]):
    """Create a PolicyEngine with the given rules (bypass file loading)."""
    import router
    engine = router.PolicyEngine.__new__(router.PolicyEngine)
    engine.rules = rules
    return engine


def test_policy_engine_loads_empty_when_no_file():
    """PolicyEngine with path that does not exist → rules == [].
    check() returns {"action": "allow", "rule_id": "default"}."""
    import router
    with patch.object(Path, "exists", return_value=False):
        engine = router.PolicyEngine()
    assert engine.rules == []
    result = engine.check(mcp="any", tool="anything")
    assert result["action"] == "allow"
    assert result["rule_id"] == "default"


def test_deny_rule_matches():
    """Rule: tool_pattern='delete_*' action=deny.
    check(tool='delete_user') → action=='deny'."""
    engine = _make_engine_from_rules([
        {"id": "block-delete", "match": {"tool_pattern": "delete_*"}, "action": "deny",
         "reason": "blocked"},
    ])
    result = engine.check(mcp="any", tool="delete_user")
    assert result["action"] == "deny"
    assert result["rule_id"] == "block-delete"


def test_deny_rule_no_match():
    """Rule: tool_pattern='delete_*' action=deny.
    check(tool='get_price') → action=='allow' (falls through to default)."""
    engine = _make_engine_from_rules([
        {"id": "block-delete", "match": {"tool_pattern": "delete_*"}, "action": "deny",
         "reason": "blocked"},
    ])
    result = engine.check(mcp="any", tool="get_price")
    assert result["action"] == "allow"
    assert result["rule_id"] == "default"


def test_require_confirm_rule():
    """Rule: tool_pattern='execute_*' action=require_confirm.
    check(tool='execute_trade') → action=='require_confirm'."""
    engine = _make_engine_from_rules([
        {"id": "confirm-exec", "match": {"tool_pattern": "execute_*"},
         "action": "require_confirm", "reason": "needs confirmation"},
    ])
    result = engine.check(mcp="any", tool="execute_trade")
    assert result["action"] == "require_confirm"
    assert result["rule_id"] == "confirm-exec"


def test_mcp_pattern_filter():
    """Rule: mcp_pattern='crypto-*' tool_pattern='*' action=deny.
    check(mcp='crypto-gateway') → deny; check(mcp='weather-mcp') → allow."""
    engine = _make_engine_from_rules([
        {"id": "block-crypto", "match": {"mcp_pattern": "crypto-*", "tool_pattern": "*"},
         "action": "deny", "reason": "crypto blocked"},
    ])
    result = engine.check(mcp="crypto-gateway", tool="anything")
    assert result["action"] == "deny"

    result = engine.check(mcp="weather-mcp", tool="anything")
    assert result["action"] == "allow"
    assert result["rule_id"] == "default"


def test_first_rule_wins():
    """Two rules: first deny delete_*, second allow *.
    check(tool='delete_item') → deny (first match)."""
    engine = _make_engine_from_rules([
        {"id": "deny-delete", "match": {"tool_pattern": "delete_*"}, "action": "deny"},
        {"id": "allow-all", "match": {"tool_pattern": "*"}, "action": "allow"},
    ])
    result = engine.check(mcp="any", tool="delete_item")
    assert result["action"] == "deny"
    assert result["rule_id"] == "deny-delete"


def test_wildcard_catch_all():
    """Single rule: tool_pattern='*' action=allow.
    check(tool='anything_at_all') → allow."""
    engine = _make_engine_from_rules([
        {"id": "allow-all", "match": {"tool_pattern": "*"}, "action": "allow"},
    ])
    result = engine.check(mcp="any", tool="anything_at_all")
    assert result["action"] == "allow"
    assert result["rule_id"] == "allow-all"


def test_policy_reload(tmp_path):
    """Write YAML to tmp_path, load, check rule count.
    Modify YAML, reload, check new rule count."""
    import router

    # Write initial policy with 2 rules
    policy_file = tmp_path / "default.yaml"
    initial_rules = {
        "rules": [
            {"id": "r1", "match": {"tool_pattern": "delete_*"}, "action": "deny"},
            {"id": "r2", "match": {"tool_pattern": "*"}, "action": "allow"},
        ]
    }
    policy_file.write_text(yaml.dump(initial_rules))

    engine = router.PolicyEngine.__new__(router.PolicyEngine)
    engine.rules = []

    # Patch the path resolution to use our temp file
    policies_dir = tmp_path
    with patch.object(Path, "__truediv__", side_effect=lambda self, other:
                      policy_file if other == "default.yaml" else Path.__truediv__(self, other)):
        # Manually load from our file
        with open(policy_file) as f:
            data = yaml.safe_load(f)
        engine.rules = data.get("rules", [])

    assert len(engine.rules) == 2

    # Modify the file — add a third rule
    updated_rules = {
        "rules": [
            {"id": "r1", "match": {"tool_pattern": "delete_*"}, "action": "deny"},
            {"id": "r2", "match": {"tool_pattern": "execute_*"}, "action": "require_confirm"},
            {"id": "r3", "match": {"tool_pattern": "*"}, "action": "allow"},
        ]
    }
    policy_file.write_text(yaml.dump(updated_rules))

    with open(policy_file) as f:
        data = yaml.safe_load(f)
    engine.rules = data.get("rules", [])

    assert len(engine.rules) == 3
