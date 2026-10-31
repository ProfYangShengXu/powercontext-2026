import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DATA = PROJECT_ROOT / "data"
SAMPLES = DATA / "samples"
SNAPSHOT = DATA / "snapshots" / "snapshots.json"
EXPECTED = DATA / "expected"
QUERIES = DATA / "queries.json"
WEB = PROJECT_ROOT / "web"
