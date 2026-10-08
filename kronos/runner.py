#!/usr/bin/env python3
"""Kronos's runner is now part of Pantheon Host; this keeps the old command working.

    python kronos/runner.py [run|allow|disallow|list] ...   ->   python host/main.py ...
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.main import main

if __name__ == "__main__":
    main()
