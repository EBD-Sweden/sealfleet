"""Centralized policy decision engine.

Evaluates access control decisions based on identity, action, resource,
and context. Supports loading policy packs from YAML files.

MVP: Rule-based evaluation with YAML-defined policies.
Later: OPA integration, Cedar policies, real-time policy updates.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("mcpfinder.policy")


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REDACT = "redact"


@dataclass
class PolicyContext:
    """Context for a policy evaluation request."""
    user_id: str
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    action: str = ""       # e.g., "call_tool", "list_tools"
    resource: str = ""     # e.g., "crypto.price_quote"
    environment: dict[str, Any] = field(default_factory=dict)  # time, ip, etc.


@dataclass
class PolicyResult:
    """Result of a policy evaluation."""
    decision: Decision
    rule_name: str = ""
    reason: str = ""
    redactions: list[str] = field(default_factory=list)
    evaluated_at: float = 0.0
    evaluation_time_ms: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.decision in (Decision.ALLOW, Decision.REDACT)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "rule_name": self.rule_name,
            "reason": self.reason,
            "redactions": self.redactions,
            "evaluated_at": self.evaluated_at,
            "evaluation_time_ms": self.evaluation_time_ms,
        }


@dataclass
class PolicyRule:
    """A policy rule loaded from a pack."""
    name: str
    decision: Decision
    priority: int = 0  # Higher = evaluated first
    # Match conditions (None = match all)
    match_roles: Optional[list[str]] = None
    match_scopes: Optional[list[str]] = None
    match_actions: Optional[list[str]] = None
    match_resources: Optional[list[str]] = None
    reason: str = ""
    redactions: list[str] = field(default_factory=list)

    def matches(self, ctx: PolicyContext) -> bool:
        """Check if this rule applies to the given context."""
        if self.match_roles is not None:
            if not any(r in ctx.roles for r in self.match_roles):
                return False
        if self.match_scopes is not None:
            if not any(s in ctx.scopes for s in self.match_scopes):
                return False
        if self.match_actions is not None:
            if ctx.action not in self.match_actions:
                return False
        if self.match_resources is not None:
            if not self._resource_matches(ctx.resource):
                return False
        return True

    def _resource_matches(self, resource: str) -> bool:
        for pattern in (self.match_resources or []):
            if pattern == "*":
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if resource == prefix or resource.startswith(prefix + "."):
                    return True
            elif pattern == resource:
                return True
        return False


class PolicyEngine:
    """Central policy decision point.

    Loads rules from policy packs (YAML) and evaluates them against
    incoming requests. Deny rules always take priority.
    """

    def __init__(self):
        self._rules: list[PolicyRule] = []
        self._default_decision = Decision.DENY
        self._packs_loaded: list[str] = []

    def load_pack(self, pack_path: str) -> int:
        """Load a policy pack from a YAML file.

        Args:
            pack_path: Path to the YAML policy pack file.

        Returns:
            Number of rules loaded.
        """
        path = Path(pack_path)
        if not path.exists():
            raise FileNotFoundError(f"Policy pack not found: {pack_path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        pack_name = data.get("name", path.stem)
        rules_data = data.get("rules", [])
        count = 0

        for rd in rules_data:
            match = rd.get("match", {})
            rule = PolicyRule(
                name=f"{pack_name}/{rd['name']}",
                decision=Decision(rd["decision"]),
                priority=rd.get("priority", 0),
                match_roles=match.get("roles"),
                match_scopes=match.get("scopes"),
                match_actions=match.get("actions"),
                match_resources=match.get("resources"),
                reason=rd.get("reason", ""),
                redactions=rd.get("redactions", []),
            )
            self._rules.append(rule)
            count += 1

        # Sort by priority (highest first), then deny before allow at same priority
        self._rules.sort(
            key=lambda r: (r.priority, r.decision == Decision.DENY),
            reverse=True,
        )
        self._packs_loaded.append(pack_name)
        logger.info("Loaded policy pack: %s (%d rules)", pack_name, count)
        return count

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a rule programmatically."""
        self._rules.append(rule)
        self._rules.sort(
            key=lambda r: (r.priority, r.decision == Decision.DENY),
            reverse=True,
        )

    def evaluate(self, ctx: PolicyContext) -> PolicyResult:
        """Evaluate a policy decision.

        Rules are evaluated in priority order (highest first).
        Within the same priority, deny rules are checked before allow.
        First matching rule wins.

        Args:
            ctx: The policy evaluation context.

        Returns:
            PolicyResult with the decision.
        """
        start = time.time()

        for rule in self._rules:
            if rule.matches(ctx):
                return PolicyResult(
                    decision=rule.decision,
                    rule_name=rule.name,
                    reason=rule.reason or f"Decided by {rule.name}",
                    redactions=rule.redactions,
                    evaluated_at=start,
                    evaluation_time_ms=(time.time() - start) * 1000,
                )

        # Default
        return PolicyResult(
            decision=self._default_decision,
            rule_name="default",
            reason="No matching rule — default policy applied",
            evaluated_at=start,
            evaluation_time_ms=(time.time() - start) * 1000,
        )

    def set_default(self, decision: Decision) -> None:
        self._default_decision = decision

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    @property
    def packs_loaded(self) -> list[str]:
        return list(self._packs_loaded)
