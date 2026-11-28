"""``python -m harness_builder``: the host-side command line (see cli.py)."""
import sys

from .cli import main

sys.exit(main())
