from __future__ import annotations

from app.storage.database import Database


class CachedTranslator:
    def __init__(self, database: Database, delegate, *, provider: str, model: str) -> None:
        self.database = database
        self.delegate = delegate
        self.provider = provider
        self.model = model

    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        if not texts:
            return []
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for text in texts:
            cached = self.database.get_translation_cache(
                provider=self.provider,
                model=self.model,
                source_language=source_language,
                target_language=target_language,
                source_text=text,
            )
            if cached is None:
                if text not in missing:
                    missing.append(text)
            else:
                resolved[text] = cached
        if missing:
            translations = await self.delegate.translate(missing, source_language, target_language)
            for source_text, translated_text in zip(missing, translations, strict=True):
                resolved[source_text] = translated_text
                self.database.put_translation_cache(
                    provider=self.provider,
                    model=self.model,
                    source_language=source_language,
                    target_language=target_language,
                    source_text=source_text,
                    translated_text=translated_text,
                )
        return [resolved[text] for text in texts]

