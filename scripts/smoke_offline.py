from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np

from app.paths import AppPaths
from app.services.application import TranslationApplication
from app.tasks.pipeline import PipelineOptions


def main() -> int:
    paths = AppPaths.default()
    application = TranslationApplication(paths)
    application.initialize()
    source = paths.temp_root / "offline-smoke-input.png"
    output = paths.temp_root / "offline-smoke-output"
    image = np.full((180, 640, 3), 255, dtype=np.uint8)
    cv2.putText(image, "HELLO 123", (30, 115), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 5)
    cv2.imwrite(str(source), image)
    outcome = asyncio.run(
        application.process_page(
            source,
            output,
            PipelineOptions(translate=False),
        )
    )
    texts = [region.source_text for region in outcome.result.regions]
    print(f"OCR文本: {texts}")
    print(f"修复图: {outcome.result.artifacts.inpainted}")
    print(f"译图: {outcome.result.artifacts.translated}")
    print(f"JSON: {outcome.result.artifacts.metadata}")
    return 0 if any("HELLO" in text for text in texts) else 1


if __name__ == "__main__":
    raise SystemExit(main())

