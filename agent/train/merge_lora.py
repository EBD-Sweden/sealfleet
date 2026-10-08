"""Merge a nemo-automodel/PEFT LoRA adapter into a safetensors base, streaming.

W' = W + (lora_alpha / r) * B @ A, one base shard at a time, so host memory
stays at about one shard (~5 GB) — the merged model never sits in RAM at once,
which matters on GB10's unified memory. Writes a servable model directory
(same shard layout + index, base config/tokenizer files copied).

    python merge_lora.py <base_dir> <adapter_dir> <out_dir>

Refuses adapters that need a different merge (DoRA, rank-scaled MoE LoRA).
"""

import json
import os
import shutil
import sys

import torch
from safetensors import safe_open
from safetensors.torch import save_file


def main(base_dir: str, adapter_dir: str, out_dir: str) -> None:
    cfg = json.load(open(os.path.join(adapter_dir, "adapter_config.json")))
    am = os.path.join(adapter_dir, "automodel_peft_config.json")
    extra = json.load(open(am)) if os.path.exists(am) else {}
    if extra.get("use_dora") or extra.get("moe_rank_scaling") or cfg.get("use_rslora"):
        raise SystemExit(f"unsupported adapter options: {extra} {cfg.get('use_rslora')}")
    scale = cfg["lora_alpha"] / cfg["r"]

    adapter = {}
    with safe_open(os.path.join(adapter_dir, "adapter_model.safetensors"), "pt") as f:
        for k in f.keys():
            adapter[k] = f.get_tensor(k)
    prefix = "base_model.model."
    pairs = {}
    for k in adapter:
        if k.endswith(".lora_A.weight"):
            target = k[len(prefix):-len(".lora_A.weight")] + ".weight"
            pairs[target] = (adapter[k], adapter[k.replace(".lora_A.", ".lora_B.")])
    print(f"adapter: {len(pairs)} LoRA pairs, scale {scale}", flush=True)

    os.makedirs(out_dir, exist_ok=True)
    index = json.load(open(os.path.join(base_dir, "model.safetensors.index.json")))
    merged = set()
    for shard in sorted(set(index["weight_map"].values())):
        tensors = {}
        with safe_open(os.path.join(base_dir, shard), "pt") as f:
            meta = f.metadata()
            for k in f.keys():
                t = f.get_tensor(k)
                if k in pairs:
                    a, b = pairs[k]
                    delta = (b.float() @ a.float()) * scale
                    if delta.shape != t.shape:
                        raise SystemExit(f"shape mismatch for {k}: {tuple(delta.shape)} vs {tuple(t.shape)}")
                    t = (t.float() + delta).to(t.dtype)
                    merged.add(k)
                tensors[k] = t.contiguous()
        save_file(tensors, os.path.join(out_dir, shard), metadata=meta or {"format": "pt"})
        print(f"  {shard}: {len(tensors)} tensors", flush=True)
        del tensors

    missing = set(pairs) - merged
    if missing:
        raise SystemExit(f"{len(missing)} LoRA targets not found in base, e.g. {sorted(missing)[:3]}")
    for name in os.listdir(base_dir):
        if not name.endswith(".safetensors") and os.path.isfile(os.path.join(base_dir, name)):
            shutil.copy2(os.path.join(base_dir, name), os.path.join(out_dir, name))
    json.dump({"base": base_dir, "adapter": adapter_dir, "merged_tensors": len(merged), "scale": scale},
              open(os.path.join(out_dir, "sealfleet_merge.json"), "w"), indent=2)
    print(f"merged {len(merged)} tensors into {out_dir}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
