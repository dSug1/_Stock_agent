"""Make ``src/`` importable so ``pytest`` runs without needing PYTHONPATH set.

Repo convention is ``PYTHONPATH=src``; this conftest just makes the same path available when pytest
is invoked from the component root, so ``from platform_discoverer import ...`` resolves in tests.
"""

import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
