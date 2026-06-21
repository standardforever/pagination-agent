from __future__ import annotations

from typing import Any

from ..extraction.extractor import JobExtractionContext
from ..pagination.browser import gradual_scroll_probe_for_more_content
from ..pagination.llm import analyse_url_pattern_from_click
from ..pagination.state import url_plan_after_observed_move
from ..pipeline.context import PipelineContext


async def execute_infinite_scroll(
    page,
    extractor: JobExtractionContext,
    context: PipelineContext,
    *,
    max_rounds: int = 30,
    no_growth_limit: int = 3,
) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    no_growth_count = 0
    job_growth_rounds = 0
    total_new_jobs = 0
    url_pattern: dict[str, Any] | None = None
    stop_reason = "completed_or_limit"

    print("  -- Infinite Scroll Execution --")
    for round_index in range(1, max_rounds + 1):
        before_url = page.url
        before_total_jobs = len(context.jobs)
        probe = await gradual_scroll_probe_for_more_content(page)
        extraction = await extractor.extract_current_page(page, context, f"infinite_scroll_{round_index}")
        new_jobs = int(extraction.get("new_jobs") or 0)
        height_increased = bool(probe.get("height_increased"))
        jobs_increased = len(context.jobs) > before_total_jobs or new_jobs > 0
        if jobs_increased:
            job_growth_rounds += 1
            total_new_jobs += new_jobs
        url_changed = page.url != before_url
        if url_changed and url_pattern is None:
            pattern = analyse_url_pattern_from_click(before_url, page.url)
            if pattern.get("can_use_url") and pattern.get("url"):
                pattern["url"] = url_plan_after_observed_move(pattern["url"])
                url_pattern = pattern

        report = {
            "round": round_index,
            "before_height": probe.get("before_height"),
            "after_height": probe.get("after_height"),
            "height_increased": height_increased,
            "url_before": before_url,
            "url_after": page.url,
            "url_changed": url_changed,
            "new_jobs": new_jobs,
            "total_jobs": len(context.jobs),
        }
        rounds.append(report)
        print(
            "    round=%s height=%s->%s new_jobs=%s total_jobs=%s"
            % (round_index, report["before_height"], report["after_height"], new_jobs, len(context.jobs))
        )

        if height_increased or jobs_increased:
            no_growth_count = 0
        else:
            no_growth_count += 1

        if no_growth_count >= no_growth_limit:
            stop_reason = "no_height_or_job_growth"
            break

    result = {
        "rounds": rounds,
        "job_growth_rounds": job_growth_rounds,
        "total_new_jobs": total_new_jobs,
        "url_pattern": url_pattern,
        "stop_reason": stop_reason,
    }
    context.infinite_scroll_runs.append(result)
    print("  -- End Infinite Scroll Execution --")
    return result
