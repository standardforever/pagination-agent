from __future__ import annotations

import asyncio
import json
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .browser import extract_reduced_html_from_page, scroll_page
from .click import click_attempt_report, find_click_target
from .config import MAX_PAGINATION_TEST_PAGES, MAX_REPAIR_ATTEMPTS
from .llm import analyse_pagination_repair, analyse_url_pattern_from_click
from .url_utils import _as_int, _increment_value, _make_url_from_plan


async def continue_url_pagination_on_page(
    page,
    url_plan: dict[str, Any],
    base_url: str,
    start_index: int,
    max_pages: int,
    seen_urls: set[str],
) -> None:
    sequence_type = url_plan.get("sequence_type")
    increment = _as_int(url_plan.get("increment"), 1) or 1
    value = url_plan.get("next_value")
    if not value:
        current = url_plan.get("current_value")
        if current:
            value = _increment_value(str(current), sequence_type, increment)

    # The clicked page already landed on next_value, so advance once before loading.
    if value is not None and start_index > 2:
        value = _increment_value(str(value), sequence_type, increment)

    for index in range(start_index, max_pages + 1):
        target_url = _make_url_from_plan(url_plan, base_url, str(value) if value is not None else None)
        if not target_url:
            print("  URL plan has no constructible next URL.")
            break
        if target_url in seen_urls:
            print(f"  stopped: generated repeated URL: {target_url}")
            break

        print(f"  Page {index}: loading {target_url}")
        try:
            response = await page.goto(target_url, timeout=30_000, wait_until="domcontentloaded")
            status = response.status if response else "no-response"
            seen_urls.add(page.url)
            await scroll_page(page)
            print(f"    loaded status={status} final_url={page.url}")
            if isinstance(status, int) and status >= 400:
                print(f"    stopped: HTTP status {status}")
                break
        except PlaywrightTimeoutError:
            print("    stopped: timed out loading page")
            break
        except Exception as exc:
            print(f"    stopped: navigation failed: {exc}")
            break

        if sequence_type == "cursor":
            print("    stopped: cursor pagination cannot infer later tokens")
            break

        value = _increment_value(str(value), sequence_type, increment) if value is not None else None
        if value is None:
            print("    stopped: cannot increment next value")
            break


async def test_pagination_plan(cdp_url: str, page_url: str, result: dict[str, Any]) -> None:
    navigation = result.get("navigation") or {}
    method = navigation.get("method")

    print(f"\n  ── Pagination Plan Test ──")
    if not result.get("has_pagination") or method == "none":
        print("  No executable pagination plan.")
        print(f"  ── End ──\n")
        return

    if method == "url":
        await test_url_pagination(cdp_url, page_url, navigation)
    elif method == "click":
        await test_click_pagination(cdp_url, page_url, navigation)
    else:
        print(f"  Unsupported navigation method: {method!r}")

    print(f"  ── End ──\n")


async def test_url_pagination(cdp_url: str, page_url: str, navigation: dict[str, Any]) -> None:
    url_plan = navigation.get("url") or {}
    max_pages = min(_as_int(navigation.get("max_pages"), MAX_PAGINATION_TEST_PAGES) or 1, MAX_PAGINATION_TEST_PAGES)
    sequence_type = url_plan.get("sequence_type")
    increment = _as_int(url_plan.get("increment"), 1) or 1
    value = url_plan.get("next_value")

    if not value:
        current = url_plan.get("current_value")
        if current:
            value = _increment_value(str(current), sequence_type, increment)

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        seen_urls = {page_url}

        for index in range(2, max_pages + 1):
            target_url = _make_url_from_plan(url_plan, page_url, str(value) if value is not None else None)
            if not target_url:
                print("  URL plan has no constructible next URL.")
                break
            if target_url in seen_urls:
                print(f"  stopped: generated repeated URL: {target_url}")
                break
            seen_urls.add(target_url)

            print(f"  Page {index}: {target_url}")
            try:
                response = await page.goto(target_url, timeout=30_000, wait_until="domcontentloaded")
                status = response.status if response else "no-response"
                await scroll_page(page)
                print(f"    loaded status={status} final_url={page.url}")
                if isinstance(status, int) and status >= 400:
                    print(f"    stopped: HTTP status {status}")
                    break
            except PlaywrightTimeoutError:
                print("    stopped: timed out loading page")
                break
            except Exception as exc:
                print(f"    stopped: navigation failed: {exc}")
                break

            if sequence_type == "cursor":
                print("    stopped: cursor pagination cannot infer later tokens")
                break

            value = _increment_value(str(value), sequence_type, increment) if value is not None else None
            if value is None:
                print("    stopped: cannot increment next value")
                break

        await browser.close()


async def test_click_pagination(cdp_url: str, page_url: str, navigation: dict[str, Any]) -> None:
    click_plan = navigation.get("click") or {}
    next_selector = click_plan.get("next_selector")
    wait_ms = _as_int(click_plan.get("wait_after_click_ms"), 1500) or 1500
    max_pages = min(_as_int(navigation.get("max_pages"), MAX_PAGINATION_TEST_PAGES) or 1, MAX_PAGINATION_TEST_PAGES)

    has_fallback = any(click_plan.get(k) for k in (
        "selector_candidates",
        "aria_label_template",
        "text_template",
        "item_selector",
    ))
    if not next_selector and not has_fallback:
        print("  Click plan has no executable click target.")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        seen_urls = set()

        print(f"  Page 1: {page_url}")
        await page.goto(page_url, timeout=30_000, wait_until="domcontentloaded")
        seen_urls.add(page.url)
        await asyncio.sleep(5)
        await scroll_page(page)
        error_log = []
        repair_attempts = 0

        for index in range(2, max_pages + 1):
            try:
                next_button, used_selector, is_disabled = await find_click_target(page, click_plan, index)
                if next_button is None:
                    error_log.append(click_attempt_report(click_plan, index, "click target not found"))

                    print(f"  Page {index}: click target not found; reloading and retrying local methods ...")
                    await page.reload(wait_until="domcontentloaded", timeout=30_000)
                    await asyncio.sleep(5)
                    await scroll_page(page)
                    next_button, used_selector, is_disabled = await find_click_target(page, click_plan, index)

                    if next_button is None and repair_attempts < MAX_REPAIR_ATTEMPTS:
                        repair_attempts += 1
                        print(f"  Page {index}: local retry failed; asking LLM to repair plan ...")
                        reduced_html = await extract_reduced_html_from_page(page)
                        repair = analyse_pagination_repair(
                            page_url=page.url,
                            original_navigation=navigation,
                            target_page=index,
                            errors=error_log,
                            reduced_html=reduced_html,
                        )
                        print(f"    Repair Result: {json.dumps(repair)}")

                        repaired_navigation = repair.get("navigation") or {}
                        if repair.get("can_repair") and repaired_navigation.get("method") == "click":
                            click_plan = repaired_navigation.get("click") or {}
                            next_button, used_selector, is_disabled = await find_click_target(page, click_plan, index)
                            if next_button is None:
                                error_log.append(click_attempt_report(click_plan, index, "repaired click target not found"))
                        elif repair.get("can_repair") and repaired_navigation.get("method") == "url":
                            await continue_url_pagination_on_page(
                                page=page,
                                url_plan=repaired_navigation.get("url") or {},
                                base_url=page.url,
                                start_index=index,
                                max_pages=max_pages,
                                seen_urls=seen_urls,
                            )
                            break

                    if next_button is None:
                        print(f"  stopped: click target not found for page {index}: {used_selector}")
                        if error_log:
                            print(f"  Attempt errors: {json.dumps(error_log[-5:], indent=2)}")
                        break

                if is_disabled:
                    error_log.append(click_attempt_report(click_plan, index, f"target disabled: {used_selector}"))
                    print(f"  stopped: next selector is disabled: {used_selector}")
                    break
                print(f"  Page {index}: clicking {used_selector}")
                before_url = page.url
                before_height = await page.evaluate("() => document.body.scrollHeight")
                await next_button.scroll_into_view_if_needed(timeout=5_000)
                await next_button.click(timeout=10_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass
                await asyncio.sleep(wait_ms / 1000)
                await scroll_page(page)
                seen_urls.add(page.url)
                after_height = await page.evaluate("() => document.body.scrollHeight")
                print(f"    loaded final_url={page.url} height={after_height}")
                if page.url != before_url:
                    print("    URL changed after click; asking LLM for URL pattern ...")
                    pattern = analyse_url_pattern_from_click(before_url, page.url)
                    print(f"    URL Pattern Result: {json.dumps(pattern)}")
                    if pattern.get("can_use_url") and (pattern.get("url") or {}):
                        print("    switching from click pagination to URL pagination")
                        await continue_url_pagination_on_page(
                            page=page,
                            url_plan=pattern["url"],
                            base_url=page.url,
                            start_index=index + 1,
                            max_pages=max_pages,
                            seen_urls=seen_urls,
                        )
                        break
                    print("    URL pattern not usable; continuing with click pagination")
                if page.url == before_url and after_height == before_height:
                    print("    note: URL and height did not change after click")
            except PlaywrightTimeoutError:
                print("    stopped: click or wait timed out")
                break
            except Exception as exc:
                print(f"    stopped: click failed: {exc}")
                break

        await browser.close()
