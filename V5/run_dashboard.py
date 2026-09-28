#!/usr/bin/env python3
"""
Launch the MEXC × DEX Arbitrage web dashboard.

The bot itself runs headless via `python main.py`. This serves a
browser dashboard over the data the bot writes.

Usage:
    python run_dashboard.py [--port 8000] [--host 127.0.0.1]
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))


def main() -> None:
    parser = argparse.ArgumentParser(description="Arbitrage web dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    print(f"Dashboard: http://{args.host}:{args.port}")
    uvicorn.run("dashboard.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
