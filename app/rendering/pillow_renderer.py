from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.domain.models import TextRegion
from app.paths import AppPaths


class PillowTextRenderer:
    def __init__(self, *, font_path: Path | None = None, min_font_size: int = 10, max_font_size: int = 64, color: tuple[int, int, int] = (0, 0, 0), stroke_width: int = 0) -> None:
        self.font_path = font_path or self._find_font()
        self.min_font_size = min_font_size
        self.max_font_size = max_font_size
        self.color = color
        self.stroke_width = stroke_width

    async def render(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(canvas)
        for region in regions:
            if region.translated_text:
                self._draw_region(draw, region)
        return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)

    def _draw_region(self, draw: ImageDraw.ImageDraw, region: TextRegion) -> None:
        left, top, right, bottom = region.bounds
        box_width, box_height = max(1, right - left), max(1, bottom - top)
        text = self._vertical_text(region.translated_text) if region.direction == "vertical" else region.translated_text
        font, wrapped = self._fit_text(draw, text, box_width, box_height, region.direction)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center", spacing=2)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        x = left + max(0, (box_width - text_width) / 2)
        y = top + max(0, (box_height - text_height) / 2) - bounds[1]
        draw.multiline_text((x, y), wrapped, font=font, fill=self.color, align="center", spacing=2, stroke_width=self.stroke_width, stroke_fill=(255, 255, 255))

    def _fit_text(self, draw: ImageDraw.ImageDraw, text: str, width: int, height: int, direction: str) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
        for size in range(self.max_font_size, self.min_font_size - 1, -1):
            font = self._load_font(size)
            wrapped = text if direction == "vertical" else self._wrap_text(draw, text, font, width)
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2, align="center")
            if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
                return font, wrapped
        font = self._load_font(self.min_font_size)
        return font, text if direction == "vertical" else self._wrap_text(draw, text, font, width)

    @staticmethod
    def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
        lines: list[str] = []
        current = ""
        for character in text.replace("\n", ""):
            candidate = current + character
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines)

    @staticmethod
    def _vertical_text(text: str) -> str:
        return "\n".join(character for character in text if character != "\n")

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if self.font_path and self.font_path.exists():
            return ImageFont.truetype(str(self.font_path), size=size)
        return ImageFont.load_default(size=size)

    @staticmethod
    def _find_font() -> Path | None:
        candidates = [
            AppPaths.default().fonts_dir / "NotoSansCJKsc-Regular.otf",
            Path(r"C:\Windows\Fonts\msyh.ttc"),
            Path(r"C:\Windows\Fonts\simhei.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
        ]
        return next((path for path in candidates if path.exists()), None)

