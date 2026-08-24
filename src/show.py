"""Print a prefilled record for visual inspection."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.prefill import prefill_file

path = sys.argv[1] if len(sys.argv) > 1 else "data/raw/0001.txt"
print(json.dumps(prefill_file(path), indent=2, ensure_ascii=False))