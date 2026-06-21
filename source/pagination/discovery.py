from __future__ import annotations

import json
from typing import Any

from .browser import extract_html_from_last_match_downward, get_extraction_js
from .config import OPENAI_MODEL
from .llm import analyse_pagination


async def discover_pagination(page) -> dict[str, Any]:
    extracted = await page.evaluate(get_extraction_js())
    reduced_html = extracted.get("reduced_html", "")
    total_html_len = extracted.get("total_html_len", 0)
    print(f"\n  Full page HTML length    : {total_html_len:,} chars")
    print(f"  Reduced body HTML length : {len(reduced_html):,} chars")
    print(f"\n  Sending pagination HTML to LLM ({OPENAI_MODEL}) ...")
    result = analyse_pagination(page.url, reduced_html, total_html_len)
    print(f"\n  -- Pagination Discovery Result --")
    print(json.dumps(result, indent=2))
    print("  -- End --\n")
    return result


async def discover_bottom_continuation(page, job_selector: str | None) -> dict[str, Any]:
    extracted = await extract_html_from_last_match_downward(page, job_selector)
    bottom_html = str(extracted.get("html") or "")
    controls = extracted.get("controls") or []
    print(f"\n  Bottom continuation HTML length : {len(bottom_html):,} chars")
    if not bottom_html:
        return {
            "has_pagination": False,
            "pagination_type": "none",
            "confidence": 0.0,
            "summary": "No bottom-of-results HTML could be extracted.",
            "navigation": {"method": "none", "url": {}, "click": {}, "stop_when": []},
            "notes": "",
        }

    result = analyse_pagination(page.url, bottom_html[:80_000], len(bottom_html))
    result = _recover_bottom_control_plan(result, controls)
    result["bottom_context"] = {
        "job_selector": job_selector,
        "match_count": extracted.get("match_count"),
        "html_length": extracted.get("html_length"),
        "controls": controls[:20],
    }
    print(f"\n  -- Bottom Continuation Discovery Result --")
    print(json.dumps(result, indent=2))
    print("  -- End --\n")
    return result


def _recover_bottom_control_plan(result: dict[str, Any], controls: list[dict[str, Any]]) -> dict[str, Any]:
    if has_executable_pagination(result):
        return result

    candidate = _first_continuation_control(controls)
    if not candidate:
        return result

    href = str(candidate.get("href") or "").strip()
    href_is_navigable = bool(href and href != "#" and not href.lower().startswith(("javascript:", "void(")))
    selector = candidate.get("selector")
    text = candidate.get("text") or candidate.get("aria_label")

    recovered = dict(result)
    recovered["has_pagination"] = True
    recovered["pagination_type"] = "click"
    recovered["confidence"] = max(float(recovered.get("confidence") or 0), 0.78)
    recovered["summary"] = "Recovered executable bottom continuation control from DOM candidates."
    recovered["navigation"] = {
        "method": "click",
        "max_pages": 3,
        "url": {
            "next_url": href if href_is_navigable else None,
            "url_template": None,
            "page_param": None,
            "sequence_type": None,
            "current_value": None,
            "next_value": None,
            "increment": None,
        },
        "click": {
            "next_selector": selector,
            "disabled_selector": None,
            "wait_after_click_ms": 2000,
            "selector_candidates": [selector] if selector else [],
            "container_selector": None,
            "item_selector": None,
            "active_selector": None,
            "aria_label_template": candidate.get("aria_label") or text,
            "text_template": text,
            "current_page": None,
            "next_page": None,
            "increment": None,
        },
        "stop_when": [],
    }
    recovered["notes"] = (
        f"Recovered from bottom control text={text!r} href={href!r}. "
        "Use click fallback first; URL is included only if href is navigable."
    )
    return recovered


def _first_continuation_control(controls: list[dict[str, Any]]) -> dict[str, Any] | None:
    keywords = (
        "view all",
        "show all",
        "show more",
        "load more",
        "more jobs",
        "more results",
        "next",
        "older",
    )
    for control in controls:
        text = " ".join(
            str(control.get(key) or "")
            for key in ("text", "aria_label", "href")
        ).lower()
        if any(keyword in text for keyword in keywords):
            return control
    return None


def has_executable_pagination(pagination_result: dict[str, Any]) -> bool:
    navigation = pagination_result.get("navigation") or {}
    method = navigation.get("method")
    if not pagination_result.get("has_pagination") or method in (None, "none"):
        return False

    if method == "url":
        url_plan = navigation.get("url") or {}
        click_plan = navigation.get("click") or {}
        has_concrete_url = bool(url_plan.get("next_url"))
        has_values = bool(url_plan.get("current_value") is not None and url_plan.get("next_value") is not None)
        has_click_fallback = any(
            click_plan.get(key)
            for key in (
                "next_selector",
                "selector_candidates",
                "aria_label_template",
                "text_template",
                "item_selector",
                "container_selector",
            )
        )
        confidence = float(pagination_result.get("confidence") or 0)
        if not has_concrete_url and not has_values and not has_click_fallback:
            return False
        if confidence < 0.7 and not has_concrete_url and not has_click_fallback:
            return False

    return True
