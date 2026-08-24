import sys
from pathlib import Path

# Repo root must be on sys.path so tests can `import src.prefill`; there is
# no pyproject.toml / pytest.ini in this repo to configure that otherwise.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
