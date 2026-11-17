import sys
from pathlib import Path

# ``--import-mode=importlib`` puts no test directory on ``sys.path``, so a
# test file cannot import a helper module beside it; ``api_fakes.py`` is one.
sys.path.insert(0, str(Path(__file__).parent))
