from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from source.pagination.browser import get_extraction_js, load_and_extract, scroll_page
    from source.pagination.click import click_attempt_report, find_click_target
    from source.pagination.job_bridge import JobPatternBridge, save_jobs_json
    from source.pagination.llm import analyse_pagination, analyse_pagination_repair, analyse_url_pattern_from_click
    from source.pagination.main import main, process_url
    from source.pagination.navigation import (
        continue_url_pagination_on_page,
        test_click_pagination,
        test_pagination_plan,
        test_url_pagination,
    )
    from source.pagination.orchestrator import run_standard_mvp
    from source.pipeline.runner import run_pipeline
    from source.pagination.url_utils import _make_url_from_plan, _resolve_url
else:
    from .browser import get_extraction_js, load_and_extract, scroll_page
    from .click import click_attempt_report, find_click_target
    from .job_bridge import JobPatternBridge, save_jobs_json
    from .llm import analyse_pagination, analyse_pagination_repair, analyse_url_pattern_from_click
    from .main import main, process_url
    from .navigation import continue_url_pagination_on_page, test_click_pagination, test_pagination_plan, test_url_pagination
    from .orchestrator import run_standard_mvp
    from ..pipeline.runner import run_pipeline
    from .url_utils import _make_url_from_plan, _resolve_url


if __name__ == "__main__":
    asyncio.run(main())
