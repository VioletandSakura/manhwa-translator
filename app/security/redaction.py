from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_ -]?key|token|secret)\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{6,})\b"),
)


def mask_secret(secret: str) -> str:
    """把密钥显示为首尾保留的掩码。"""
    value = str(secret or "")
    if len(value) < 8:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def redact_text(text: str) -> str:
    """清除文本中的 Authorization、密钥与 sk- 开头的敏感片段。"""
    redacted = str(text or "")
    redacted = _SECRET_PATTERNS[0].sub(r"\1***", redacted)
    redacted = _SECRET_PATTERNS[1].sub(r"\1***", redacted)
    redacted = _SECRET_PATTERNS[2].sub("***", redacted)
    return redacted
