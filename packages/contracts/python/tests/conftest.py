import json
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


@pytest.fixture
def golden():
    def _load(name: str) -> dict:
        return json.loads((GOLDEN_DIR / f"{name}.json").read_text())

    return _load
