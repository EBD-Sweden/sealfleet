#!/usr/bin/env bash
# Run the Nemotron-3-Nano-30B LoRA SFT in nemo-automodel 26.06 on this GB10.
#
#   agent/train/run_lora.sh <run-name> [extra automodel overrides...]
#   agent/train/run_lora.sh smoke --step_scheduler.max_steps=10
#
# The container gets a hard memory cap (cgroup) so a runaway is killed inside
# it instead of triggering the host's global OOM killer — GB10 memory is one
# unified pool shared with the GPU and the k3s pods.
#
# Env: CONFIG (yaml in agent/train), MODEL_DIR, DATA_DIR, OUT_ROOT, MEM_CAP (default 80g), IMAGE.
set -euo pipefail

RUN=${1:?usage: run_lora.sh <run-name> [overrides...]}
shift
IMAGE=${IMAGE:-nvcr.io/nvidia/nemo-automodel:26.06}
MODEL_DIR=${MODEL_DIR:-/srv/spark-share/models/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16}
DATA_DIR=${DATA_DIR:-/srv/spark-share/datasets/sealfleet-tools-v1-nemotron3}
OUT_ROOT=${OUT_ROOT:-/srv/spark-share/runs/sealfleet-lora}
MEM_CAP=${MEM_CAP:-80g}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$OUT_ROOT/$RUN"
mkdir -p "$OUT"

# Refuse to start without headroom: the cap must sit below what is free.
avail_gb=$(awk '/MemAvailable/ {print int($2/1024/1024)}' /proc/meminfo)
cap_gb=${MEM_CAP%g}
if (( avail_gb < cap_gb + 8 )); then
  echo "Only ${avail_gb} GB available; need ${cap_gb} GB cap + 8 GB margin. Free memory first." >&2
  exit 1
fi

# The cgroup cap above only bounds host-side memory: CUDA allocations on GB10
# come from the same pool but are not charged to memory.max. So also watch
# MemAvailable and stop the run before the host's global OOM killer starts
# picking off k8s pods.
MIN_AVAIL_GB=${MIN_AVAIL_GB:-12}
(
  while sleep 5; do
    docker inspect "sealfleet-lora-$RUN" >/dev/null 2>&1 || { sleep 5; docker inspect "sealfleet-lora-$RUN" >/dev/null 2>&1 || exit 0; }
    a=$(awk '/MemAvailable/ {print int($2/1024/1024)}' /proc/meminfo)
    if (( a < MIN_AVAIL_GB )); then
      echo "[watchdog] MemAvailable ${a} GB < ${MIN_AVAIL_GB} GB — stopping sealfleet-lora-$RUN" | tee -a "$OUT/train.log" >&2
      docker kill "sealfleet-lora-$RUN" >/dev/null 2>&1
      exit 0
    fi
  done
) &
WATCHDOG=$!
trap 'kill $WATCHDOG 2>/dev/null' EXIT

docker run --rm --name "sealfleet-lora-$RUN" \
  --gpus all --ipc=host --ulimit memlock=-1 \
  --memory="$MEM_CAP" --memory-swap="$MEM_CAP" \
  -e HF_HUB_OFFLINE=1 -e HF_HOME=/tmp/hf \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -v "$MODEL_DIR":/models/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16:ro \
  -v "$DATA_DIR":/data:ro \
  -v "$HERE/${CONFIG:-lora_nemotron3_30b.yaml}":/cfg/lora.yaml:ro \
  -v "$HERE/sealfleet_chat_dataset.py":/cfg/sealfleet_chat_dataset.py:ro \
  -v "$HERE/train_sealfleet.py":/cfg/train_sealfleet.py:ro \
  -e PYTHONPATH=/cfg \
  -v "$OUT":/out \
  -w /opt/Automodel \
  --entrypoint python "$IMAGE" \
  /cfg/train_sealfleet.py --config /cfg/lora.yaml "$@" 2>&1 | tee "$OUT/train.log"
