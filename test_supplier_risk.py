# -*- coding: utf-8 -*-
"""Offline tests for supplier_risk.py. No network / real AI calls."""

from supplier_risk import (
    RISK_DIMENSIONS,
    _supplier_cache_key,
    _supplier_query_fingerprint,
    calculate_supplier_risk,
    risk_level_from_score,
)


def _research(score=20, status="CONFIRMED", identity="CONFIRMED"):
    return {
        "identity_match": identity,
        "dimensions": [
            {
                "key": key,
                "score": score,
                "evidence_status": status,
                "summary": "",
                "evidence_urls": [],
            }
            for key in RISK_DIMENSIONS
        ],
    }


def test_level_thresholds():
    assert risk_level_from_score(0) == "LOW"
    assert risk_level_from_score(34.9) == "LOW"
    assert risk_level_from_score(35) == "MEDIUM"
    assert risk_level_from_score(64.9) == "MEDIUM"
    assert risk_level_from_score(65) == "HIGH"
    assert risk_level_from_score(100) == "HIGH"


def test_complete_low_risk_evidence_stays_low():
    result = calculate_supplier_risk(_research(score=20))
    assert result["score"] == 20.0
    assert result["coverage"] == 100.0
    assert result["confidence"] == "HIGH"
    assert result["level"] == "LOW"


def test_missing_evidence_is_not_treated_as_safe():
    result = calculate_supplier_risk(_research(score=50, status="INSUFFICIENT"))
    assert result["coverage"] == 0.0
    assert result["uncertainty_penalty"] == 8.0
    assert result["score"] == 58.0
    assert result["level"] == "MEDIUM"
    assert result["confidence"] == "LOW"


def test_high_risk_evidence_is_high():
    result = calculate_supplier_risk(_research(score=85))
    assert result["score"] == 85.0
    assert result["level"] == "HIGH"


def test_query_fingerprint_is_language_independent_but_locale_cache_is_not():
    fp1 = _supplier_query_fingerprint("ACME Ltd", "UK", "123", "https://acme.example")
    fp2 = _supplier_query_fingerprint(" acme LTD ", " uk ", "123", "https://acme.example")
    assert fp1 == fp2
    assert _supplier_cache_key(fp1, "en") != _supplier_cache_key(fp1, "zh-CN")
