"""Sanity-check a generated dataset before training.

Checks every label against the schemas the model is shown: selection targets
name a tool in that example's catalog (or a sentinel / null) and only use its
input keys; platform tool calls name an offered tool, parse as JSON, and carry
every required argument. Also reports duplicates and length percentiles.

    python agent/train/validate_data.py /srv/spark-share/datasets/sealfleet-tools-v1/train.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

SENTINELS = {"__platform__", "__create_pipeline__", None}


def catalog_from_prompt(prompt: str) -> dict[str, set[str]]:
    """Pipeline name -> arg names, parsed back out of the selection prompt."""
    out = {}
    for m in re.finditer(r"^- (\w+): .*\(args — (.*)\)$", prompt, re.M):
        args = set() if m.group(2) == "none" else {a.split(":")[0].strip() for a in re.split(r", (?=\w+:)", m.group(2))}
        out[m.group(1)] = args
    return out


def main(path: str) -> int:
    errors, kinds, lengths, seen, dups = Counter(), Counter(), [], set(), 0
    n = 0
    for line in open(path):
        n += 1
        ex = json.loads(line)
        msgs = ex["messages"]
        key = json.dumps(msgs, sort_keys=True)
        dups += key in seen
        seen.add(key)
        lengths.append(sum(len(str(m.get("content") or "")) for m in msgs)
                       + (len(json.dumps(ex["tools"])) if ex.get("tools") else 0))
        task = ex["meta"]["task"]
        kinds[task] += 1
        if task == "select":
            cat = catalog_from_prompt(msgs[-2]["content"])
            try:
                tgt = json.loads(msgs[-1]["content"])
            except json.JSONDecodeError:
                errors["select: target not JSON"] += 1
                continue
            tool = tgt.get("tool")
            if tool not in SENTINELS and tool not in cat:
                errors["select: tool not in catalog"] += 1
            elif tool in cat and not set(tgt.get("arguments", {})) <= cat[tool]:
                errors["select: unknown argument"] += 1
            continue
        offered = {t["function"]["name"]: t["function"]["parameters"] for t in ex["tools"]}
        ids = set()
        for m in msgs:
            for c in m.get("tool_calls") or []:
                fn = c["function"]
                ids.add(c["id"])
                if fn["name"] not in offered:
                    errors["platform: tool not offered"] += 1
                    continue
                try:
                    args = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    errors["platform: arguments not JSON"] += 1
                    continue
                missing = set(offered[fn["name"]].get("required", [])) - set(args)
                if missing:
                    errors[f"platform: {fn['name']} missing {sorted(missing)}"] += 1
            if m["role"] == "tool" and m["tool_call_id"] not in ids:
                errors["platform: orphan tool result"] += 1
        if msgs[-1]["role"] != "assistant":
            errors["platform: does not end on assistant"] += 1

    lengths.sort()
    pct = {p: lengths[int(p / 100 * (len(lengths) - 1))] for p in (50, 90, 99, 100)}
    print(json.dumps({"examples": n, "by_task": kinds, "duplicates": dups, "errors": errors,
                      "chars_percentiles": pct, "approx_tokens_p99": pct[99] // 3}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
