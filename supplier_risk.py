# -*- coding: utf-8 -*-
"""RiskPilot supplier / building-material company due-diligence page.

Design principles
-----------------
1. DeepSeek Web Search gathers and evaluates public evidence.
2. DeepSeek does NOT decide the final RiskPilot level directly.
3. Python deterministically aggregates eight risk dimensions into LOW/MEDIUM/HIGH.
4. Missing public information is treated as uncertainty, never as proof of safety.
5. Only public information is requested. Non-public bank/account information must not
   be inferred or claimed.
6. The search is user-triggered only; page reruns never call the API automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import requests
import streamlit as st
from dotenv import load_dotenv

from i18n import get_current_language, risk_label, t
from utils.locale_registry import ai_language_name


DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_SUPPLIER_MODEL", "deepseek-v4-flash")

# Risk weights sum to 100. Keep the internal keys stable; only rendered labels are
# localized. Scores are risk scores: 0 = very low concern, 100 = very high concern.
RISK_DIMENSIONS: Dict[str, Dict[str, Any]] = {
    "legal_disputes": {"weight": 16, "label_key": "supplier.dimension.legal"},
    "credit_financial": {"weight": 18, "label_key": "supplier.dimension.credit"},
    "regulatory_compliance": {"weight": 12, "label_key": "supplier.dimension.regulatory"},
    "product_quality": {"weight": 16, "label_key": "supplier.dimension.quality"},
    "delivery_performance": {"weight": 14, "label_key": "supplier.dimension.delivery"},
    "corporate_stability": {"weight": 10, "label_key": "supplier.dimension.corporate"},
    "safety_environment": {"weight": 7, "label_key": "supplier.dimension.hse"},
    "reputation_transparency": {"weight": 7, "label_key": "supplier.dimension.reputation"},
}

EVIDENCE_COVERAGE_FACTOR = {
    "CONFIRMED": 1.0,
    "MIXED": 0.6,
    "INSUFFICIENT": 0.0,
}

IDENTITY_VALUES = {"CONFIRMED", "LIKELY", "AMBIGUOUS", "NOT_CONFIRMED"}
EVIDENCE_VALUES = set(EVIDENCE_COVERAGE_FACTOR)
SOURCE_RELIABILITY = {"HIGH", "MEDIUM", "LOW"}

SUPPLIER_RESULT_CACHE_KEY = "supplier_risk_results_by_locale"


def _ai_language_for_locale(locale: str) -> str:
    """Human-readable language name for DeepSeek output instructions."""
    try:
        return ai_language_name(locale) or "English"
    except Exception:
        return "English"


def _supplier_query_fingerprint(
    company_name: str,
    jurisdiction: str = "",
    registration_id: str = "",
    website: str = "",
) -> str:
    """Stable cache identity for one supplier query, independent of UI language."""
    normalized = "\n".join([
        str(company_name or "").strip().casefold(),
        str(jurisdiction or "").strip().casefold(),
        str(registration_id or "").strip().casefold(),
        str(website or "").strip().casefold(),
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _supplier_result_cache() -> Dict[str, Any]:
    """Per-session multilingual supplier result cache."""
    cache = st.session_state.get(SUPPLIER_RESULT_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        st.session_state[SUPPLIER_RESULT_CACHE_KEY] = cache
    return cache


def _supplier_cache_key(query_fingerprint: str, locale: str) -> str:
    return f"{query_fingerprint}:{locale}"



def _clamp(value: Any, low: int = 0, high: int = 100, default: int = 50) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def risk_level_from_score(score: float) -> str:
    """Deterministic RiskPilot supplier level."""
    if score >= 65:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def _safe_http_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _build_schema() -> Dict[str, Any]:
    """Structured-output schema for the web-research call."""
    dimension_keys = list(RISK_DIMENSIONS)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "company",
            "identity_match",
            "executive_summary",
            "dimensions",
            "red_flags",
            "positive_signals",
            "sources",
            "limitations",
        ],
        "properties": {
            "company": {
                "type": "object",
                "additionalProperties": False,
                "required": ["legal_name", "jurisdiction", "registration_id", "website"],
                "properties": {
                    "legal_name": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                    "registration_id": {"type": "string"},
                    "website": {"type": "string"},
                },
            },
            "identity_match": {
                "type": "string",
                "enum": ["CONFIRMED", "LIKELY", "AMBIGUOUS", "NOT_CONFIRMED"],
            },
            "executive_summary": {"type": "string"},
            "dimensions": {
                "type": "array",
                "minItems": 8,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "score", "evidence_status", "summary", "evidence_urls"],
                    "properties": {
                        "key": {"type": "string", "enum": dimension_keys},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "evidence_status": {
                            "type": "string",
                            "enum": ["CONFIRMED", "MIXED", "INSUFFICIENT"],
                        },
                        "summary": {"type": "string"},
                        "evidence_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 10,
                        },
                    },
                },
            },
            "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "positive_signals": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "sources": {
                "type": "array",
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "title",
                        "url",
                        "publisher",
                        "published_at",
                        "source_type",
                        "reliability",
                        "claim",
                    ],
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "publisher": {"type": "string"},
                        "published_at": {"type": "string"},
                        "source_type": {"type": "string"},
                        "reliability": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                        "claim": {"type": "string"},
                    },
                },
            },
            "limitations": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        },
    }


def _build_research_prompt(
    company_name: str,
    jurisdiction: str = "",
    registration_id: str = "",
    website: str = "",
    target_locale: str = "en",
) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    output_language = _ai_language_for_locale(target_locale)
    return f"""
You are RiskPilot's supplier due-diligence research agent.
Today is {today}.

TARGET COMPANY
Legal/company name supplied by user: {company_name}
Country / jurisdiction supplied by user: {jurisdiction or 'Not supplied'}
Registration / tax / credit ID supplied by user: {registration_id or 'Not supplied'}
Website supplied by user: {website or 'Not supplied'}

OUTPUT LANGUAGE
Write ALL human-readable analytical text in {output_language}.
Keep JSON field names, enum tokens, internal dimension keys, LOW/MEDIUM/HIGH-like
machine tokens, company registration numbers and URLs unchanged. Preserve the exact
legal company name and source title when translating them could create ambiguity.
The fields executive_summary, every dimension.summary, red_flags, positive_signals,
source.claim and limitations must be written in {output_language}.

Use the web_search tool extensively. Research the exact legal entity, not merely a
similar company name. Prefer current, primary, official sources and cross-check
important adverse findings.

SEARCH AND ASSESS THESE EIGHT DIMENSIONS
1. legal_disputes
   - court judgments, litigation, arbitration announcements, enforcement cases,
     judgment-debtor/default lists, bankruptcy/insolvency proceedings.
   - A lawsuit or arbitration is NOT automatically misconduct. Distinguish claimant,
     defendant, respondent, third party and case status whenever the source allows.

2. credit_financial
   - public credit/default records, tax arrears where legally public, bond/default
     events, insolvency indicators, audited/public financial distress, public credit
     ratings and PUBLICLY AVAILABLE bank credit certificates/credit facilities.
   - Never claim access to private bank accounts, private bank references or private
     credit files. If bank credit evidence is not public, mark it unavailable.

3. regulatory_compliance
   - administrative penalties, procurement debarment/blacklists, sanctions,
     customs/tax/regulatory actions, licence suspensions and major compliance issues.

4. product_quality
   - product-quality inspections, recalls, certification status, counterfeit/fraud
     findings, failed testing, material-standard violations, verified serious quality
     complaints and regulator notices.

5. delivery_performance
   - credible evidence of repeated late delivery, contract non-performance,
     termination for default, supplier blacklisting, material shortages attributable
     to the company, or strong evidence of reliable delivery/performance.

6. corporate_stability
   - registration/operating status, abnormal-operation status, dissolution,
     insolvency, frequent controlling-owner/director changes, major ownership stress,
     business continuity indicators. Do not treat registered capital alone as proof
     of solvency.

7. safety_environment
   - workplace safety penalties, serious accidents attributable to the company,
     environmental enforcement, pollution incidents, permit problems and HSE record.

8. reputation_transparency
   - credible adverse media, material controversies, transparency of ownership and
     disclosures, consistency between official records and company claims. Treat
     anonymous reviews/social posts as weak evidence unless independently verified.

SCORING FOR EACH DIMENSION
- score is RISK, 0 = very low concern, 100 = very high concern.
- Use 50 when evidence is genuinely insufficient rather than pretending missing data
  is good news.
- evidence_status = CONFIRMED when strong primary/official evidence supports the
  assessment; MIXED when evidence exists but is incomplete/conflicting/secondary;
  INSUFFICIENT when reliable evidence is not enough.
- Do NOT output an overall LOW/MEDIUM/HIGH level. RiskPilot Python will calculate it.

EVIDENCE RULES
- Never invent a record, case, award, penalty, credit rating, certification or source.
- Never attribute a record to the target when identity matching is ambiguous.
- Prefer official corporate registries, courts, regulators, procurement authorities,
  certification bodies, stock-exchange filings, audited reports and established media.
- Search in the local language(s) of the jurisdiction when useful.
- Include URLs only for pages actually found/opened during this research.
- 'No result found' means unknown, NOT 'no issue exists'.
- Pending allegations must be labelled as allegations/pending matters, not facts.
- Produce concise, procurement-oriented analysis.

RETURN FORMAT
Return exactly ONE valid JSON object and no Markdown. Use this shape:
{{
  "company": {{
    "legal_name": "string",
    "jurisdiction": "string",
    "registration_id": "string",
    "website": "https://..."
  }},
  "identity_match": "CONFIRMED | LIKELY | AMBIGUOUS | NOT_CONFIRMED",
  "executive_summary": "string",
  "dimensions": [
    {{
      "key": "one of the eight internal dimension keys",
      "score": 0,
      "evidence_status": "CONFIRMED | MIXED | INSUFFICIENT",
      "summary": "string",
      "evidence_urls": ["https://..."]
    }}
  ],
  "red_flags": ["string"],
  "positive_signals": ["string"],
  "sources": [
    {{
      "title": "string",
      "url": "https://...",
      "publisher": "string",
      "published_at": "string",
      "source_type": "string",
      "reliability": "HIGH | MEDIUM | LOW",
      "claim": "string"
    }}
  ],
  "limitations": ["string"]
}}
The dimensions array must contain exactly the eight required dimension keys once each.
""".strip()


def _extract_output_text(response_json: Dict[str, Any]) -> str:
    """Extract final assistant text from a raw DeepSeek Responses API object.

    DeepSeek normally returns message -> content -> output_text. This helper is
    intentionally tolerant so a small response-shape change does not become a false
    ``deepseek_empty_output``.
    """
    direct = response_json.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: List[str] = []

    for item in response_json.get("output") or []:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue

                part_type = str(part.get("type") or "")
                text = part.get("text")

                if part_type in {"output_text", "text"} and isinstance(text, str) and text:
                    chunks.append(text)

        # Defensive compatibility path.
        text = item.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)

    return "".join(chunks).strip()


def _clean_json_output(text: str) -> str:
    """Remove common Markdown wrappers and isolate one JSON object."""
    text = str(text or "").strip()

    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        return text[start:end + 1].strip()

    return text


def _response_diagnostics(raw: Dict[str, Any]) -> str:
    """Small safe diagnostic string for failed/incomplete/empty API responses."""
    output_types = [
        str(item.get("type") or "")
        for item in (raw.get("output") or [])
        if isinstance(item, dict)
    ]

    details = {
        "status": raw.get("status"),
        "incomplete_details": raw.get("incomplete_details"),
        "error": raw.get("error"),
        "output_item_types": output_types,
        "usage": raw.get("usage"),
        "model": raw.get("model"),
    }

    return json.dumps(details, ensure_ascii=False, indent=2)[:3000]


def _deepseek_request(
    api_key: str,
    prompt: str,
    *,
    model: str,
    structured: bool,
) -> Tuple[Dict[str, Any], str]:
    """One DeepSeek Responses call.

    ``structured=True`` uses JSON Schema. The recovery attempt uses plain text JSON
    because DeepSeek documents that JSON/structured modes may occasionally return
    empty content.
    """
    text_config: Dict[str, Any]

    if structured:
        text_config = {
            "format": {
                "type": "json_schema",
                "name": "riskpilot_supplier_due_diligence",
                "schema": _build_schema(),
            }
        }
    else:
        text_config = {"format": {"type": "text"}}

    payload = {
        "model": model,
        "instructions": (
            "Use web search for public-source supplier due diligence. "
            "Be evidence-grounded, conservative about identity matching, and never "
            "invent facts. Follow the requested output language for every human-readable "
            "analysis field. Return one valid JSON object only."
        ),
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},
        # Low reasoning is deliberate: DeepSeek counts reasoning tokens inside
        # max_output_tokens. High reasoning + many web-search rounds can consume the
        # entire token budget before a final message is emitted.
        "reasoning": {"effort": "low"},
        "max_output_tokens": 12000,
        "text": text_config,
    }

    response = requests.post(
        DEEPSEEK_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=150,
    )

    if response.status_code != 200:
        return {}, f"deepseek_http_{response.status_code}: {response.text[:1500]}"

    try:
        raw = response.json()
    except ValueError:
        return {}, "deepseek_non_json_response"

    return raw, ""




def _extract_search_trace(response_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Keep only non-sensitive server-side web-search actions for audit/display."""
    trace: List[Dict[str, Any]] = []
    for item in response_json.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        row = {
            "type": str(action.get("type") or ""),
            "query": str(action.get("query") or ""),
            "url": _safe_http_url(action.get("url")),
        }
        if any(row.values()):
            trace.append(row)
    return trace[:50]


def _normalize_research_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("supplier_payload_not_object")

    identity = str(payload.get("identity_match") or "NOT_CONFIRMED").upper()
    if identity not in IDENTITY_VALUES:
        identity = "NOT_CONFIRMED"

    raw_dimensions = payload.get("dimensions")
    raw_dimensions = raw_dimensions if isinstance(raw_dimensions, list) else []
    by_key: Dict[str, Dict[str, Any]] = {}

    for item in raw_dimensions:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in RISK_DIMENSIONS or key in by_key:
            continue
        status = str(item.get("evidence_status") or "INSUFFICIENT").upper()
        if status not in EVIDENCE_VALUES:
            status = "INSUFFICIENT"
        urls = item.get("evidence_urls")
        urls = urls if isinstance(urls, list) else []
        by_key[key] = {
            "key": key,
            "score": _clamp(item.get("score"), default=50),
            "evidence_status": status,
            "summary": str(item.get("summary") or "").strip()[:4000],
            "evidence_urls": [u for u in (_safe_http_url(x) for x in urls) if u][:10],
        }

    dimensions: List[Dict[str, Any]] = []
    for key, meta in RISK_DIMENSIONS.items():
        item = by_key.get(key) or {
            "key": key,
            "score": 50,
            "evidence_status": "INSUFFICIENT",
            "summary": "",
            "evidence_urls": [],
        }
        item["weight"] = int(meta["weight"])
        dimensions.append(item)

    sources: List[Dict[str, Any]] = []
    seen_urls = set()
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        url = _safe_http_url(source.get("url"))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        reliability = str(source.get("reliability") or "LOW").upper()
        if reliability not in SOURCE_RELIABILITY:
            reliability = "LOW"
        sources.append({
            "title": str(source.get("title") or url).strip()[:500],
            "url": url,
            "publisher": str(source.get("publisher") or "").strip()[:300],
            "published_at": str(source.get("published_at") or "").strip()[:100],
            "source_type": str(source.get("source_type") or "").strip()[:100],
            "reliability": reliability,
            "claim": str(source.get("claim") or "").strip()[:2000],
        })
        if len(sources) >= 30:
            break

    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}

    return {
        "company": {
            "legal_name": str(company.get("legal_name") or "").strip()[:500],
            "jurisdiction": str(company.get("jurisdiction") or "").strip()[:300],
            "registration_id": str(company.get("registration_id") or "").strip()[:200],
            "website": _safe_http_url(company.get("website")),
        },
        "identity_match": identity,
        "executive_summary": str(payload.get("executive_summary") or "").strip()[:6000],
        "dimensions": dimensions,
        "red_flags": [str(x).strip()[:1500] for x in (payload.get("red_flags") or []) if str(x).strip()][:20],
        "positive_signals": [str(x).strip()[:1500] for x in (payload.get("positive_signals") or []) if str(x).strip()][:20],
        "sources": sources,
        "limitations": [str(x).strip()[:1500] for x in (payload.get("limitations") or []) if str(x).strip()][:20],
    }


def calculate_supplier_risk(research: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic aggregation: AI supplies evidence-level dimension scores only."""
    dimensions = research.get("dimensions") if isinstance(research, dict) else []
    dimensions = dimensions if isinstance(dimensions, list) else []

    score_by_key = {item.get("key"): item for item in dimensions if isinstance(item, dict)}
    weighted_score = 0.0
    coverage = 0.0

    normalized: List[Dict[str, Any]] = []
    for key, meta in RISK_DIMENSIONS.items():
        raw = score_by_key.get(key) or {}
        score = _clamp(raw.get("score"), default=50)
        status = str(raw.get("evidence_status") or "INSUFFICIENT").upper()
        if status not in EVIDENCE_VALUES:
            status = "INSUFFICIENT"
        weight = float(meta["weight"])
        weighted_score += score * weight / 100.0
        coverage += weight * EVIDENCE_COVERAGE_FACTOR[status]

        normalized.append({
            **raw,
            "key": key,
            "score": score,
            "weight": int(weight),
            "evidence_status": status,
            "level": risk_level_from_score(score),
        })

    # Uncertainty is not treated as proof of danger, but low evidence coverage should
    # prevent a falsely reassuring low score. Maximum uncertainty adjustment = +8.
    uncertainty_penalty = round(max(0.0, 100.0 - coverage) * 0.08, 1)
    final_score = round(min(100.0, weighted_score + uncertainty_penalty), 1)
    level = risk_level_from_score(final_score)

    identity = str(research.get("identity_match") or "NOT_CONFIRMED").upper()
    if identity == "CONFIRMED" and coverage >= 80:
        confidence = "HIGH"
    elif identity in {"CONFIRMED", "LIKELY"} and coverage >= 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "score": final_score,
        "level": level,
        "coverage": round(coverage, 1),
        "confidence": confidence,
        "uncertainty_penalty": uncertainty_penalty,
        "dimensions": normalized,
    }


def deepseek_supplier_research(
    company_name: str,
    jurisdiction: str = "",
    registration_id: str = "",
    website: str = "",
    target_locale: str = "en",
) -> Dict[str, Any]:
    """User-triggered DeepSeek web research with one automatic recovery attempt."""
    load_dotenv()

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {"success": False, "error": "missing_api_key"}

    company_name = str(company_name or "").strip()[:300]
    jurisdiction = str(jurisdiction or "").strip()[:200]
    registration_id = str(registration_id or "").strip()[:150]
    website = str(website or "").strip()[:500]

    if len(company_name) < 2:
        return {"success": False, "error": "company_name_required"}

    prompt = _build_research_prompt(
        company_name,
        jurisdiction,
        registration_id,
        website,
        target_locale=target_locale,
    )

    # Attempt 1: fast model + strict JSON Schema.
    # Attempt 2 (only if needed): same web research but plain-text JSON, avoiding the
    # occasional empty-content behaviour documented for JSON/structured output.
    attempts = [
        {
            "model": DEEPSEEK_MODEL,
            "structured": True,
            "label": "structured",
        },
        {
            "model": "deepseek-v4-flash",
            "structured": False,
            "label": "plain_json_recovery",
        },
    ]

    last_error = "deepseek_unknown_failure"
    last_detail = ""
    last_raw: Dict[str, Any] = {}

    for attempt_number, attempt in enumerate(attempts, start=1):
        try:
            raw, request_error = _deepseek_request(
                api_key,
                prompt,
                model=attempt["model"],
                structured=bool(attempt["structured"]),
            )
        except requests.exceptions.Timeout:
            last_error = "deepseek_timeout"
            last_detail = (
                f"attempt={attempt_number}; model={attempt['model']}; "
                f"mode={attempt['label']}"
            )
            continue
        except requests.RequestException as exc:
            last_error = "deepseek_network_error"
            last_detail = str(exc)[:1500]
            continue

        if request_error:
            last_error = request_error.split(":", 1)[0]
            last_detail = request_error[:3000]
            continue

        last_raw = raw
        status = str(raw.get("status") or "")

        if status == "failed":
            last_error = "deepseek_response_failed"
            last_detail = _response_diagnostics(raw)
            continue

        text = _extract_output_text(raw)

        if not text:
            # This is particularly useful for the user's current error:
            # HTTP 200 + no final assistant message.
            if status == "incomplete":
                reason = (
                    (raw.get("incomplete_details") or {}).get("reason")
                    if isinstance(raw.get("incomplete_details"), dict)
                    else ""
                )
                last_error = (
                    f"deepseek_incomplete_{reason}"
                    if reason
                    else "deepseek_incomplete"
                )
            else:
                last_error = "deepseek_empty_output"

            last_detail = _response_diagnostics(raw)
            continue

        cleaned = _clean_json_output(text)

        try:
            model_payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            last_error = "deepseek_invalid_json_output"
            last_detail = (
                f"{exc}\n\n"
                f"response diagnostics:\n{_response_diagnostics(raw)}\n\n"
                f"output preview:\n{cleaned[:1500]}"
            )
            continue

        try:
            research = _normalize_research_payload(model_payload)
        except Exception as exc:
            last_error = "supplier_payload_invalid"
            last_detail = str(exc)[:1500]
            continue

        scoring = calculate_supplier_risk(research)

        return {
            "success": True,
            "researched_at": datetime.now(timezone.utc).isoformat(),
            "locale": target_locale,
            "research": research,
            "scoring": scoring,
            "search_trace": _extract_search_trace(raw),
            "model": raw.get("model") or attempt["model"],
            "usage": raw.get("usage") if isinstance(raw.get("usage"), dict) else {},
            "api_attempt": attempt_number,
            "response_status": status or "completed",
        }

    return {
        "success": False,
        "error": last_error,
        "detail": last_detail or _response_diagnostics(last_raw),
    }


def _render_bullets(title_key: str, values: List[str], empty_key: str = "supplier.none") -> None:
    st.subheader(t(title_key))
    if not values:
        st.caption(t(empty_key))
        return
    for value in values:
        st.markdown(f"- {value}")


def _identity_label(value: str) -> str:
    mapping = {
        "CONFIRMED": "supplier.identity.confirmed",
        "LIKELY": "supplier.identity.likely",
        "AMBIGUOUS": "supplier.identity.ambiguous",
        "NOT_CONFIRMED": "supplier.identity.not_confirmed",
    }
    key = mapping.get(str(value or "").upper(), "supplier.identity.not_confirmed")
    return t(key)


def _confidence_label(value: str) -> str:
    mapping = {
        "HIGH": "supplier.confidence.high",
        "MEDIUM": "supplier.confidence.medium",
        "LOW": "supplier.confidence.low",
    }
    return t(mapping.get(str(value or "").upper(), "supplier.confidence.low"))


def _reliability_label(value: str) -> str:
    mapping = {
        "HIGH": "supplier.reliability.high",
        "MEDIUM": "supplier.reliability.medium",
        "LOW": "supplier.reliability.low",
    }
    return t(mapping.get(str(value or "").upper(), "supplier.reliability.low"))


def supplier_risk_page() -> None:
    """Streamlit page shown from the RiskPilot sidebar.

    UI labels always follow ``ui_lang``. AI-generated supplier analysis is generated
    explicitly for the current UI language and cached by (supplier query, locale), so
    switching languages never silently reuses another language's analysis and never
    triggers an AI call by itself.
    """
    current_locale = get_current_language()

    st.title(t("supplier.title"))
    st.caption(t("supplier.caption"))
    st.info(t("supplier.disclaimer"))

    c1, c2 = st.columns(2)
    with c1:
        company_name = st.text_input(
            t("supplier.field.company_name"),
            key="supplier_company_name",
            placeholder=t("supplier.placeholder.company_name"),
        )
        registration_id = st.text_input(
            t("supplier.field.registration_id"),
            key="supplier_registration_id",
            placeholder=t("supplier.placeholder.registration_id"),
        )
    with c2:
        jurisdiction = st.text_input(
            t("supplier.field.jurisdiction"),
            key="supplier_jurisdiction",
            placeholder=t("supplier.placeholder.jurisdiction"),
        )
        website = st.text_input(
            t("supplier.field.website"),
            key="supplier_website",
            placeholder="https://",
        )

    query_fingerprint = _supplier_query_fingerprint(
        company_name,
        jurisdiction,
        registration_id,
        website,
    )
    cache_key = _supplier_cache_key(query_fingerprint, current_locale)
    cache = _supplier_result_cache()

    search_clicked = st.button(
        t("supplier.button.search"),
        type="primary",
        use_container_width=True,
    )

    if search_clicked:
        if not str(company_name or "").strip():
            st.warning(t("supplier.warning.company_required"))
        else:
            with st.spinner(
                t(
                    "supplier.spinner.searching",
                    language=_ai_language_for_locale(current_locale),
                )
            ):
                result = deepseek_supplier_research(
                    company_name=company_name,
                    jurisdiction=jurisdiction,
                    registration_id=registration_id,
                    website=website,
                    target_locale=current_locale,
                )
            cache[cache_key] = result

    result = cache.get(cache_key)

    # If the same supplier has been researched in another locale, do not leak that
    # language's prose into the current UI. The user explicitly regenerates in the
    # current language, matching RiskPilot's existing no-auto-AI-on-language-switch rule.
    if not isinstance(result, dict):
        same_query_other_locale = any(
            key.startswith(f"{query_fingerprint}:")
            for key in cache
            if key != cache_key
        )
        if same_query_other_locale and str(company_name or "").strip():
            st.info(
                t(
                    "supplier.info.result_other_language",
                    language=_ai_language_for_locale(current_locale),
                )
            )
        return

    if not result.get("success"):
        error = str(result.get("error") or "unknown_error")
        if error == "missing_api_key":
            st.error(t("supplier.error.no_api_key"))
        else:
            st.error(t("supplier.error.search_failed", error=error))
        detail = str(result.get("detail") or "").strip()
        if detail:
            with st.expander(t("supplier.error.details")):
                st.code(detail, language="text")
        return

    research = result.get("research") or {}
    scoring = result.get("scoring") or {}
    company = research.get("company") or {}

    st.markdown("---")
    st.header(t("supplier.result.title"))
    if company.get("legal_name"):
        st.subheader(company["legal_name"])

    st.caption(
        t(
            "supplier.result.language",
            language=_ai_language_for_locale(result.get("locale") or current_locale),
        )
    )

    identity = str(research.get("identity_match") or "NOT_CONFIRMED")
    if identity in {"AMBIGUOUS", "NOT_CONFIRMED"}:
        st.warning(t("supplier.warning.identity"))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("supplier.metric.level"), risk_label(scoring.get("level", "MEDIUM")))
    m2.metric(t("supplier.metric.score"), f"{scoring.get('score', 0):.1f}/100")
    m3.metric(t("supplier.metric.coverage"), f"{scoring.get('coverage', 0):.0f}%")
    m4.metric(t("supplier.metric.confidence"), _confidence_label(scoring.get("confidence", "LOW")))

    st.caption(
        t(
            "supplier.caption.identity",
            identity=_identity_label(identity),
            penalty=f"{scoring.get('uncertainty_penalty', 0):.1f}",
        )
    )

    summary = str(research.get("executive_summary") or "").strip()
    if summary:
        st.subheader(t("supplier.summary.title"))
        st.write(summary)

    st.subheader(t("supplier.dimensions.title"))
    rows = []
    for item in scoring.get("dimensions") or []:
        key = item.get("key")
        meta = RISK_DIMENSIONS.get(key, {})
        rows.append({
            t("supplier.table.dimension"): t(meta.get("label_key", "supplier.dimension.unknown")),
            t("supplier.table.weight"): f"{item.get('weight', 0)}%",
            t("supplier.table.score"): item.get("score", 50),
            t("supplier.table.level"): risk_label(item.get("level", "MEDIUM")),
            t("supplier.table.evidence"): t(
                {
                    "CONFIRMED": "supplier.evidence.confirmed",
                    "MIXED": "supplier.evidence.mixed",
                    "INSUFFICIENT": "supplier.evidence.insufficient",
                }.get(item.get("evidence_status"), "supplier.evidence.insufficient")
            ),
            t("supplier.table.analysis"): item.get("summary", ""),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        _render_bullets("supplier.red_flags.title", research.get("red_flags") or [])
    with right:
        _render_bullets("supplier.positive_signals.title", research.get("positive_signals") or [])

    st.subheader(t("supplier.sources.title"))
    sources = research.get("sources") or []
    if not sources:
        st.caption(t("supplier.none"))
    else:
        for index, source in enumerate(sources, start=1):
            with st.container(border=True):
                st.markdown(f"**{index}. {source.get('title') or source.get('url')}**")

                reliability = _reliability_label(source.get("reliability", "LOW"))
                meta = " · ".join(
                    x for x in [
                        source.get("publisher", ""),
                        source.get("published_at", ""),
                        source.get("source_type", ""),
                        reliability,
                    ] if x
                )
                if meta:
                    st.caption(meta)

                if source.get("claim"):
                    st.write(source["claim"])

                url = _safe_http_url(source.get("url"))
                if url:
                    st.link_button(t("supplier.button.open_source"), url)

    _render_bullets("supplier.limitations.title", research.get("limitations") or [])

    trace = result.get("search_trace") or []
    if trace:
        with st.expander(t("supplier.search_trace.title")):
            st.dataframe(trace, use_container_width=True, hide_index=True)

    st.caption(t("supplier.footer"))
