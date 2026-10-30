from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class AppPaths:
    project_root: Path
    output_root: Path
    temp_root: Path

    @classmethod
    def default(cls) -> "AppPaths":
        # Keep a source checkout portable, including its existing task history.
        root = Path(__file__).resolve().parents[1]
        return cls(
            project_root=root,
            output_root=root / "outputs",
            temp_root=root / ".cache" / "tmp",
        )

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def models_dir(self) -> Path:
        return self.project_root / "models"

    @property
    def fonts_dir(self) -> Path:
        return self.project_root / "fonts"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    def runtime_directories(self) -> tuple[Path, ...]:
        return (
            self.data_dir,
            self.data_dir / "tasks",
            self.models_dir,
            self.fonts_dir,
            self.logs_dir,
            self.output_root,
            self.temp_root,
        )

    def ensure_runtime_directories(self) -> None:
        for directory in self.runtime_directories():
            directory.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        if not _SAFE_TASK_ID.fullmatch(task_id):
            raise ValueError("任务编号包含不安全字符")
        return self.data_dir / "tasks" / task_id

