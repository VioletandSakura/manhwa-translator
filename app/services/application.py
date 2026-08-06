from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re
import uuid
import zipfile

import cv2
import numpy as np

from app.detection.rapidocr_detector import RapidOCRDetector
from app.inpainting.fallback import FallbackInpainter
from app.inpainting.lama_onnx import LaMaONNXInpainter
from app.domain.enums import PageStatus, TaskStatus
from app.domain.models import ApiChannelRecord, LibraryEntry, TextRegion
from app.importing.importer import ImportedBatch, InputImporter
from app.inpainting.opencv_inpainter import OpenCVInpainter
from app.ocr.rapidocr_engine import RapidOCREngine
from app.ocr.mangaocr_engine import MangaOCREngine
from app.paths import AppPaths
from app.rendering.pillow_renderer import PillowTextRenderer
from app.security.credentials import CredentialStore
from app.security.redaction import redact_text
from app.storage.database import Database
from app.tasks.pipeline import PagePipeline, PipelineOptions, ProgressCallback
from app.tasks.queue import PersistentTaskQueue
from app.translation.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleTranslator
from app.translation.router import ChannelRouterTranslator

from .cached_translator import CachedTranslator
from .safe_logger import SafeLogger


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    task_id: str
    page_id: str
    result: Any


@dataclass(frozen=True, slots=True)
class BatchCreation:
    task_id: str
    page_count: int
    imported: ImportedBatch


class TranslationApplication:
    CHANNEL_ID = "deepseek-default"
    DEFAULT_API = {
        "service_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "timeout_seconds": 60,
        "retries": 2,
    }

    def __init__(
        self,
        paths: AppPaths,
        *,
        database: Database | None = None,
        credentials: CredentialStore | None = None,
        logger: SafeLogger | None = None,
    ) -> None:
        self.paths = paths
        self.database = database or Database(paths.database_path)
        self.credentials = credentials or CredentialStore()
        self.logger = logger or SafeLogger(paths.logs_dir / "app.log")
        self._rapidocr_engine: RapidOCREngine | None = None

    def initialize(self) -> None:
        self.paths.ensure_runtime_directories()
        self.database.initialize()
        self.database.repair_completed_task_statuses()

    def save_api_config(self, values: dict[str, object]) -> None:
        service_name = str(values.get("service_name") or "").strip()
        base_url = str(values.get("base_url") or "").strip()
        model = self._normalize_api_model(service_name, base_url, str(values.get("model") or "").strip())
        if not service_name or not base_url or not model:
            raise ValueError("服务名称、Base URL和模型不能为空")
        api_key = str(values.get("api_key") or "").strip()
        if api_key:
            self.credentials.save(self.CHANNEL_ID, api_key)
        if not self.credentials.load(self.CHANNEL_ID):
            raise ValueError("请输入并保存API Key；Windows凭据管理器中尚无此通道的密钥")
        settings = {
            "service_name": service_name,
            "base_url": base_url,
            "model": model,
            "timeout_seconds": str(int(values.get("timeout_seconds") or 60)),
            "retries": str(int(values.get("retries") or 2)),
        }
        for key, value in settings.items():
            self.database.set_setting(f"api.{key}", value)
        try:
            existing = self.database.get_api_channel(self.CHANNEL_ID)
            verified = existing.verified
            last_error = existing.last_error
        except KeyError:
            verified = False
            last_error = None
        self.database.upsert_api_channel(
            ApiChannelRecord(
                id=self.CHANNEL_ID,
                service_name=service_name,
                enabled=True,
                base_url=base_url,
                model=model,
                timeout_seconds=int(values.get("timeout_seconds") or 60),
                max_concurrency=1,
                rpm_limit=60,
                retries=int(values.get("retries") or 2),
                priority=10,
                strategy="priority",
                verified=verified,
                last_error=last_error,
            )
        )
        self.logger.info(f"已保存API配置：{service_name} / {model}")

    def create_batch_task(self, sources: list[Path], output_dir: Path) -> BatchCreation:
        importer = InputImporter(self.paths.data_dir / "imports")
        imported = importer.import_sources([Path(source) for source in sources])
        source_label = " | ".join(str(Path(source)) for source in sources)
        task_id = self.database.create_task(source_label, str(Path(output_dir)))
        for page in imported.pages:
            self.database.add_page(task_id, page.index, str(page.source_path))
        self.logger.info(f"已创建批量任务：{len(imported.pages)}页")
        return BatchCreation(task_id=task_id, page_count=len(imported.pages), imported=imported)

    def save_api_channel(self, values: dict[str, object]) -> str:
        channel_id = str(values.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", channel_id):
            raise ValueError("API通道编号只能包含字母、数字、下划线和短横线")
        service_name = str(values.get("service_name") or "").strip()
        base_url = str(values.get("base_url") or "").strip()
        model = self._normalize_api_model(service_name, base_url, str(values.get("model") or "").strip())
        if not service_name or not model or not base_url.startswith(("https://", "http://")):
            raise ValueError("服务名称、有效Base URL和模型不能为空")
        try:
            existing = self.database.get_api_channel(channel_id)
            verified, last_error = existing.verified, existing.last_error
        except KeyError:
            verified, last_error = False, None
        api_key = str(values.get("api_key") or "").strip()
        if api_key:
            self.credentials.save(channel_id, api_key)
        if not self.credentials.load(channel_id):
            raise ValueError("请输入并保存API Key；Windows凭据管理器中尚无此通道的密钥")
        self.database.upsert_api_channel(
            ApiChannelRecord(
                id=channel_id,
                service_name=service_name,
                enabled=bool(values.get("enabled", True)),
                base_url=base_url,
                model=model,
                timeout_seconds=max(5, int(values.get("timeout_seconds") or 60)),
                max_concurrency=max(1, int(values.get("max_concurrency") or 1)),
                rpm_limit=max(1, int(values.get("rpm_limit") or 60)),
                retries=max(0, int(values.get("retries") or 2)),
                priority=int(values.get("priority") or 100),
                strategy=str(values.get("strategy") or "priority"),
                verified=verified,
                last_error=last_error,
            )
        )
        if channel_id == self.CHANNEL_ID:
            for key, value in {
                "service_name": service_name,
                "base_url": base_url,
                "model": model,
                "timeout_seconds": str(max(5, int(values.get("timeout_seconds") or 60))),
                "retries": str(max(0, int(values.get("retries") or 2))),
            }.items():
                self.database.set_setting(f"api.{key}", str(value))
        return channel_id

    @staticmethod
    def _normalize_api_model(service_name: str, base_url: str, model: str) -> str:
        if service_name.casefold() == "deepseek" or "api.deepseek.com" in base_url.casefold():
            return model.casefold()
        return model

    def delete_api_channel(self, channel_id: str) -> None:
        self.database.delete_api_channel(channel_id)
        self.credentials.revoke(channel_id)

    def save_library_content(self, category: str, name: str, payload: str) -> str:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("名称不能为空")
        existing = next(
            (entry for entry in self.database.list_library_entries(category) if entry.name == clean_name),
            None,
        )
        entry_id = existing.id if existing is not None else uuid.uuid4().hex
        self.database.upsert_library_entry(
            LibraryEntry(entry_id, category, clean_name, payload, True, 100)
        )
        return entry_id

    def list_library_content(self, category: str) -> list[LibraryEntry]:
        return self.database.list_library_entries(category)

    def export_task_zip(self, task_id: str, target_path: Path) -> Path:
        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        used_names: set[str] = set()
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for page in self.database.list_task_pages(task_id):
                if not page.result_path or not Path(page.result_path).is_file():
                    continue
                name = f"{Path(page.source_path).stem}.png"
                if name in used_names:
                    name = f"{page.page_index + 1:04d}_{name}"
                used_names.add(name)
                archive.write(page.result_path, arcname=name)
        return target_path

    def archive_originals(self, task_id: str) -> Path | None:
        """Pack the original/ folder into originals.zip and remove loose copies.

        Only runs for completed tasks and only after the zip is written and
        verified, so originals are never lost if archiving fails.
        """
        try:
            task = self.database.get_task(task_id)
            if task.status is not TaskStatus.COMPLETED:
                return None
            output_dir = Path(task.output_dir)
            original_dir = output_dir / "original"
            archive_path = output_dir / "originals.zip"
            if archive_path.exists():
                return archive_path
            if not original_dir.is_dir():
                return None
            files = sorted(
                (path for path in original_dir.iterdir() if path.is_file()),
                key=lambda path: path.name,
            )
            if not files:
                return None
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
            if not archive_path.is_file() or archive_path.stat().st_size <= 0:
                raise RuntimeError("原图压缩包写入失败")
            for path in files:
                path.unlink()
            try:
                original_dir.rmdir()
            except OSError:
                pass
            self.logger.info(f"原图已归档：{archive_path.name}")
            return archive_path
        except Exception as error:
            self.logger.warning(f"原图归档失败：{redact_text(str(error))}")
            return None

    async def rerender_metadata(self, metadata_path: Path) -> Path:
        metadata_path = Path(metadata_path)
        document = json.loads(metadata_path.read_text(encoding="utf-8"))
        regions = [
            TextRegion(
                id=str(data["id"]),
                polygon=[tuple(point) for point in data["polygon"]],
                source_text=str(data.get("source_text", "")),
                translated_text=str(data.get("translated_text", "")),
                confidence=float(data.get("confidence", 0)),
                direction=str(data.get("direction", "horizontal")),
                angle=float(data.get("angle", 0)),
                needs_review=bool(data.get("needs_review", False)),
                metadata=dict(data.get("metadata", {})),
            )
            for data in document.get("regions", [])
        ]
        task_output = metadata_path.parent.parent
        inpainted_path = task_output / "inpainted" / f"{metadata_path.stem}_inpainted.png"
        image = cv2.imdecode(
            np.frombuffer(inpainted_path.read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise ValueError(f"缺少修复图，无法重新渲染：{inpainted_path}")
        rendered = await PillowTextRenderer().render(image, regions)
        translated_path = task_output / "translated" / f"{metadata_path.stem}_translated.png"
        translated_path.parent.mkdir(parents=True, exist_ok=True)
        succeeded, encoded = cv2.imencode(".png", rendered)
        if not succeeded:
            raise ValueError(f"无法编码重渲染图片：{translated_path.name}")
        translated_path.write_bytes(encoded.tobytes())
        return translated_path

    def load_api_config(self) -> dict[str, object]:
        loaded: dict[str, object] = {
            key: self.database.get_setting(f"api.{key}", str(default))
            for key, default in self.DEFAULT_API.items()
        }
        loaded["timeout_seconds"] = int(loaded["timeout_seconds"])
        loaded["retries"] = int(loaded["retries"])
        loaded["has_api_key"] = bool(self.credentials.load(self.CHANNEL_ID))
        return loaded

    def revoke_api_key(self) -> None:
        self.credentials.revoke(self.CHANNEL_ID)
        self.logger.info("已撤销本地保存的API Key")

    def revoke_api_channel_key(self, channel_id: str) -> None:
        self.credentials.revoke(channel_id)
        self.logger.info(f"已撤销API通道密钥：{channel_id}")

    def list_api_channels(self) -> list[ApiChannelRecord]:
        return self.database.list_api_channels()

    def build_translator(self) -> CachedTranslator:
        channels = self.database.list_api_channels(enabled_only=True)
        if not channels:
            raise ValueError("尚未配置可用的API通道")

        def factory(channel: ApiChannelRecord, api_key: str) -> OpenAICompatibleTranslator:
            return OpenAICompatibleTranslator(
                OpenAICompatibleConfig(
                    service_name=channel.service_name,
                    api_key=api_key,
                    base_url=channel.base_url,
                    model=channel.model,
                    timeout_seconds=channel.timeout_seconds,
                    retries=channel.retries,
                )
            )

        delegate = ChannelRouterTranslator(self.database, self.credentials, factory)
        route_signature = ",".join(f"{channel.id}:{channel.model}" for channel in channels)
        return CachedTranslator(
            self.database,
            delegate,
            provider="API通道路由",
            model=route_signature,
        )

    def build_pipeline(
        self,
        *,
        translate: bool = True,
        ocr_engine: str = "rapidocr",
        inpainting_engine: str = "opencv",
        high_accuracy_ocr: bool = False,
    ) -> PagePipeline:
        translator = self.build_translator() if translate else _UnusedTranslator()
        detector_ocr = self._get_rapidocr_engine(high_accuracy_ocr=high_accuracy_ocr)
        ocr = (
            MangaOCREngine(self.paths.models_dir / "manga-ocr-base")
            if ocr_engine == "mangaocr"
            else detector_ocr
        )
        opencv_inpainter = OpenCVInpainter()
        inpainter = (
            FallbackInpainter(
                LaMaONNXInpainter(self.paths.models_dir / "lama.onnx"),
                opencv_inpainter,
            )
            if inpainting_engine == "lama_onnx"
            else opencv_inpainter
        )
        return PagePipeline(
            detector=RapidOCRDetector(detector_ocr),
            ocr=ocr,
            translator=translator,
            inpainter=inpainter,
            renderer=PillowTextRenderer(),
        )

    def _get_rapidocr_engine(self, *, high_accuracy_ocr: bool = False) -> RapidOCREngine:
        det_limit_side_len = 1280 if high_accuracy_ocr else None
        max_side_len = 4000 if high_accuracy_ocr else None
        if self._rapidocr_engine is None:
            self._rapidocr_engine = RapidOCREngine(
                det_limit_side_len=det_limit_side_len,
                max_side_len=max_side_len,
            )
        else:
            self._rapidocr_engine.set_detection_config(
                det_limit_side_len=det_limit_side_len,
                max_side_len=max_side_len,
            )
        return self._rapidocr_engine

    def build_task_queue(
        self,
        options: PipelineOptions,
        *,
        ocr_engine: str = "rapidocr",
        inpainting_engine: str = "opencv",
        high_accuracy_ocr: bool = False,
        max_concurrent_pages: int = 1,
        on_task_completed=None,
        on_page_completed=None,
        on_page_failed=None,
        on_page_progress=None,
    ) -> PersistentTaskQueue:
        pipeline = self.build_pipeline(
            translate=options.translate,
            ocr_engine=ocr_engine,
            inpainting_engine=inpainting_engine,
            high_accuracy_ocr=high_accuracy_ocr,
        )

        async def process(page, output_dir, on_progress):
            return await pipeline.process(
                Path(page.source_path), output_dir, options, on_progress=on_progress
            )

        return PersistentTaskQueue(
            self.database,
            process,
            max_concurrent_pages=max_concurrent_pages,
            on_task_completed=(
                on_task_completed
                if on_task_completed is not None
                else self.archive_originals
            ),
            on_page_completed=on_page_completed,
            on_page_failed=on_page_failed,
            on_page_progress=on_page_progress,
        )

    async def test_api_connection(self) -> str:
        return await self.test_api_channel(self.CHANNEL_ID)

    async def list_api_channel_models(self, channel_id: str) -> list[str]:
        channel = self.database.get_api_channel(channel_id)
        api_key = self.credentials.load(channel_id)
        if not api_key:
            raise ValueError("尚未保存API Key")
        translator = OpenAICompatibleTranslator(
            OpenAICompatibleConfig(
                service_name=channel.service_name,
                api_key=api_key,
                base_url=channel.base_url,
                model=channel.model,
                timeout_seconds=channel.timeout_seconds,
                retries=0,
            )
        )
        try:
            return await translator.list_models()
        finally:
            await translator.close()

    async def test_api_channel(self, channel_id: str) -> str:
        channel = self.database.get_api_channel(channel_id)
        api_key = self.credentials.load(channel_id)
        if not api_key:
            raise ValueError("尚未保存API Key")
        translator = OpenAICompatibleTranslator(
            OpenAICompatibleConfig(
                service_name=channel.service_name,
                api_key=api_key,
                base_url=channel.base_url,
                model=channel.model,
                timeout_seconds=channel.timeout_seconds,
                retries=channel.retries,
            )
        )
        try:
            result = await translator.test_connection()
            self.database.set_api_channel_health(channel_id, verified=True, last_error=None)
            return result
        except Exception as error:
            self.database.set_api_channel_health(
                channel_id, verified=False, last_error=redact_text(str(error))
            )
            raise
        finally:
            await translator.close()

    async def process_page(
        self,
        source_path: Path,
        output_dir: Path,
        options: PipelineOptions,
        *,
        on_progress: ProgressCallback | None = None,
        pipeline: PagePipeline | None = None,
    ) -> ProcessingOutcome:
        task_id = self.database.create_task(str(source_path), str(output_dir))
        page_id = self.database.add_page(task_id, 0, str(source_path))
        self.database.update_task_status(task_id, TaskStatus.RUNNING)
        self.database.update_page_status(page_id, PageStatus.RUNNING, stage="准备", progress=0)
        active_pipeline = pipeline or self.build_pipeline(translate=options.translate)

        def record_progress(stage: str, progress: int) -> None:
            self.database.update_page_status(page_id, PageStatus.RUNNING, stage=stage, progress=progress)
            if on_progress is not None:
                on_progress(stage, progress)

        try:
            result = await active_pipeline.process(
                source_path,
                output_dir,
                options,
                on_progress=record_progress,
            )
        except Exception as error:
            message = redact_text(str(error))
            self.database.update_page_status(page_id, PageStatus.FAILED, stage="失败", progress=0, error=message)
            self.database.update_task_status(task_id, TaskStatus.FAILED)
            self.logger.error(message)
            raise
        self.database.update_page_status(page_id, PageStatus.COMPLETED, stage="保存", progress=100)
        self.database.update_task_status(task_id, TaskStatus.COMPLETED)
        self.archive_originals(task_id)
        self.logger.info(f"页面处理完成：{Path(source_path).name}")
        return ProcessingOutcome(task_id=task_id, page_id=page_id, result=result)


class _UnusedTranslator:
    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        return list(texts)
