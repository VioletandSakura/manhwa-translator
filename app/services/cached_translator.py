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
        cache = self.database.get_translation_cache_many(
            provider=self.provider,
            model=self.model,
            source_language=source_language,
            target_language=target_language,
            texts=texts,
        )
        resolved: dict[str, str] = dict(cache)
        missing = list(dict.fromkeys(text for text in texts if text not in cache))
        if missing:
            translations = await self.delegate.translate(missing, source_language, target_language)
            new_entries = {
                source_text: translated_text
                for source_text, translated_text in zip(missing, translations, strict=True)
            }
            resolved.update(new_entries)
            self.database.put_translation_cache_many(
                provider=self.provider,
                model=self.model,
                source_language=source_language,
                target_language=target_language,
                entries=new_entries,
            )
        return [resolved[text] for text in texts]

    async def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if close is not None:
            await close()
