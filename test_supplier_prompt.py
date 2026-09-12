# -*- coding: utf-8 -*-
"""Regression test for supplier prompt construction. No network / AI calls."""

import ast
from pathlib import Path

SOURCE = Path(__file__).with_name("supplier_risk.py")


def _load_prompt_function():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    module = ast.Module(
        body=[
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_build_research_prompt"
        ],
        type_ignores=[],
    )

    from datetime import datetime, timezone

    namespace = {
        "datetime": datetime,
        "timezone": timezone,
        "_ai_language_for_locale": (
            lambda locale: "Simplified Chinese" if locale == "zh-CN" else "English"
        ),
    }

    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["_build_research_prompt"]


def test_prompt_builds_without_format_error():
    build = _load_prompt_function()
    prompt = build(
        "中国建材集团有限公司",
        jurisdiction="中国",
        target_locale="zh-CN",
    )

    assert '"company": {' in prompt
    assert '"legal_name": "string"' in prompt
    assert "Simplified Chinese" in prompt
    assert "中国建材集团有限公司" in prompt


def test_prompt_contains_all_eight_dimension_keys():
    build = _load_prompt_function()
    prompt = build("Example Ltd", jurisdiction="UK", target_locale="en")

    for key in (
        "legal_disputes",
        "credit_financial",
        "regulatory_compliance",
        "product_quality",
        "delivery_performance",
        "corporate_stability",
        "safety_environment",
        "reputation_transparency",
    ):
        assert key in prompt
