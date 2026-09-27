"""Pin every compose image by digest: `python scripts/pin_images.py` (needs Docker and network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crashcourt.pinning import main  # noqa: E402

raise SystemExit(main(Path(__file__).resolve().parents[1] / "compose.yaml"))
