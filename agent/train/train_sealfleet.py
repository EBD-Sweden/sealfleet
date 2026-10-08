"""nemo-automodel fine-tune entrypoint for the Sealfleet LoRA (replaces finetune.py).

Same as examples/llm_finetune/finetune.py, plus one fix after setup():

Nemotron-3-Nano has UNTIED embeddings (tie_word_embeddings: false), but when
nemo-automodel 26.06 builds the PEFT model on the meta device (force_hf path)
`lm_head.weight` ends up aliased to `backbone.embeddings.weight`. The real
checkpoint lm_head is never used: predictions are garbage and the step-0 loss
is ~12.6 instead of ~2-3. After setup we untie lm_head and load the checkpoint
lm_head into it (frozen; it is excluded from LoRA, so the optimizer — built in
setup() — never references it).

Run via agent/train/run_lora.sh.
"""

import json
import os

import torch
from safetensors import safe_open

from nemo_automodel.components.config._arg_parser import parse_args_and_load_config
from nemo_automodel.recipes.llm.train_ft import TrainFinetuneRecipeForNextTokenPrediction


def untie_lm_head(model: torch.nn.Module, model_dir: str) -> None:
    config = getattr(model, "config", None)
    text_config = config.get_text_config() if hasattr(config, "get_text_config") else config
    head, emb = model.get_output_embeddings(), model.get_input_embeddings()
    if getattr(text_config, "tie_word_embeddings", True) or head.weight.data_ptr() != emb.weight.data_ptr():
        return
    index = json.load(open(os.path.join(model_dir, "model.safetensors.index.json")))["weight_map"]
    with safe_open(os.path.join(model_dir, index["lm_head.weight"]), "pt") as f:
        weight = f.get_tensor("lm_head.weight")
    head.weight = torch.nn.Parameter(weight.to(device=emb.weight.device, dtype=emb.weight.dtype), requires_grad=False)
    print(f"[sealfleet] untied lm_head and loaded checkpoint lm_head {tuple(weight.shape)}", flush=True)


def main() -> None:
    cfg = parse_args_and_load_config("/cfg/lora.yaml")
    recipe = TrainFinetuneRecipeForNextTokenPrediction(cfg)
    recipe.setup()
    for part in recipe.model_parts:
        untie_lm_head(part, cfg.model.pretrained_model_name_or_path)
    recipe.run_train_validation_loop()


if __name__ == "__main__":
    main()
