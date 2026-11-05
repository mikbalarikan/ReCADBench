from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent / "examples"


@pytest.fixture
def examples() -> Path:
    return EXAMPLES
