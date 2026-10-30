from __future__ import annotations

import os
from app.paths import AppPaths


TEMP_ROOT = AppPaths.default().temp_root
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP_ROOT)
os.environ["TMP"] = str(TEMP_ROOT)
os.environ["HF_HOME"] = str(TEMP_ROOT / "huggingface")
os.environ["PIP_CACHE_DIR"] = str(TEMP_ROOT.parent / "pip")

from app.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
