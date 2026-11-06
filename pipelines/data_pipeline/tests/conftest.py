import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# `scripts` is imported from the pipeline folder, and the AIDev package's fake dataset and fake
# downloader are reused so these tests never touch the network.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "ingestion" / "aidev" / "tests"))

from helpers import REV, FakeFetcher, build_world  # noqa: E402


@pytest.fixture
def world(tmp_path: Path):
    """(inventory, fake downloader, inventory file, params for two tables, folder to download into)"""
    inventory, files, out = build_world(tmp_path)
    expected = tmp_path / "inventory.json"
    expected.write_text(json.dumps(inventory), encoding="utf-8")
    params = {"commit": REV, "tables": ["pull_request", "repository"]}
    return inventory, FakeFetcher(files), expected, params, out


@pytest.fixture(autouse=True)
def clean_root_logger():
    """setup_logging() installs handlers on the root logger; remove them after each test."""
    before = list(logging.getLogger().handlers)
    yield
    root = logging.getLogger()
    for handler in [h for h in root.handlers if h not in before]:
        root.removeHandler(handler)
        handler.close()
