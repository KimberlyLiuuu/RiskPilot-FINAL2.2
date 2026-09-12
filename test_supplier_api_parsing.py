# -*- coding: utf-8 -*-
"""Offline parser tests. No network calls."""

from supplier_risk import _clean_json_output, _extract_output_text


def test_extract_standard_responses_output():
    raw = {
        "status": "completed",
        "output": [
            {"type": "web_search_call", "action": {"type": "search", "query": "x"}},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"ok": true}'}
                ],
            },
        ],
    }
    assert _extract_output_text(raw) == '{"ok": true}'


def test_extract_top_level_output_text_fallback():
    assert _extract_output_text({"output_text": '{"ok": true}'}) == '{"ok": true}'


def test_clean_markdown_json():
    text = '```json\n{"ok": true}\n```'
    assert _clean_json_output(text) == '{"ok": true}'
