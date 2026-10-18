"""Entry point of the installed (PyInstaller) app: the same as `python -m wta`."""

import sys

from wta.app import main

if __name__ == "__main__":
    sys.exit(main())
