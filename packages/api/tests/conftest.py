import sys
from pathlib import Path

# ``--import-mode=importlib`` puts no test directory on ``sys.path``, so a
# test file cannot import a helper module beside it; ``api_fakes.py`` is one.
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from api_fakes import REQUIRED, Signer

from htrflow_api.config import ApiConfig


@pytest.fixture
def cfg() -> ApiConfig:
    return ApiConfig.from_env(REQUIRED)


@pytest.fixture
def signer() -> Signer:
    return Signer()
