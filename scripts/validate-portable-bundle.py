from __future__ import annotations

import sys
from pathlib import Path

repository_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository_root / "src"))

from koreader_ecosystem.portable_bundle import validate_portable_bundle  # noqa: E402

report = validate_portable_bundle(Path(sys.argv[1]))
if not report.executable.is_file():
    raise SystemExit("The portable executable is missing.")
print("Portable application contents and runtime dependencies are valid.")
