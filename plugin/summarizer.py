from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from agent.auxiliary_client import call_llm, extract_content_or_reasoning

logger = logging.getLogger(__name__)


SUMMARY_SYSTEM = """You are an assistant that writes compact, high-signal summaries of chat transcripts.
Rules:
- Be factual. Do not invent.
- Preserve commands, file paths, decisions, constraints, and open questions.
- Use bullet points.
- Keep it short but useful.
"""


def _hash_source(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize_turns(
    serialized: str,
    *,
    model_override: str = "",
    provider_override: str = "",
    timeout: float = 120.0,
) -> Tuple[Optional[str], str]:
    """Return (summary_text, source_hash)."""
    src_hash = _hash_source(serialized)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": "Summarize the following transcript chunk.\n\n" + serialized,
        },
    ]

    try:
        resp = call_llm(
            task="compression",  # reuse existing auxiliary routing + defaults
            provider=provider_override or None,
            model=model_override or None,
            messages=messages,
            temperature=0.2,
            max_tokens=900,
            timeout=timeout,
        )
        text = extract_content_or_reasoning(resp) or ""
        text = text.strip()
        if not text:
            return None, src_hash
        return text, src_hash
    except Exception as e:
        logger.debug("SessionVault summarization failed: %s", e)
        return None, src_hash
