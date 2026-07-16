#!/usr/bin/env python3
"""Compatibility wrapper for the canonical Sealfleet MCP server CLI.

The maintained entrypoint is `python -m runtime.cli`. This file remains only so
older docs/scripts that invoke scripts/mcpfinder_cli.py execute the same code.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
