from __future__ import annotations

import asyncio
import json
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .browser import extract_reduced_html_from_page, get_extraction_js, scroll_page
from .click import find_click_target
from .config import MAX_PAGINATION_TEST_PAGES, OPENAI_MODEL
from .job_bridge import JobPatternBridge, _job_key, _unique_jobs
from .llm import analyse_pagination, analyse_pagination_repair, analyse_url_pattern_from_click
from .state import current_value_after_click_url_pattern
from .url_utils import _as_int, _increment_value, _make_url_from_plan


def _has_constructible_url_plan(url_plan: dict[str, Any], base_url: str, value: str | None) -> bool:
    return bool(_make_url_from_plan(url_plan, base_url, str(value) if value is not None else None))


def _next_url_value(url_plan: dict[str, Any]) -> str | None:
    sequence_type = url_plan.get("sequence_type")
    increment = _as_int(url_plan.get("increment"), 1) or 1
    value = url_plan.get("next_value")
    if value:
        return str(value)
    current = url_plan.get("current_value")
    if current:
        return _increment_value(str(current), sequence_type, increment)
    return None


def _advance_url_value(url_plan: dict[str, Any], value: str | None) -> str | None:
    if value is None:
        return None
    return _increment_value(
        str(value),
        url_plan.get("sequence_type"),
        _as_int(url_plan.get("increment"), 1) or 1,
    )


async def _load_url_page(page, target_url: str) -> tuple[bool, str]:
    try:
        response = await page.goto(target_url, timeout=30_000, wait_until="domcontentloaded")
        status = response.status if response else None
        await asyncio.sleep(1)
        await scroll_page(page)
        if isinstance(status, int) and status >= 400:
            return False, f"bad_http_status:{status}"
        return True, f"status:{status or 'no-response'}"
    except PlaywrightTimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, f"navigation_error:{exc}"


async def _click_next_page(page, click_plan: dict[str, Any], page_number: int) -> tuple[bool, str, bool]:
    target, used_selector, disabled = await find_click_target(page, click_plan, page_number)
    if target is None:
        return False, f"click_target_not_found:{used_selector}", False
    if disabled:
        return False, f"click_target_disabled:{used_selector}", True

    wait_ms = _as_int(click_plan.get("wait_after_click_ms"), 1500) or 1500
    before_url = page.url
    try:
        await target.scroll_into_view_if_needed(timeout=5_000)
        await target.click(timeout=10_000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        await asyncio.sleep(wait_ms / 1000)
        await scroll_page(page)
        return True, f"clicked:{used_selector}:url_changed={page.url != before_url}", False
    except PlaywrightTimeoutError:
        return False, f"click_timeout:{used_selector}", False
    except Exception as exc:
        return False, f"click_error:{used_selector}:{exc}", False


async def _extract_and_merge_jobs(
    extractor: JobPatternBridge,
    page,
    all_jobs: list[dict[str, Any]],
    seen_job_keys: set[str],
) -> tuple[int, dict[str, Any]]:
    result = await extractor.extract_current_page(page, page.url)
    page_jobs = _unique_jobs(result.get("jobs") or [])
    new_jobs = []
    for job in page_jobs:
        key = _job_key(job)
        if key and key not in seen_job_keys:
            seen_job_keys.add(key)
            new_jobs.append(job)
            all_jobs.append(job)
    return len(new_jobs), result


async def run_standard_mvp(cdp_url: str, page_url: str) -> dict[str, Any]:
    all_jobs: list[dict[str, Any]] = []
    seen_job_keys: set[str] = set()
    page_reports: list[dict[str, Any]] = []
    navigation_errors: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        print(f"  Loading  : {page_url}")
        await page.goto(page_url, timeout=30_000, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        await scroll_page(page)

        extractor = JobPatternBridge()
        print("  Building job pattern ...")
        pattern_result = await extractor.build_pattern(page, page.url)
        initial_jobs = _unique_jobs(pattern_result.get("jobs") or [])
        for job in initial_jobs:
            key = _job_key(job)
            if key:
                seen_job_keys.add(key)
                all_jobs.append(job)
        page_reports.append({
            "page_index": 1,
            "url": page.url,
            "new_jobs": len(initial_jobs),
            "total_jobs": len(all_jobs),
            "source": "job_pattern_initial",
            "validation": pattern_result.get("validation"),
        })
        print(f"  Page 1 jobs: {len(initial_jobs)}")

        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

        extracted = await page.evaluate(get_extraction_js())
        reduced_html = extracted.get("reduced_html", "")
        total_html_len = extracted.get("total_html_len", 0)
        print(f"\n  Full page HTML length    : {total_html_len:,} chars")
        print(f"  Reduced body HTML length : {len(reduced_html):,} chars")

        print(f"\n  Sending to LLM ({OPENAI_MODEL}) ...")
        pagination_result = analyse_pagination(page.url, reduced_html, total_html_len)
        print(f"\n  ── LLM Result ──")
        print(json.dumps(pagination_result, indent=2))
        print(f"  ── End ──\n")

        navigation = pagination_result.get("navigation") or {}
        url_plan = navigation.get("url") or {}
        click_plan = navigation.get("click") or {}
        max_pages = min(_as_int(navigation.get("max_pages"), MAX_PAGINATION_TEST_PAGES) or 1, MAX_PAGINATION_TEST_PAGES)
        url_value = _next_url_value(url_plan)
        seen_urls = {page.url}
        url_plan_disabled = False
        stop_reason = "completed_or_limit"

        if not pagination_result.get("has_pagination") or navigation.get("method") == "none":
            await browser.close()
            return {
                "jobs": all_jobs,
                "pagination": pagination_result,
                "page_reports": page_reports,
                "navigation_errors": navigation_errors,
                "stop_reason": "no_pagination",
            }

        print("  ── MVP Pagination Run ──")
        for index in range(2, max_pages + 1):
            previous_url = page.url
            navigated = False
            used_method = None
            detail = ""
            url_failed_this_page = False

            if not url_plan_disabled and _has_constructible_url_plan(url_plan, previous_url, url_value):
                target_url = _make_url_from_plan(url_plan, previous_url, url_value)
                if target_url in seen_urls:
                    detail = f"repeated_url:{target_url}"
                    url_plan_disabled = True
                else:
                    print(f"  Page {index}: trying URL {target_url}")
                    ok, detail = await _load_url_page(page, target_url)
                    used_method = "url"
                    navigated = ok
                    if ok:
                        seen_urls.add(page.url)
                    else:
                        url_failed_this_page = True
                        navigation_errors.append({"page_index": index, "method": "url", "detail": detail})
                        print(f"    URL failed ({detail}); falling back to click if available")
                        try:
                            await page.goto(previous_url, timeout=30_000, wait_until="domcontentloaded")
                            await asyncio.sleep(1)
                            await scroll_page(page)
                        except Exception as exc:
                            navigation_errors.append({"page_index": index, "method": "url_restore", "detail": str(exc)})

            if not navigated and click_plan:
                print(f"  Page {index}: trying click fallback")
                before_click_url = page.url
                ok, detail, disabled = await _click_next_page(page, click_plan, index)
                used_method = "click"
                navigated = ok
                if disabled:
                    navigation_errors.append({"page_index": index, "method": "click", "detail": detail})
                    stop_reason = "click_target_disabled"
                    break
                if not ok:
                    navigation_errors.append({"page_index": index, "method": "click", "detail": detail})
                    reduced = await extract_reduced_html_from_page(page)
                    repair = analyse_pagination_repair(
                        page_url=page.url,
                        original_navigation=navigation,
                        target_page=index,
                        errors=navigation_errors[-8:],
                        reduced_html=reduced,
                    )
                    print(f"    Repair Result: {json.dumps(repair)}")
                    repaired_navigation = repair.get("navigation") or {}
                    if repair.get("can_repair") and repaired_navigation.get("method") == "url":
                        url_plan = repaired_navigation.get("url") or {}
                        url_value = _next_url_value(url_plan)
                        continue
                    if repair.get("can_repair") and repaired_navigation.get("method") == "click":
                        click_plan = repaired_navigation.get("click") or {}
                        ok, detail, disabled = await _click_next_page(page, click_plan, index)
                        used_method = "click_repair"
                        navigated = ok
                    if not navigated:
                        stop_reason = "navigation_failed"
                        break

                if page.url != before_click_url:
                    pattern = analyse_url_pattern_from_click(before_click_url, page.url)
                    print(f"    URL Pattern Result: {json.dumps(pattern)}")
                    if pattern.get("can_use_url") and pattern.get("url"):
                        url_plan = pattern["url"]
                        url_value = current_value_after_click_url_pattern(url_plan, url_value)
                        url_plan_disabled = False
                elif url_failed_this_page:
                    url_plan_disabled = True

            if not navigated:
                print(f"  stopped: navigation failed for page {index}: {detail}")
                if stop_reason == "completed_or_limit":
                    stop_reason = "navigation_failed"
                break

            new_count, extraction = await _extract_and_merge_jobs(extractor, page, all_jobs, seen_job_keys)
            page_reports.append({
                "page_index": index,
                "url": page.url,
                "new_jobs": new_count,
                "total_jobs": len(all_jobs),
                "source": used_method,
                "detail": detail,
                "validation": extraction.get("validation"),
            })
            print(f"    extracted new_jobs={new_count} total_jobs={len(all_jobs)}")

            if new_count <= 0:
                print("  stopped: job extraction produced no new jobs; treating as last page")
                stop_reason = "no_new_jobs"
                break

            if url_plan.get("sequence_type") == "cursor":
                url_value = None
            else:
                url_value = _advance_url_value(url_plan, url_value)

        await browser.close()

    return {
        "jobs": all_jobs,
        "pagination": pagination_result,
        "page_reports": page_reports,
        "navigation_errors": navigation_errors,
        "stop_reason": stop_reason,
    }
