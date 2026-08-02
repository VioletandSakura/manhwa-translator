from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.detection.rapidocr_detector import RapidOCRDetector
from app.domain.enums import PageStatus, TaskStatus
from app.inpainting.opencv_inpainter import OpenCVInpainter
from app.ocr.rapidocr_engine import RapidOCREngine
from app.paths import AppPaths
from app.rendering.pillow_renderer import PillowTextRenderer
from app.security.credentials import CredentialStore
from app.security.redaction import redact_text
from app.storage.database import Database
from app.tasks.pipeline import PagePipeline, PipelineOptions, ProgressCallback
from app.translation.openai_compatible import OpenAICompatibleConfig, OpenAICompatibleTranslator

from .cached_translator import CachedTranslator
from .safe_logger import SafeLogger


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    task_id: str
    page_id: str
    result: Any


class TranslationApplication:
    CHANNEL_ID = "deepseek-default"
    DEFAULT_API = {
        "service_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
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

    def initialize(self) -> None:
        self.paths.ensure_runtime_directories()
        self.database.initialize()

    def save_api_config(self, values: dict[str, object]) -> None:
        service_name = str(values.get("service_name") or "").strip()
        base_url = str(values.get("base_url") or "").strip()
        model = str(values.get("model") or "").strip()
        if not service_name or not base_url or not model:
            raise ValueError("服务名称、Base URL和模型不能为空")
        api_key = str(values.get("api_key") or "").strip()
        if api_key:
            self.credentials.save(self.CHANNEL_ID, api_key)
        settings = {
            "service_name": service_name,
            "base_url": base_url,
            "model": model,
            "timeout_seconds": str(int(values.get("timeout_seconds") or 60)),
            "retries": str(int(values.get("retries") or 2)),
        }
        for key, value in settings.items():
            self.database.set_setting(f"api.{key}", value)
        self.logger.info(f"已保存API配置：{service_name} / {model}")

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

    def build_translator(self) -> CachedTranslator:
        api = self.load_api_config()
        api_key = self.credentials.load(self.CHANNEL_ID)
        if not api_key:
            raise ValueError("尚未保存API Key，请先到API管理页面配置")
        config = OpenAICompatibleConfig(
            service_name=str(api["service_name"]),
            api_key=api_key,
            base_url=str(api["base_url"]),
            model=str(api["model"]),
            timeout_seconds=float(api["timeout_seconds"]),
            retries=int(api["retries"]),
        )
        delegate = OpenAICompatibleTranslator(config)
        return CachedTranslator(
            self.database,
            delegate,
            provider=config.service_name,
            model=config.model,
        )

    def build_pipeline(self, *, translate: bool = True) -> PagePipeline:
        translator = self.build_translator() if translate else _UnusedTranslator()
        ocr = RapidOCREngine()
        return PagePipeline(
            detector=RapidOCRDetector(ocr),
            ocr=ocr,
            translator=translator,
            inpainter=OpenCVInpainter(),
            renderer=PillowTextRenderer(),
        )

    async def test_api_connection(self) -> str:
        api = self.load_api_config()
        api_key = self.credentials.load(self.CHANNEL_ID)
        if not api_key:
            raise ValueError("尚未保存API Key")
        translator = OpenAICompatibleTranslator(
            OpenAICompatibleConfig(
                service_name=str(api["service_name"]),
                api_key=api_key,
                base_url=str(api["base_url"]),
                model=str(api["model"]),
                timeout_seconds=float(api["timeout_seconds"]),
                retries=int(api["retries"]),
            )
        )
        try:
            return await translator.test_connection()
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
        self.logger.info(f"页面处理完成：{Path(source_path).name}")
        return ProcessingOutcome(task_id=task_id, page_id=page_id, result=result)


class _UnusedTranslator:
    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        return list(texts)
