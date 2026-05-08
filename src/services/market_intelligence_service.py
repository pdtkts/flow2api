"""Keyword / market insight synthesis from TAS tracker image arrays."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ..core.config import config as app_config
from .llm_provider_chain import METADATA_PROVIDERS, LlmProviderChain, get_csv, normalized_retry_count

_MONTH_ORDER = [
    ("Jan", "January"),
    ("Feb", "February"),
    ("Mar", "March"),
    ("Apr", "April"),
    ("May", "May"),
    ("Jun", "June"),
    ("Jul", "July"),
    ("Aug", "August"),
    ("Sep", "September"),
    ("Oct", "October"),
    ("Nov", "November"),
    ("Dec", "December"),
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        s = str(value).strip().replace(",", "")
        if not s:
            return default
        return int(float(s))
    except Exception:
        return default


def _project_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": raw.get("id"),
        "title": str(raw.get("title") or "")[:500],
        "downloads": _safe_int(raw.get("downloads")),
        "mediaType": raw.get("mediaType"),
        "contentType": raw.get("contentType"),
        "category": raw.get("category"),
        "isAI": raw.get("isAI"),
        "premium": raw.get("premium"),
        "creator": raw.get("creator"),
    }


def _aggregate_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(items)
    ai_ct = sum(1 for x in items if bool(x.get("isAI")))
    by_media: Dict[str, int] = {}
    by_cat: Dict[str, int] = {}
    by_creator: Dict[str, int] = {}
    for x in items:
        mt = str(x.get("mediaType") or "unknown").strip() or "unknown"
        by_media[mt] = by_media.get(mt, 0) + 1
        cat = str(x.get("category") or "unknown").strip() or "unknown"
        by_cat[cat] = by_cat.get(cat, 0) + 1
        cr = str(x.get("creator") or "unknown").strip() or "unknown"
        by_creator[cr] = by_creator.get(cr, 0) + 1

    def top_n(d: Dict[str, int], n: int) -> List[Dict[str, Any]]:
        rows = sorted(d.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:n]
        return [{"name": k, "count": v} for k, v in rows]

    return {
        "total_count": total,
        "ai_pct": round(100.0 * ai_ct / total, 1) if total else 0.0,
        "mediaType_breakdown": top_n(by_media, 12),
        "top_categories": top_n(by_cat, 15),
        "top_creators": top_n(by_creator, 15),
    }


def _normalize_brief(raw: Any, event_name: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    best = raw.get("bestSellers")
    if not isinstance(best, list):
        best = []
    best = [str(x).strip() for x in best if str(x).strip()][:10]
    shots = raw.get("shotList")
    out_shots: List[Dict[str, str]] = []
    if isinstance(shots, list):
        for s in shots[:10]:
            if not isinstance(s, dict):
                continue
            out_shots.append(
                {
                    "idea": str(s.get("idea") or "")[:500],
                    "type": str(s.get("type") or "photo")[:40],
                    "description": str(s.get("description") or "")[:1200],
                    "whyItWorks": str(s.get("whyItWorks") or "")[:800],
                }
            )
    return {
        "event": event_name,
        "bestSellers": best,
        "shotList": out_shots,
    }


def _clamp_pct(v: Any) -> int:
    try:
        n = int(round(float(v)))
    except Exception:
        return 0
    return max(0, min(100, n))


def _normalize_trends(raw: Any) -> List[Dict[str, Any]]:
    by_short = {short: {"demand": 0, "saturation": 0} for short, _ in _MONTH_ORDER}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            m = str(row.get("month") or "").strip()
            if not m:
                continue
            key = None
            for short, long in _MONTH_ORDER:
                if m.lower() in (short.lower(), long.lower()):
                    key = short
                    break
            if key is None:
                continue
            by_short[key]["demand"] = _clamp_pct(row.get("demand"))
            by_short[key]["saturation"] = _clamp_pct(row.get("saturation"))
    return [{"month": short, "demand": by_short[short]["demand"], "saturation": by_short[short]["saturation"]} for short, _ in _MONTH_ORDER]


class MarketIntelligenceService:
    def __init__(self, chain: LlmProviderChain) -> None:
        self._llm = chain

    async def analyze_keyword(
        self,
        *,
        event_name: str,
        raw_data: List[Dict[str, Any]],
        max_items: int,
        backend: Optional[str],
        model: Optional[str],
        fallback_models: Optional[List[str]],
    ) -> Dict[str, Any]:
        insights = list(raw_data[:max_items])
        items_for_stats = [x for x in insights if isinstance(x, dict)]
        stats = _aggregate_stats(items_for_stats)

        scored = sorted(
            items_for_stats,
            key=lambda r: _safe_int(r.get("downloads")),
            reverse=True,
        )[:40]
        top_compact = [_project_item(x) for x in scored]

        allowed = [p for p in METADATA_PROVIDERS if p != "csvgen"]
        provider_chain = self._llm.resolve_provider_chain(
            str(backend or "").strip().lower() or None,
            provider_order_csv=app_config.flow2api_market_provider_order,
            enabled_providers_csv=app_config.flow2api_market_enabled_providers,
            legacy_backend=app_config.flow2api_market_backend or "gemini_native",
            allowed_providers=allowed,
        )
        configured_primary = str(
            app_config.flow2api_market_primary_model
            or app_config.flow2api_market_model
            or "gemini-2.5-flash"
        ).strip()
        configured_enabled = get_csv(app_config.flow2api_market_enabled_models)
        configured_fallback = get_csv(app_config.flow2api_market_fallback_models)
        if not configured_enabled:
            configured_enabled = [configured_primary, *configured_fallback]
        configured_enabled = list(dict.fromkeys([m for m in configured_enabled if m]))
        if configured_primary not in configured_enabled:
            configured_enabled.insert(0, configured_primary)
        default_fallback_chain = [m for m in configured_enabled if m != configured_primary]
        if configured_fallback:
            default_fallback_chain = list(
                dict.fromkeys([m for m in configured_fallback if m and m != configured_primary] + default_fallback_chain)
            )

        use_model = str(model or configured_primary).strip()
        use_fallback = fallback_models if fallback_models is not None else default_fallback_chain
        retry_count = normalized_retry_count(app_config.flow2api_market_provider_retry_count)

        stats_json = json.dumps(stats, ensure_ascii=False)
        tops_json = json.dumps(top_compact, ensure_ascii=False)

        prompt = (
            f'You are a stock-imagery market analyst. Commercial event / keyword theme: "{event_name}".\n'
            "Below is aggregate statistics and a sample of top TAS keyword-search assets (titles, downloads, categories).\n"
            "The trends[].demand and trends[].saturation values are directional estimates for a typical calendar year "
            "(not real external data): synthesize plausible 0-100 integers from the theme and sample.\n\n"
            "Respond with ONLY one JSON object (no markdown fences) using exactly this shape:\n"
            "{\n"
            '  "brief": {\n'
            '    "event": "<string>",\n'
            '    "bestSellers": ["short distilled winning title themes"],\n'
            '    "shotList": [\n'
            "      {\n"
            '        "idea": "concise shoot concept",\n'
            '        "type": "photo|vector|illustration|video|3d",\n'
            '        "description": "1-2 sentences",\n'
            '        "whyItWorks": "one sentence"\n'
            "      }\n"
            "    ]\n"
            "  },\n"
            '  "trends": [\n'
            '    {"month": "Jan", "demand": 0, "saturation": 0},\n'
            "    ... exactly 12 objects in calendar order Jan through Dec ...\n"
            "  ]\n"
            "}\n\n"
            f"Aggregate stats JSON:\n{stats_json}\n\n"
            f"Top sample items JSON:\n{tops_json}\n"
        )

        if not provider_chain:
            raise HTTPException(status_code=400, detail="No enabled market analysis providers configured")

        try:
            parsed = await self._llm.invoke_with_provider_chain(
                providers=provider_chain,
                retry_count=retry_count,
                model=use_model,
                fallback_models=use_fallback,
                prompt_text=prompt,
                image_bytes=None,
                mime_type="text/plain",
                use_cloning_credentials=False,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        brief_raw = parsed.get("brief") if isinstance(parsed, dict) else {}
        trends_raw = parsed.get("trends") if isinstance(parsed, dict) else []

        return {
            "brief": _normalize_brief(brief_raw, (event_name or "").strip() or "event"),
            "trends": _normalize_trends(trends_raw),
            "insights": insights,
            "sources": [],
        }
