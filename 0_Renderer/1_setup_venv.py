#!/usr/bin/env python3
"""
1_setup_venv.py
Creates the .venv virtual environment at the project root and installs
all dependencies. Run this ONCE before using 2_stock_visualizer.py.

Usage:
    python 1_setup_venv.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
VENV = ROOT / ".venv"
REQ  = ROOT / "requirements.txt"

PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main():
    print("=" * 55)
    print("  Stock Visualizer — Environment Setup")
    print("=" * 55)

    if VENV.exists():
        print(f"\n[1/2] .venv already exists at {VENV} — skipping creation.")
    else:
        print(f"\n[1/2] Creating virtual environment at {VENV} …")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        print("      Done.")

    print(f"\n[2/2] Installing dependencies from {REQ.name} …")
    # Use python -m pip for self-upgrade (avoids Windows venv pip.exe lock bug)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "--upgrade", "pip"], check=False)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "-r", str(REQ)], check=True)
    print("      Done.")

    print("\n" + "=" * 55)
    print("  Setup complete!")
    print()
    if sys.platform == "win32":
        print(r"  Activate venv :  .venv\Scripts\activate")
        print(r"  Run visualizer:  0_Renderer\run.bat")
    else:
        print("  Activate venv :  source .venv/bin/activate")
        print("  Run visualizer:  .venv/bin/python 0_Renderer/2_stock_visualizer.py")
    print("=" * 55)


if __name__ == "__main__":
    main()
