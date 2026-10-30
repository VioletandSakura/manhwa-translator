from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

from app.domain.models import TextRegion
from app.paths import AppPaths


class PillowTextRenderer:
    """Render per-region styles in a local layer, then rotate about its center."""

    def __init__(self, *, font_path: Path | None = None, min_font_size: int = 10, max_font_size: int = 64, color: tuple[int, int, int] = (0, 0, 0), stroke_width: int = 0) -> None:
        if not 1 <= min_font_size <= max_font_size <= 300:
            raise ValueError("字号范围无效，最小字号不能大于最大字号（1–300）。")
        if font_path is not None and not Path(font_path).is_file():
            raise ValueError("找不到所选字体文件，请重新选择或留空使用默认字体。")
        self.font_path = Path(font_path) if font_path else self._find_font()
        self.min_font_size = min_font_size
        self.max_font_size = max_font_size
        self.color = color
        self.stroke_width = stroke_width
        self._font_cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def validate(self) -> None:
        try:
            self._load_font(self.min_font_size)
        except OSError as error:
            raise ValueError("所选文件不是可用字体，请选择TTF、OTF或TTC字体。") from error

    async def render(self, image: np.ndarray, regions: list[TextRegion]) -> np.ndarray:
        canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        for region in regions:
            if not region.translated_text.strip():
                continue
            left, top, right, bottom = region.bounds
            width, height = max(1, right - left), max(1, bottom - top)
            if width > canvas.width * 4 or height > canvas.height * 4 or width * height > max(4_000_000, canvas.width * canvas.height * 4):
                raise ValueError("文本框过大，请把宽高调整到页面范围内。")
            layer = Image.new("RGBA", (width, height))
            self._draw_region(ImageDraw.Draw(layer), region, width, height)
            if region.angle:
                layer = layer.rotate(-region.angle, resample=Image.Resampling.BICUBIC, expand=True)
            x = round((left + right - layer.width) / 2)
            y = round((top + bottom - layer.height) / 2)
            canvas.paste(layer, (x, y), layer)
        return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)

    def _sizes(self, region: TextRegion) -> range:
        requested = int(region.metadata.get("font_size", 0))
        if requested:
            if not 1 <= requested <= 300:
                raise ValueError("文本框字号必须在1–300之间，或设置为自动。")
            return range(requested, 0, -1)
        return range(self.max_font_size, self.min_font_size - 1, -1)

    def _draw_region(self, draw: ImageDraw.ImageDraw, region: TextRegion, width: int, height: int) -> None:
        style = region.metadata
        color = ImageColor.getrgb(str(style["color"])) if style.get("color") else self.color
        stroke = max(0, min(20, int(style.get("outline_width", self.stroke_width))))
        outline = ImageColor.getrgb(str(style.get("outline_color", "#ffffff")))
        alignment = str(style.get("alignment", "center"))
        if alignment not in {"left", "center", "right"}:
            raise ValueError("文本对齐方式必须为左对齐、居中或右对齐。")
        sizes = self._sizes(region)
        if region.direction == "vertical":
            self._draw_vertical(draw, region, width, height, sizes, color, stroke, outline, alignment)
            return
        for size in sizes:
            font = self._load_font(size)
            wrapped = self._wrap_text(draw, region.translated_text, font, max(1, width - stroke * 2))
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, align=alignment, spacing=2, stroke_width=stroke)
            text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            if text_width <= width and text_height <= height:
                break
        offset = 0 if alignment == "left" else max(0, width - text_width) * (1 if alignment == "right" else 0.5)
        x = offset - bounds[0]
        y = max(0, (height - text_height) / 2) - bounds[1]
        draw.multiline_text((x, y), wrapped, font=font, fill=color, align=alignment, spacing=2, stroke_width=stroke, stroke_fill=outline)
        self._record_layout(region, size, text_width > width or text_height > height)

    def _draw_vertical(self, draw, region, width, height, sizes, color, stroke, outline, alignment) -> None:
        text = region.translated_text.replace("\n", "")
        for size in sizes:
            font = self._load_font(size)
            bounds = draw.textbbox((0, 0), "国", font=font, stroke_width=stroke)
            column_width = max(1, bounds[2] - bounds[0]) + 2
            row_height = max(1, bounds[3] - bounds[1]) + 2
            per_column = max(1, height // row_height)
            columns = [text[start:start + per_column] for start in range(0, len(text), per_column)]
            layout_width = len(columns) * column_width
            if layout_width <= width and row_height <= height:
                break
        offset = 0 if alignment == "left" else max(0, width - layout_width) * (1 if alignment == "right" else 0.5)
        for column_index, column in enumerate(columns):
            x = offset + (len(columns) - 1 - column_index) * column_width
            y = max(0, (height - len(column) * row_height) / 2)
            for row_index, character in enumerate(column):
                char_bounds = draw.textbbox((0, 0), character, font=font, stroke_width=stroke)
                draw.text((x - char_bounds[0], y + row_index * row_height - char_bounds[1]), character,
                          font=font, fill=color, stroke_width=stroke, stroke_fill=outline)
        self._record_layout(region, size, layout_width > width or row_height > height)

    @staticmethod
    def _record_layout(region: TextRegion, size: int, overflow: bool) -> None:
        region.metadata["rendered_font_size"] = size
        if overflow:
            region.metadata["render_warning"] = "文字无法在最小字号下完整放入文本框，请扩大文本框或减小字号。"
        else:
            region.metadata.pop("render_warning", None)

    @staticmethod
    def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
        lines = []
        for paragraph in text.split("\n"):
            current = ""
            for character in paragraph:
                candidate = current + character
                if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
                    lines.append(current)
                    current = character
                else:
                    current = candidate
            lines.append(current)
        return "\n".join(lines)

    @staticmethod
    def _vertical_text(text: str) -> str:
        return "\n".join(character for character in text if character != "\n")

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if size not in self._font_cache:
            self._font_cache[size] = ImageFont.truetype(str(self.font_path), size=size) if self.font_path else ImageFont.load_default(size=size)
        return self._font_cache[size]

    @staticmethod
    def _find_font() -> Path | None:
        candidates = [AppPaths.default().fonts_dir / "NotoSansCJKsc-Regular.otf", Path(r"C:\Windows\Fonts\msyh.ttc"),
                      Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\arial.ttf")]
        return next((path for path in candidates if path.is_file()), None)
