#!/usr/bin/env python3
"""
1_setup_venv.py
Creates the .venv virtual environment and installs all dependencies.
Run this ONCE before using 2_stock_visualizer.py.


Usage:
    python 1_setup_venv.py
"""

import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
REQ  = ROOT / "requirements.txt"


def main():
    print("=" * 55)
    print("  Stock Visualizer — Environment Setup")
    print("=" * 55)

    # ── 1. Create virtual environment ────────────────────────
    if VENV.exists():
        print(f"\n[1/2] .venv already exists at {VENV} — skipping creation.")
    else:
        print(f"\n[1/2] Creating virtual environment at {VENV} …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        print("      Done.")

    # ── 2. Locate pip inside the venv ────────────────────────
    if sys.platform == "win32":
        pip    = VENV / "Scripts" / "pip.exe"
        python = VENV / "Scripts" / "python.exe"
    else:
        pip    = VENV / "bin" / "pip"
        python = VENV / "bin" / "python"

    # ── 3. Install / upgrade requirements ────────────────────
    print(f"\n[2/2] Installing dependencies from {REQ.name} …")
    # Use python -m pip for self-upgrade (avoids Windows venv pip.exe lock bug)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=False)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(REQ)], check=True)
    print("      Done.")

    # ── 4. Final instructions ────────────────────────────────
    print("\n" + "=" * 55)
    print("  Setup complete!")
    print()
    if sys.platform == "win32":
        activate = r".venv\Scripts\activate"
        run_cmd  = r".venv\Scripts\python.exe 2_stock_visualizer.py"
    else:
        activate = "source .venv/bin/activate"
        run_cmd  = ".venv/bin/python 2_stock_visualizer.py"

    print(f"  Activate venv :  {activate}")
    print(f"  Run visualizer:  {run_cmd}")
    print("=" * 55)


if __name__ == "__main__":
    main()
