"""`python -m deputy` runs the same command line as the `deputy` script."""

import sys

from deputy.cli import main

sys.exit(main())
