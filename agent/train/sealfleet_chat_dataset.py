"""nemo-automodel ChatDataset that keeps tool-call arguments as dicts.

nemo-automodel >= 26.06 normalises `tool_calls[].function.arguments` to a JSON
string (OpenAI wire format). Nemotron-3-Nano's chat template renders calls with
`tool_call.arguments|items`, which needs a mapping, and Jinja has no JSON
parser — so every tool-calling row fails to render. This wrapper re-parses the
arguments after automodel's normalisation; everything else is unchanged.

Used by agent/train/lora_nemotron3_30b*.yaml (dataset._target_:
sealfleet_chat_dataset.ChatDataset); run_lora.sh puts this directory on
PYTHONPATH inside the container.
"""

import json

from nemo_automodel.components.datasets.llm import chat_dataset as _cd

_normalize_messages = _cd._normalize_messages


def _normalize_messages_dict_args(messages):
    out = _normalize_messages(messages)
    for m in out:
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            if isinstance(fn.get("arguments"), str):
                try:
                    fn["arguments"] = json.loads(fn["arguments"])
                except json.JSONDecodeError:
                    pass
    return out


# ChatDataset.__getitem__ looks the function up in its module at call time.
_cd._normalize_messages = _normalize_messages_dict_args
ChatDataset = _cd.ChatDataset
