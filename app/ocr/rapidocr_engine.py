from __future__ import annotations

import asyncio
import threading
from difflib import SequenceMatcher
from typing import Any

import cv2
import numpy as np

from app.domain.models import TextRegion


class RapidOCRUnavailableError(RuntimeError):
    pass


class RapidOCREngine:
    """延迟加载RapidOCR，避免程序启动时下载或载入模型。"""

    def __init__(
        self,
        engine: Any | None = None,
        *,
        refine_threshold: float = 0.65,
        det_limit_side_len: int | None = None,
        max_side_len: int | None = None,
        crop_padding: int | None = None,
    ) -> None:
        self._engine = engine
        self._inference_lock = threading.Lock()
        self.refine_threshold = refine_threshold
        self.det_limit_side_len = det_limit_side_len
        self.max_side_len = max_side_len
        self.crop_padding = crop_padding

    def _get_engine(self) -> Any:
        if self._engine is not None:
            self._apply_detection_config()
            return self._engine
        try:
            from rapidocr import RapidOCR
        except ImportError as error:
            raise RapidOCRUnavailableError("尚未安装本地OCR组件。请在OCR设置中确认安装后再试。") from error
        self._engine = RapidOCR()
        self._apply_detection_config()
        return self._engine

    def set_detection_config(
        self,
        *,
        det_limit_side_len: int | None = None,
        max_side_len: int | None = None,
    ) -> None:
        self.det_limit_side_len = det_limit_side_len
        self.max_side_len = max_side_len
        self._apply_detection_config()

    def _apply_detection_config(self) -> None:
        if self._engine is None:
            return
        if self.max_side_len is not None:
            self._engine.max_side_len = self.max_side_len
        if self.det_limit_side_len is not None and hasattr(self._engine, "text_det"):
            self._engine.text_det.limit_side_len = self.det_limit_side_len

    async def recognize(self, image: np.ndarray, regions: list[TextRegion]) -> list[TextRegion]:
        height, width = image.shape[:2]
        pending: list[tuple[TextRegion, np.ndarray]] = []
        refinable: list[TextRegion] = []
        for region in regions:
            if "ocr_source_text" in region.metadata:
                region.source_text = str(region.metadata.get("ocr_source_text") or "")
                region.confidence = float(region.metadata.get("ocr_confidence") or 0.0)
                if region.confidence < self.refine_threshold:
                    refinable.append(region)
                continue
            left, top, right, bottom = self._padded_bounds(
                region.bounds, (height, width)
            )
            crop = image[top:bottom, left:right]
            if crop.size == 0:
                region.needs_review = True
                continue
            pending.append((region, crop))
        if refinable:
            await asyncio.to_thread(self._refine_regions, image, refinable)
        if pending:
            for region, text, confidence in await asyncio.to_thread(self._recognize_crops, pending):
                region.source_text = text
                region.confidence = confidence
        return regions

    def _padded_bounds(
        self,
        bounds: tuple[int, int, int, int],
        image_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Expand a region crop by a small margin so text touching the box
        edges is not cut off, which commonly causes missing characters."""
        height, width = image_size
        left, top, right, bottom = bounds
        box_height = bottom - top
        box_width = right - left
        padding = self.crop_padding
        if padding is None:
            padding = min(14, max(2, round(0.06 * min(box_height, box_width))))
        return (
            max(0, left - padding),
            max(0, top - padding),
            min(width, right + padding),
            min(height, bottom + padding),
        )

    def infer(self, image: np.ndarray) -> Any:
        with self._inference_lock:
            return self._get_engine()(image)

    def _recognize_crops(self, crops: list[tuple[TextRegion, np.ndarray]]) -> list[tuple[TextRegion, str, float]]:
        engine = self._get_engine()
        results: list[tuple[TextRegion, str, float]] = []
        with self._inference_lock:
            for region, crop in crops:
                text, confidence = self._recognize_crop(engine, crop)
                results.append((region, text, confidence))
        return results

    def _refine_regions(self, image: np.ndarray, regions: list[TextRegion]) -> None:
        engine = self._get_engine()
        with self._inference_lock:
            for region in regions:
                crop = self._rotate_crop(image, region.polygon)
                padded = self._rotate_crop(
                    image, self._expand_polygon(region.polygon, 1.1)
                )
                variants: list[np.ndarray] = []
                for base in (crop, padded):
                    if base is None or base.size == 0:
                        continue
                    variants.extend(self._refine_variants(base))
                best = None
                for variant in variants:
                    result = self._recognize_variant(engine, variant)
                    if result and result["text"] and (best is None or result["conf"] > best["conf"]):
                        best = result
                if best and self._accept_refinement(region.source_text, region.confidence, best):
                    region.source_text = best["text"]
                    region.confidence = best["conf"]
                    region.metadata["ocr_refined"] = True

    def _refine_variants(self, crop: np.ndarray) -> list[np.ndarray]:
        variants = [crop]
        upscaled = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        variants.append(upscaled)
        variants.append(self._clahe(upscaled))
        return variants

    def _recognize_variant(self, engine: Any, crop: np.ndarray) -> dict[str, Any] | None:
        if hasattr(engine, "recognize_txt"):
            try:
                result = engine.recognize_txt([crop])
            except Exception:
                result = None
            if result is not None and getattr(result, "txts", None):
                texts = [str(text) for text in (result.txts or [])]
                scores = [float(score) for score in (result.scores or [])]
                if texts:
                    return {
                        "text": "\n".join(texts).strip(),
                        "conf": sum(scores) / len(scores) if scores else 0.0,
                    }
            return None
        text, confidence = self._recognize_crop(engine, crop)
        return {"text": text, "conf": confidence} if text else None

    @classmethod
    def _accept_refinement(cls, old_text: str, old_confidence: float, candidate: dict[str, Any]) -> bool:
        text = candidate["text"]
        confidence = candidate["conf"]
        if not text or confidence < 0.65 or confidence < old_confidence + 0.05:
            return False
        if len(text) > len(old_text) + 8:
            return False
        if text != old_text and len(text) < len(old_text):
            return False
        similarity = SequenceMatcher(None, old_text, text).ratio()
        return similarity >= 0.5

    @staticmethod
    def _expand_polygon(points: list[tuple[int, int]], ratio: float) -> list[tuple[int, int]]:
        polygon = np.asarray(points, dtype=np.float32)
        center = polygon.mean(axis=0)
        expanded = (polygon - center) * ratio + center
        return [(int(round(x)), int(round(y))) for x, y in expanded.tolist()]

    @staticmethod
    def _rotate_crop(image: np.ndarray, points: list[tuple[int, int]]) -> np.ndarray | None:
        polygon = np.asarray(points, dtype=np.float32)
        if polygon.shape != (4, 2):
            return None
        width = int(
            max(
                np.linalg.norm(polygon[0] - polygon[1]),
                np.linalg.norm(polygon[2] - polygon[3]),
            )
        )
        height = int(
            max(
                np.linalg.norm(polygon[0] - polygon[3]),
                np.linalg.norm(polygon[1] - polygon[2]),
            )
        )
        if width < 1 or height < 1:
            return None
        standard = np.array(
            [[0, 0], [width, 0], [width, height], [0, height]],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(polygon, standard)
        crop = cv2.warpPerspective(
            image,
            matrix,
            (width, height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC,
        )
        if crop.shape[0] * 1.0 / crop.shape[1] >= 1.5:
            crop = np.ascontiguousarray(np.rot90(crop))
        return crop

    @staticmethod
    def _clahe(image: np.ndarray) -> np.ndarray:
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        lightness, a, b = cv2.split(lab)
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        return cv2.cvtColor(cv2.merge((enhanced, a, b)), cv2.COLOR_LAB2BGR)

    @staticmethod
    def _recognize_crop(engine: Any, crop: np.ndarray) -> tuple[str, float]:
        result = engine(crop)
        payload = result[0] if isinstance(result, tuple) else result
        if payload is None:
            return "", 0.0
        if hasattr(payload, "txts"):
            texts = list(payload.txts or [])
            scores = [float(value) for value in (payload.scores or [])]
        elif isinstance(payload, list):
            texts = [str(item[1]) for item in payload if len(item) >= 3]
            scores = [float(item[2]) for item in payload if len(item) >= 3]
        else:
            return "", 0.0
        confidence = sum(scores) / len(scores) if scores else 0.0
        return "\n".join(texts).strip(), confidence
