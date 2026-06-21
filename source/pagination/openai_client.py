from __future__ import annotations

from typing import Any

from ..job_pattern.utils.openai_service import create_openai_client


def get_openai_client(api_key: str | None = None) -> Any:
    return create_openai_client(api_key)
