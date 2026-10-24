"""The free Ad Library check moved into the engine (surven_outreach/day7/adlib.py, 10/24). Same command line as before."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from surven_outreach.day7.adlib import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
