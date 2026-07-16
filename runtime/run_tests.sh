#!/bin/bash
set -e
cd "$(dirname "$0")"
pip install pytest pytest-asyncio httpx anyio 2>/dev/null | tail -1
python -m pytest tests/ -v --tb=short 2>&1
