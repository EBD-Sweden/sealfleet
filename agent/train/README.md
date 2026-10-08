# Sealfleet planner LoRA data

Fine-tuning data for the chat planner (selection + platform tool calls), built
from the live tool catalog and the agent's own prompt builders, so the model
trains on exactly what production sends.

1. **Snapshot the catalog** (core-agent pod; read-only calls only):
   `snapshot_catalog.py` → `catalog.json` (gitignored — it is deployment data).
2. **Generate**: `gen_data.py --catalog agent/train/catalog.json --out <dir>`
   → `train.jsonl`, `val.jsonl`, `stats.json`. OpenAI chat format; platform
   rows carry `tools`. Rows start with a `/no_think` system turn (Nemotron
   reasoning off) — serve with `LOCAL_LLM_SYSTEM_PREFIX=/no_think` so inference
   matches. Benchmark questions (`agent/eval/cases.yaml`) are excluded.
   For **Nemotron-3-Nano-30B-A3B with nemo-automodel** generate with
   `--system-prefix '' --args-format dict --expand-turns`: its chat template
   iterates `arguments|items` (dict, not string), switches reasoning with the
   `enable_thinking` template flag (not `/no_think`), and `ChatDataset` only
   computes loss on the final assistant turn, so each trajectory is split into
   one example per assistant turn (after the train/val split).
3. **Validate**: `validate_data.py <dir>/train.jsonl` — labels vs schemas,
   duplicates, length percentiles.

Run the generator where `agent/server.py` imports (openai, httpx, pyyaml),
e.g. the `sealfleet-runtime` image with `pip install -r agent/requirements.txt`.

Measure before/after with `agent/eval/run_eval.py`.

## Training (Nemotron-3-Nano-30B-A3B, one GB10)

    CONFIG=lora_nemotron3_30b_hf.yaml agent/train/run_lora.sh <run-name>

`run_lora.sh` runs nemo-automodel **26.06** (26.02's Mamba kernels are x86-only)
with a host-memory cgroup cap plus a MemAvailable watchdog: on GB10 the GPU
shares the 121 GB pool and CUDA allocations are not charged to the cgroup.
Pitfalls this setup works around:

- automodel's custom NemotronH loads the full state dict on CPU before copying
  it to the GPU (60 + 60 GB) and cannot fit; `lora_nemotron3_30b_hf.yaml` uses
  the HF class (`force_hf`), which streams to the GPU (~70 GB peak).
- 26.06's ChatDataset serialises tool-call arguments to strings; the Nemotron
  template needs dicts → `sealfleet_chat_dataset.ChatDataset`.
- The PEFT meta-device build aliases `lm_head` to the input embeddings
  although the model is untied (step-0 loss ~12.6) → `train_sealfleet.py`
  unties it and loads the checkpoint lm_head (step-0 loss ~1.9). `lm_head` is
  excluded from LoRA.
