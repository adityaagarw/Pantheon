#!/usr/bin/env python3
"""Record this computer for a Pantheon recording, by its code.

    python shadow/recorder.py ABC123 [--backend URL] [--every 15] [--exclude "Payroll"]

With Pantheon Host running you don't need this: pick "My computer" on the Shadow tab and
it starts by itself. This is the same recorder, for one recording at a time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.main import main

if __name__ == "__main__":
    main(["record", *sys.argv[1:]])
