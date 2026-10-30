from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.domain.models import TextRegion


class MangaOCREngine:
    def __init__(
        self,
        model_path: Path | None = None,
        *,
        force_cpu: bool = False,
        model: Callable[[Image.Image], str] | None = None,
    ) -> None:
        if model is not None:
            self._model = model
            return
        if model_path is None or not Path(model_path).is_dir():
            raise FileNotFoundError("未找到MangaOCR本地模型目录；程序不会自动下载本地模型")
        try:
            from manga_ocr import MangaOcr
        except ImportError as error:
            raise RuntimeError("尚未安装可选组件 manga-ocr") from error
        previous = {
            name: os.environ.get(name)
            for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        }
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            self._model = MangaOcr(
                pretrained_model_name_or_path=str(Path(model_path)),
                force_cpu=force_cpu,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]:
        height, width = image.shape[:2]
        for region in regions:
            left, top, right, bottom = region.bounds
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                region.source_text = ""
                region.confidence = 0.0
                continue
            crop = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2RGB)
            text = str(self._model(Image.fromarray(crop))).strip()
            region.source_text = text
            region.confidence = 1.0 if text else 0.0
            region.metadata["ocr_engine"] = "mangaocr"
        return regions
