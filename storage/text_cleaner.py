"""
Text cleaning for raw signal bodies before storage.

Applied once in upsert_raw_signal so every consumer (classifier, embedder,
query) gets consistently clean text without repeated processing.

What is stripped:
- HTML entities and tags (HN, HuggingFace, SO use HTML in body)
- Fenced code blocks  (``` / ~~~) from GitHub issues and SO
- HTML <pre>/<code> blocks
- Long inline code (>40 chars) - short `var_name` style kept
- Excess whitespace / blank lines

What is kept:
- Markdown formatting (**, -, ###, [link](url)) - reflects user intent
- Short inline code references
- URLs (relevant for context)
"""

from __future__ import annotations

import html
import re


# Compiled patterns for performance (called for every collected signal)
_FENCED_CODE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_HTML_PRE_CODE = re.compile(
    r"<\s*(pre|code)[^>]*>[\s\S]*?<\s*/\s*(pre|code)\s*>",
    re.IGNORECASE,
)
_HTML_LINK = re.compile(r"<a\s[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HTML_TAGS = re.compile(r"<[^>]+>")
_LONG_INLINE_CODE = re.compile(r"`[^`\n]{40,}`")
_EXCESS_BLANKS = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+\n")


def clean_body(text: str) -> str:
    """
    Clean a raw signal body for storage.
    Order matters: HTML decode first, then strip structure, then whitespace.
    """
    if not text:
        return ""

    # 1. Decode HTML entities (HN wraps text: &#x27; → ', &quot; → ", <p> → \n)
    text = html.unescape(text)

    # 2. Strip HTML <pre>/<code> blocks (SO, HF, some GitHub)
    text = _HTML_PRE_CODE.sub(" [code] ", text)

    # 3. Preserve link text, drop href noise
    text = _HTML_LINK.sub(r"\1", text)

    # 4. Drop remaining HTML tags (<p>, <br>, <ul>, etc.) - replace with space
    text = _HTML_TAGS.sub(" ", text)

    # 5. Strip long inline code first (before fenced blocks, so we don't
    #    accidentally match across the [code] markers we insert in step 6).
    #    Short inline like `variable` or `None` is useful context - keep it.
    text = _LONG_INLINE_CODE.sub(" [code] ", text)

    # 6. Strip fenced code blocks (GitHub issues, SO answers, HF discussions)
    text = _FENCED_CODE.sub(" [code] ", text)

    # 7. Collapse whitespace noise introduced by stripping
    text = _TRAILING_SPACES.sub("\n", text)
    text = _EXCESS_BLANKS.sub("\n\n", text)

    return text.strip()
