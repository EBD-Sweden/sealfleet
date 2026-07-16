"""Runtime hook boundary for MCP tool and pipeline calls.

Hooks are deliberately small and synchronous-configured: the runtime owns the
transport call, invokes pre-call hooks before dispatch, invokes post-call hooks on
returned data, and records audit metadata without exposing raw secrets/PII in
reasons.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9._-]{6,}|api[_-]?key\s*[:=]\s*[^\s,;}]+|token\s*[:=]\s*[^\s,;}]+|secret\s*[:=]\s*[^\s,;}]+)"
)


@dataclass(frozen=True)
class RuntimeHookContext:
    """Stable metadata passed to every runtime hook invocation."""

    trace_id: str
    tenant_id: str
    subject_id: str
    mcp: str
    tool: str
    transport: str
    pipeline_name: str = ""
    # Output field paths declared as PII in the MCP manifest for this tool
    # (dot paths into the result, e.g. "customer.email"). Lists are traversed
    # element-wise; "*" matches any dict key at that level.
    pii_fields: tuple[str, ...] = ()


@dataclass
class HookDecision:
    """A hook decision for pre-call or post-call phase."""

    action: str
    reason: str = ""
    result: Any = None

    @classmethod
    def allow(cls, *, result: Any = None, reason: str = "") -> "HookDecision":
        return cls(action="allow", reason=reason, result=result)

    @classmethod
    def redact(cls, *, result: Any, reason: str = "redacted") -> "HookDecision":
        return cls(action="redact", reason=reason, result=result)

    @classmethod
    def block(cls, reason: str) -> "HookDecision":
        return cls(action="block", reason=reason)


@dataclass
class RuntimeHook:
    """Base class for config-driven runtime hooks."""

    name: str
    phase: str = "both"
    order: int = 100
    block_on_violation: bool = True

    async def pre_call(self, ctx: RuntimeHookContext, payload: Any) -> HookDecision:
        return HookDecision.allow(result=payload)

    async def post_call(self, ctx: RuntimeHookContext, result: Any) -> HookDecision:
        return HookDecision.allow(result=result)

    def applies_to(self, phase: str) -> bool:
        return self.phase in ("both", phase)


@dataclass
class HookManager:
    """Deterministically ordered pre/post hook runner with fail-closed blocks."""

    hooks: list[RuntimeHook] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.hooks = sorted(self.hooks, key=lambda hook: (hook.order, hook.name))

    @property
    def enabled(self) -> bool:
        return bool(self.hooks)

    async def run_pre_call(self, ctx: RuntimeHookContext, payload: Any) -> Any:
        current = payload
        for hook in self._hooks_for("pre"):
            decision = await hook.pre_call(ctx, current)
            current = self._apply_decision(ctx, hook, "pre_call", decision, current)
        return current

    async def run_post_call(self, ctx: RuntimeHookContext, result: Any) -> Any:
        current = result
        for hook in self._hooks_for("post"):
            decision = await hook.post_call(ctx, current)
            current = self._apply_decision(ctx, hook, "post_call", decision, current)
        return current

    def _hooks_for(self, phase: str) -> Iterable[RuntimeHook]:
        return (hook for hook in self.hooks if hook.applies_to(phase))

    def _apply_decision(
        self,
        ctx: RuntimeHookContext,
        hook: RuntimeHook,
        action: str,
        decision: HookDecision,
        current: Any,
    ) -> Any:
        if decision.action == "allow":
            self._audit(ctx, hook, action, "ok", decision.reason)
            return current if decision.result is None else decision.result

        if decision.action == "redact":
            self._audit(ctx, hook, action, "redacted", decision.reason)
            return decision.result

        if decision.action == "block":
            self._audit(ctx, hook, action, "blocked", decision.reason)
            if hook.block_on_violation:
                raise PermissionError(_redact_reason(decision.reason) or "runtime hook blocked call")
            return current

        self._audit(ctx, hook, action, "blocked", "invalid hook decision")
        raise PermissionError("runtime hook returned invalid decision")

    def _audit(
        self,
        ctx: RuntimeHookContext,
        hook: RuntimeHook,
        action: str,
        result: str,
        reason: str = "",
    ) -> None:
        self.audit_events.append(
            {
                "trace_id": ctx.trace_id,
                "hook_name": hook.name,
                "action": action,
                "result": result,
                "tenant": ctx.tenant_id,
                "subject": ctx.subject_id,
                "mcp": ctx.mcp,
                "tool": ctx.tool,
                "transport": ctx.transport,
                "pipeline_name": ctx.pipeline_name,
                "reason": _redact_reason(reason),
                "timestamp": time.time(),
            }
        )


class OutputLengthGuard(RuntimeHook):
    """Post-call output size guard: block or truncate serialized results."""

    def __init__(
        self,
        name: str = "output_length_guard",
        *,
        max_chars: int = 100_000,
        mode: str = "block",
        phase: str = "post",
        order: int = 100,
        block_on_violation: bool = True,
    ):
        super().__init__(name=name, phase=phase, order=order, block_on_violation=block_on_violation)
        self.max_chars = int(max_chars)
        self.mode = mode

    async def post_call(self, ctx: RuntimeHookContext, result: Any) -> HookDecision:
        serialized = _stable_json(result)
        if len(serialized) <= self.max_chars:
            return HookDecision.allow(result=result)
        reason = f"output too long: {len(serialized)} chars exceeds max {self.max_chars}"
        if self.mode == "truncate":
            truncated = _truncate_result(result, self.max_chars)
            if isinstance(truncated, dict):
                metadata = dict(truncated.get("_runtime_hooks", {}))
                metadata.update({"truncated_by": self.name, "max_chars": self.max_chars})
                truncated["_runtime_hooks"] = metadata
            return HookDecision.redact(result=truncated, reason=reason)
        return HookDecision.block(reason)


class SecretsPiiGuard(RuntimeHook):
    """Deterministic testable stub for secrets/PII detection and redaction/block."""

    def __init__(
        self,
        name: str = "secrets_pii_guard",
        *,
        mode: str = "redact",
        phase: str = "post",
        order: int = 100,
        block_on_violation: bool = True,
    ):
        super().__init__(name=name, phase=phase, order=order, block_on_violation=block_on_violation)
        self.mode = mode

    async def pre_call(self, ctx: RuntimeHookContext, payload: Any) -> HookDecision:
        return self._scan(payload)

    async def post_call(self, ctx: RuntimeHookContext, result: Any) -> HookDecision:
        return self._scan(result)

    def _scan(self, value: Any) -> HookDecision:
        redacted, findings = _redact_sensitive(value)
        if not findings:
            return HookDecision.allow(result=value)
        reason = f"sensitive data detected: {', '.join(sorted(set(findings)))}"
        if self.mode == "block":
            return HookDecision.block(reason)
        return HookDecision.redact(result=redacted, reason=reason)


class ManifestPiiGuard(RuntimeHook):
    """Always-on redaction of manifest-declared PII output fields.

    Operators declare `pii_fields` (dot paths) per tool — or MCP-wide — in the
    MCP manifest YAML; the runtime carries them in RuntimeHookContext and this
    hook redacts the declared paths from every tool result before it reaches
    callers, downstream pipeline steps, or logs. Field NAMES are audited,
    values never are.
    """

    def __init__(
        self,
        name: str = "manifest_pii_guard",
        *,
        phase: str = "post",
        order: int = 10,
        block_on_violation: bool = True,
    ):
        super().__init__(name=name, phase=phase, order=order, block_on_violation=block_on_violation)

    async def post_call(self, ctx: RuntimeHookContext, result: Any) -> HookDecision:
        if not ctx.pii_fields:
            return HookDecision.allow(result=result)
        redacted, hit_paths = redact_pii_fields(result, ctx.pii_fields)
        if not hit_paths:
            return HookDecision.allow(result=result)
        reason = f"manifest pii fields redacted: {', '.join(sorted(hit_paths))}"
        return HookDecision.redact(result=redacted, reason=reason)


PII_REDACTED_MARKER = "[REDACTED_PII]"


def redact_pii_fields(value: Any, paths: Iterable[str]) -> tuple[Any, list[str]]:
    """Redact declared dot-path fields inside a result structure.

    Path semantics: segments navigate dicts; lists are traversed element-wise
    without consuming a segment; a "*" segment matches every dict key at that
    level. Returns (redacted_copy, sorted unique declared paths that matched).
    """
    hits: set[str] = set()

    def apply(obj: Any, segments: list[str], declared: str) -> None:
        if not segments:
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                apply(item, segments, declared)
            return
        if not isinstance(obj, dict):
            return
        head, rest = segments[0], segments[1:]
        keys = list(obj.keys()) if head == "*" else ([head] if head in obj else [])
        for key in keys:
            if rest:
                apply(obj[key], rest, declared)
            else:
                if obj[key] != PII_REDACTED_MARKER:
                    obj[key] = PII_REDACTED_MARKER
                    hits.add(declared)

    out = copy.deepcopy(value)
    for path in paths:
        segments = [seg for seg in str(path).split(".") if seg]
        if segments:
            apply(out, segments, str(path))
    return out, sorted(hits)


def build_runtime_hook_manager(config: dict[str, Any] | None = None) -> HookManager:
    """Build a hook manager from a runtime_hooks config mapping.

    Example:
        runtime_hooks:
          enabled: true
          hooks:
            - name: output_length
              type: output_length_guard
              max_chars: 100000
              mode: block
              order: 10
    """

    # Manifest-declared PII redaction is a platform guarantee, not an opt-in
    # config feature: it runs even when the runtime_hooks config is absent.
    builtin_hooks: list[RuntimeHook] = [ManifestPiiGuard()]

    runtime_cfg = (config or {}).get("runtime_hooks", config or {})
    if not runtime_cfg or not runtime_cfg.get("enabled", False):
        return HookManager(builtin_hooks)

    hooks: list[RuntimeHook] = list(builtin_hooks)
    for item in runtime_cfg.get("hooks", []):
        hook_type = item.get("type")
        kwargs = {
            "name": item.get("name") or hook_type,
            "phase": item.get("phase", "post"),
            "order": item.get("order", 100),
            "block_on_violation": item.get("block_on_violation", True),
        }
        if hook_type == "output_length_guard":
            hooks.append(
                OutputLengthGuard(
                    **kwargs,
                    max_chars=item.get("max_chars", 100_000),
                    mode=item.get("mode", "block"),
                )
            )
        elif hook_type == "secrets_pii_guard":
            hooks.append(SecretsPiiGuard(**kwargs, mode=item.get("mode", "redact")))
        else:
            raise ValueError(f"unknown runtime hook type: {hook_type}")
    return HookManager(hooks)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _truncate_result(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, dict):
        remaining = max_chars
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str):
                out[key] = item[: max(0, remaining)]
                remaining -= len(out[key])
            else:
                out[key] = item
        return out
    text = _stable_json(value)[:max_chars]
    return text


def _redact_sensitive(value: Any) -> tuple[Any, list[str]]:
    findings: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        if isinstance(item, tuple):
            return tuple(walk(val) for val in item)
        if isinstance(item, str):
            new = _EMAIL_RE.sub("[REDACTED_EMAIL]", item)
            if new != item:
                findings.append("email")
            newer = _SECRET_RE.sub("[REDACTED_SECRET]", new)
            if newer != new:
                findings.append("secret")
            return newer
        return item

    return walk(copy.deepcopy(value)), findings


def _redact_reason(reason: str) -> str:
    if not reason:
        return ""
    redacted, _ = _redact_sensitive(reason)
    return str(redacted)
