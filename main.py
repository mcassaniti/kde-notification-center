#!/usr/bin/env python3
'''KDE Notification Center - Development Launcher'''

import sys
from pathlib import Path

# Add project root to sys.path so package is importable when running from source checkout
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from knotification_center.main import main

if __name__ == "__main__":
    main()
