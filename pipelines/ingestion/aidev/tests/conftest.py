import sys
from pathlib import Path

import pytest

# Lets `python -m pytest pipelines/ingestion/aidev/tests` run from the repo root
# without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import build_world  # noqa: E402


@pytest.fixture
def world(tmp_path: Path):
    """(inventory, source files by table, folder to download into)"""
    return build_world(tmp_path)
