"""Deterministic nearest-case field-condition baseline for RiskPilot."""

from functools import lru_cache
from pathlib import Path

import pandas as pd

ABSENCE_LEVELS = ("人员全部到岗", "少量缺勤", "中等缺勤", "大量缺勤")
ABSENCE_SCORE = {value: index for index, value in enumerate(ABSENCE_LEVELS)}
SEVERITY_SCORE = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
RANGES = {
    "temperature_c": (15.0, 50.0),
    "precipitation_mm_24h": (0.0, 100.0),
    "wind_speed_m_s": (0.0, 20.0),
}
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "weather_absence_severity_200.xlsx"


@lru_cache(maxsize=1)
def _case_rows():
    frame = pd.read_excel(DATA_FILE, sheet_name="Data")
    return [{
        "temperature_c": float(row["Temperature °C"]),
        "precipitation_mm_24h": float(row["Precipitation mm/24h"]),
        "wind_speed_m_s": float(row["Wind Speed m/s"]),
        "absence": str(row["Absence"]),
        "severity_label": str(row["Severity Label"]).strip().upper(),
    } for _, row in frame.iterrows()]


def _similarity(query, row):
    values = []
    for name, (low, high) in RANGES.items():
        if query.get(name) is not None:
            difference = abs(float(query[name]) - row[name]) / (high - low)
            values.append(max(0.0, 1.0 - difference))
    absence = query.get("absence")
    if absence in ABSENCE_SCORE:
        difference = abs(ABSENCE_SCORE[absence] - ABSENCE_SCORE[row["absence"]]) / 3
        values.append(max(0.0, 1.0 - difference))
    return sum(values) / len(values) if values else 0.0


def assess_field_conditions(temperature_c, precipitation_mm_24h, wind_speed_m_s,
                            absence="人员全部到岗", top_k=8):
    """Return a stable score, level, confidence and nearest-case evidence."""
    query = {
        "temperature_c": temperature_c,
        "precipitation_mm_24h": precipitation_mm_24h,
        "wind_speed_m_s": wind_speed_m_s,
        "absence": absence if absence in ABSENCE_SCORE else ABSENCE_LEVELS[0],
    }
    ranked = sorted(((_similarity(query, row), row) for row in _case_rows()),
                    key=lambda item: item[0], reverse=True)[:max(1, int(top_k))]
    weight_sum = sum(similarity for similarity, _ in ranked) or 1.0
    weighted = sum(similarity * SEVERITY_SCORE.get(row["severity_label"], 0)
                   for similarity, row in ranked) / weight_sum
    level = "HIGH" if weighted >= 1.5 else "MEDIUM" if weighted >= 0.5 else "LOW"
    distribution = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    evidence = []
    for similarity, row in ranked:
        distribution[row["severity_label"]] += 1
        evidence.append({**row, "similarity": round(similarity, 3)})
    return {
        "score": round(25 + 30 * weighted),
        "level": level,
        "weighted_severity": round(weighted, 3),
        "confidence": round(sum(item[0] for item in ranked) / len(ranked), 3),
        "distribution": distribution,
        "query": query,
        "evidence": evidence,
        "method": "nearest_case_retrieval_v1",
    }
