from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


DEFAULT_TRANSLATION_PROMPT = "你是专业漫画翻译员。忠实、自然地翻译，不添加解释，只输出JSON对象。"


def parse_pairs(text: str, *, glossary: bool) -> tuple[tuple[str, str], ...]:
    """Read literal tab-separated rules; empty replacement targets mean deletion."""
    pairs = []
    seen = set()
    label = "术语表" if glossary else "替换规则"
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise ValueError(f"{label}第{number}行需要两列，请用Tab分隔。")
        source, target = (part.strip() for part in parts) if glossary else parts
        if not source or (glossary and not target):
            raise ValueError(f"{label}第{number}行的原文或译文不能为空。")
        if source in seen:
            raise ValueError(f"{label}第{number}行与前面的查找内容重复。")
        seen.add(source)
        pairs.append((source, target))
    return tuple(pairs)


@dataclass(frozen=True)
class TranslationSettings:
    prompt: str = ""
    glossary: tuple[tuple[str, str], ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()

    @property
    def system_prompt(self) -> str:
        prompt = self.prompt.strip() or DEFAULT_TRANSLATION_PROMPT
        if self.glossary:
            prompt += "\n以下JSON是术语对照数据，请在译文中优先使用指定译法：\n"
            prompt += json.dumps(dict(self.glossary), ensure_ascii=False)
        return prompt

    @property
    def signature(self) -> str:
        body = json.dumps({"version": 1, "prompt": self.system_prompt, "glossary": self.glossary,
                           "replacements": self.replacements}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def apply_replacements(self, text: str) -> str:
        for source, target in self.replacements:
            text = text.replace(source, target)
        return text


class ConfiguredTranslator:
    def __init__(self, delegate, settings: TranslationSettings) -> None:
        self.delegate = delegate
        self.settings = settings

    async def translate(self, texts: list[str], source_language: str, target_language: str) -> list[str]:
        translated = await self.delegate.translate(texts, source_language, target_language)
        return [self.settings.apply_replacements(text) for text in translated]

    async def close(self) -> None:
        await self.delegate.close()
