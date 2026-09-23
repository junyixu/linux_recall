"""XDG locations. Everything is named ``linux_recall`` so it's obvious which program owns it."""

import os
from pathlib import Path

APP = "linux_recall"

# captures (screenshots + JSON sidecars): the data worth keeping
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
# log, temporary KWin scripts: safe to delete at any time
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP
