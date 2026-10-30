from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.domain.models import TextRegion
from .masks import region_mask


class LaMaONNXInpainter:
    def __init__(self, model_path: Path, *, session=None, providers: list[str] | None = None) -> None:
        self.model_path = Path(model_path)
        if session is not None:
            self._session = session
            return
        if not self.model_path.is_file():
            raise FileNotFoundError("未找到LaMa ONNX模型文件；程序不会自动下载模型")
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise RuntimeError("尚未安装ONNX Runtime") from error
        active_providers = providers or ["CPUExecutionProvider"]
        self._session = ort.InferenceSession(str(self.model_path), providers=active_providers)

    async def inpaint(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        mask = region_mask(image.shape, regions)
        return await self.inpaint_mask(image, mask)

    async def inpaint_mask(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if mask.shape != image.shape[:2]:
            raise ValueError("蒙版尺寸与原图不一致。")
        if not np.any(mask):
            return image.copy()
        height, width = image.shape[:2]
        padded_height = (height + 7) // 8 * 8
        padded_width = (width + 7) // 8 * 8
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = cv2.copyMakeBorder(rgb, 0, padded_height - height, 0, padded_width - width, cv2.BORDER_REFLECT)
        padded_mask = cv2.copyMakeBorder(mask, 0, padded_height - height, 0, padded_width - width, cv2.BORDER_CONSTANT)
        image_tensor = np.transpose(rgb, (2, 0, 1))[None].astype(np.float32)
        mask_tensor = (padded_mask[None, None].astype(np.float32) / 255.0)
        inputs = self._session.get_inputs()
        if len(inputs) == 1:
            feed = {inputs[0].name: np.concatenate((image_tensor * (1 - mask_tensor), mask_tensor), axis=1)}
        else:
            feed = {}
            for item in inputs:
                feed[item.name] = mask_tensor if "mask" in item.name.casefold() else image_tensor
        output = np.asarray(self._session.run(None, feed)[0])[0]
        if output.shape[0] in {3, 4}:
            output = np.transpose(output[:3], (1, 2, 0))
        output = np.clip(output, 0, 1)
        rendered = cv2.cvtColor((output * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)[:height, :width]
        combined = image.copy()
        combined[mask > 0] = rendered[mask > 0]
        return combined
