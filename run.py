#!/usr/bin/env python
"""
UNGA BUNGA AUTO-COMPLETE — Launcher
====================================
Run this script from the project root directory:

    python run.py                          # launch interactive shell
    python run.py --train corpus.txt       # train then launch
    python run.py --benchmark              # run benchmarks
    python run.py --version                # print version

This script exists so you can launch from the project root without
needing to install the package.  It adds the project root to sys.path
so Python can find the unga_bunga_autocomplete package.
"""

import sys
import os

# Ensure the project root is on sys.path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from unga_bunga_autocomplete.__main__ import main

if __name__ == "__main__":
    main()
