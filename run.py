from __future__ import annotations

import os
from pathlib import Path


TEMP_ROOT = Path(r"D:\Chatgpt\99_Temp\manga-translator-personal")
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(TEMP_ROOT)
os.environ["TMP"] = str(TEMP_ROOT)
os.environ["HF_HOME"] = str(TEMP_ROOT / "huggingface")
os.environ["PIP_CACHE_DIR"] = str(Path(r"D:\Chatgpt\99_Temp\pip-cache"))

from app.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
