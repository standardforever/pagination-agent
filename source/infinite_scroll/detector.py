from __future__ import annotations

from typing import Any

from ..extraction.extractor import JobExtractionContext
from ..pagination.browser import gradual_scroll_probe_for_more_content
from ..pagination.llm import analyse_url_pattern_from_click
from ..pagination.state import url_plan_after_observed_move
from ..pipeline.context import PipelineContext


async def detect_infinite_scroll(
    page,
    extractor: JobExtractionContext,
    context: PipelineContext,
    attempts: int = 3,
) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    height_growth_count = 0
    job_growth_count = 0
    url_pattern: dict[str, Any] | None = None

    print("  -- Infinite Scroll Detection --")
    for attempt in range(1, attempts + 1):
        before_url = page.url
        before_total_jobs = len(context.jobs)
        probe = await gradual_scroll_probe_for_more_content(page)
        extraction = await extractor.extract_current_page(page, context, f"infinite_detection_{attempt}")
        new_jobs = int(extraction.get("new_jobs") or 0)
        height_increased = bool(probe.get("height_increased"))
        url_changed = page.url != before_url

        if height_increased:
            height_growth_count += 1
        if len(context.jobs) > before_total_jobs or new_jobs > 0:
            job_growth_count += 1
        if url_changed and url_pattern is None:
            pattern = analyse_url_pattern_from_click(before_url, page.url)
            if pattern.get("can_use_url") and pattern.get("url"):
                pattern["url"] = url_plan_after_observed_move(pattern["url"])
                url_pattern = pattern

        report = {
            "attempt": attempt,
            "before_height": probe.get("before_height"),
            "after_height": probe.get("after_height"),
            "height_increased": height_increased,
            "url_before": before_url,
            "url_after": page.url,
            "url_changed": url_changed,
            "new_jobs": new_jobs,
            "total_jobs": len(context.jobs),
        }
        probes.append(report)
        print(
            "    probe=%s height=%s->%s new_jobs=%s total_jobs=%s"
            % (attempt, report["before_height"], report["after_height"], new_jobs, len(context.jobs))
        )

    is_infinite = height_growth_count >= 2 and job_growth_count >= 1
    result = {
        "is_infinite": is_infinite,
        "height_growth_count": height_growth_count,
        "job_growth_count": job_growth_count,
        "url_pattern": url_pattern,
        "probes": probes,
    }
    print(f"  Infinite scroll likely: {is_infinite}")
    print("  -- End Infinite Scroll Detection --")
    return result
