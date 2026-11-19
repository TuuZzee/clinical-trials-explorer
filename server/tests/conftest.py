import json
from pathlib import Path

import pytest


@pytest.fixture
def synthetic_study():
    return json.loads((Path(__file__).parent / "fixtures" / "study.json").read_text())
