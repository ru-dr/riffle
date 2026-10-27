import sys
from pathlib import Path

# Lets `python -m pytest pipelines/ingestion/aidev/tests` run from the repo root
# without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
