#!/usr/bin/env python3
"""
Launch the MEXC × DEX Arbitrage Monitor with a tkinter GUI dashboard.

Usage:
    python run_gui.py
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from gui.dashboard import Dashboard  # noqa: E402


def main():
    dashboard = Dashboard(project_root=str(project_root))
    dashboard.run()


if __name__ == "__main__":
    main()
