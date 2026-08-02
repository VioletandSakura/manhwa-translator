from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .enums import PageStatus, TaskStatus


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    source_path: str
    output_dir: str
    status: TaskStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PageRecord:
    id: str
    task_id: str
    page_index: int
    source_path: str
    status: PageStatus
    stage: str
    progress: int
    error: str | None


@dataclass(slots=True)
class TextRegion:
    id: str
    polygon: list[tuple[int, int]]
    source_text: str = ""
    translated_text: str = ""
    confidence: float = 0.0
    direction: str = "horizontal"
    angle: float = 0.0
    needs_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        xs = [point[0] for point in self.polygon]
        ys = [point[1] for point in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True, slots=True)
class PageArtifacts:
    original: Path
    inpainted: Path
    translated: Path
    metadata: Path

