# -*- coding: utf-8 -*-
"""
RiskPilot 辅助函数测试脚本 (test_helpers.py)

覆盖项目中不依赖网络、不写入项目数据的辅助函数：

    utils/storage.py   risk_score / risk_icon / classify_elevation /
                       terrain_risk_from_elevation / safe_json_from_text /
                       calculate_baseline / new_log_entry
    utils/weather.py   weather_risk
    i18n.py            is_supported_language / load_locale / t /
                       risk_label / mapped_label
    vision_risk.py     clean_json_text / normalize_location /
                       parse_vision_result / get_overall_visual_level /
                       generate_risk_chain

运行方式：

    python test_helpers.py

全部通过时打印汇总并返回退出码 0；任何一项失败则返回退出码 1。
"""

import i18n
import vision_risk

from utils import storage
from utils import weather as weather_utils


# ============================================================
# 简易测试框架
# ============================================================

_PASSED = 0
_FAILED = []


def check(name, condition, detail=""):

    global _PASSED

    if condition:

        _PASSED += 1

        print(f"✅ {name}")

    else:

        _FAILED.append(name)

        message = f"❌ {name}"

        if detail:

            message += f"  ->  {detail}"

        print(message)


def eq(name, actual, expected):

    check(
        name,
        actual == expected,
        f"expected {expected!r}, got {actual!r}",
    )


# ============================================================
# utils/storage.py —— 风险等级辅助函数
# ============================================================

def test_storage_risk_helpers():

    print("\n--- storage: risk_score / risk_icon ---")

    eq("risk_score('HIGH') == 3", storage.risk_score("HIGH"), 3)
    eq("risk_score('high') == 3", storage.risk_score("high"), 3)
    eq("risk_score('Medium') == 2", storage.risk_score("Medium"), 2)
    eq("risk_score('LOW') == 1", storage.risk_score("LOW"), 1)
    eq("risk_score(None) == 1", storage.risk_score(None), 1)

    eq("risk_icon('High')", storage.risk_icon("High"), "🔴")
    eq("risk_icon('MEDIUM')", storage.risk_icon("MEDIUM"), "🟠")
    eq("risk_icon('unknown')", storage.risk_icon("unknown"), "🟢")


# ============================================================
# utils/storage.py —— 地形 / 高程辅助函数
# ============================================================

def test_storage_terrain_helpers():

    print("\n--- storage: classify_elevation / terrain_risk ---")

    eq(
        "classify_elevation(None)",
        storage.classify_elevation(None),
        "Unknown",
    )
    eq(
        "classify_elevation(50)",
        storage.classify_elevation(50),
        "Coastal / low elevation terrain",
    )
    eq(
        "classify_elevation(300)",
        storage.classify_elevation(300),
        "Low-relief / rolling terrain",
    )
    eq(
        "classify_elevation(800)",
        storage.classify_elevation(800),
        "Elevated plateau / hilly terrain",
    )
    eq(
        "classify_elevation(1795)",
        storage.classify_elevation(1795),
        "Highland / elevated terrain",
    )

    eq(
        "terrain_risk(None) == LOW",
        storage.terrain_risk_from_elevation(None),
        "LOW",
    )
    eq(
        "terrain_risk(200) == LOW",
        storage.terrain_risk_from_elevation(200),
        "LOW",
    )
    eq(
        "terrain_risk(1500) == MEDIUM",
        storage.terrain_risk_from_elevation(1500),
        "MEDIUM",
    )


# ============================================================
# utils/storage.py —— JSON 解析辅助函数
# ============================================================

def test_storage_json_helper():

    print("\n--- storage: safe_json_from_text ---")

    eq(
        "safe_json 纯 JSON",
        storage.safe_json_from_text('{"a": 1}'),
        {"a": 1},
    )
    eq(
        "safe_json 文本中的 JSON",
        storage.safe_json_from_text('前缀 {"b": 2} 后缀'),
        {"b": 2},
    )
    eq(
        "safe_json 空字符串",
        storage.safe_json_from_text(""),
        None,
    )
    eq(
        "safe_json 非 JSON 文本",
        storage.safe_json_from_text("not json"),
        None,
    )
    eq(
        "safe_json None",
        storage.safe_json_from_text(None),
        None,
    )


# ============================================================
# utils/storage.py —— 基线评分辅助函数
# ============================================================

def test_storage_baseline_helper():

    print("\n--- storage: calculate_baseline ---")

    calm = storage.calculate_baseline(
        rainfall="",
        drainage="",
        terrain="",
        earthquake="",
        heavy_rain=False,
        flooding=False,
        landslide=False,
        earthquake_hazard=False,
        extreme_heat=False,
        strong_wind=False,
        drought=False,
        wildfire=False,
        other_hazard=False,
    )

    eq("baseline 默认 score == 20", calm["score"], 20)
    eq("baseline 默认 level == LOW", calm["level"], "LOW")

    severe = storage.calculate_baseline(
        rainfall="Extreme",
        drainage="Poor",
        terrain="Mountainous",
        earthquake="High",
        heavy_rain=True,
        flooding=False,
        landslide=False,
        earthquake_hazard=False,
        extreme_heat=False,
        strong_wind=False,
        drought=False,
        wildfire=False,
        other_hazard=False,
    )

    eq("baseline 严重 score == 90", severe["score"], 90)
    eq("baseline 严重 level == HIGH", severe["level"], "HIGH")
    eq(
        "baseline 严重 heavy_rainfall == HIGH",
        severe["risks"]["heavy_rainfall"],
        "HIGH",
    )
    check(
        "baseline 风险链非空",
        len(severe["risk_chains"]) >= 1,
    )
    check(
        "baseline 行动建议非空",
        len(severe["actions"]) >= 1,
    )

    capped = storage.calculate_baseline(
        rainfall="Extreme",
        drainage="Poor",
        terrain="Mountainous",
        earthquake="High",
        heavy_rain=True,
        flooding=True,
        landslide=True,
        earthquake_hazard=True,
        extreme_heat=True,
        strong_wind=True,
        drought=True,
        wildfire=True,
        other_hazard=True,
        elevation=1795,
    )

    eq("baseline 分数上限 100", capped["score"], 100)


# ============================================================
# utils/storage.py —— 日志条目辅助函数
# ============================================================

def test_storage_log_helper():

    print("\n--- storage: new_log_entry ---")

    entry = storage.new_log_entry(
        "today.txt",
        "今日施工情况",
        analysis={"risk_level": "HIGH"},
    )

    check(
        "new_log_entry 字段完整",
        {"id", "filename", "uploaded_at", "content", "analysis"}
        <= set(entry),
    )
    eq("new_log_entry filename", entry["filename"], "today.txt")
    eq("new_log_entry content", entry["content"], "今日施工情况")
    check("new_log_entry id 非空", bool(entry["id"]))
    check("new_log_entry uploaded_at 非空", bool(entry["uploaded_at"]))


# ============================================================
# utils/weather.py —— 天气风险辅助函数
# ============================================================

def test_weather_helper():

    print("\n--- weather: weather_risk ---")

    calm = weather_utils.weather_risk({
        "current": {
            "temperature_2m": 22,
            "precipitation": 0,
            "wind_speed_10m": 8,
        },
        "hourly": {
            "precipitation_probability": [10, 20, 30],
        },
    })

    eq(
        "weather 最大降水概率",
        calm["rain_probability"],
        30,
    )
    eq(
        "weather 平静天气无风险",
        calm["risks"],
        ["No major weather-related construction risk detected."],
    )

    storm = weather_utils.weather_risk({
        "current": {
            "temperature_2m": 36,
            "precipitation": 8,
            "wind_speed_10m": 45,
        },
        "hourly": {
            "precipitation_probability": [50, 80, 60],
        },
    })

    eq(
        "weather 暴雨最大降水概率",
        storm["rain_probability"],
        80,
    )
    check(
        "weather 恶劣天气多风险",
        len(storm["risks"]) >= 4,
        storm["risks"],
    )


# ============================================================
# i18n.py —— 国际化辅助函数
# ============================================================

def test_i18n_helpers():

    print("\n--- i18n: 语言与翻译辅助函数 ---")

    # 固定为英文，保证断言稳定且不受 Streamlit 运行时影响。
    i18n.get_current_language = lambda: "en"
    i18n._cache.clear()

    eq("i18n 支持 en", i18n.is_supported_language("en"), True)
    eq("i18n 支持 zh-CN", i18n.is_supported_language("zh-CN"), True)
    eq("i18n 不支持 xx", i18n.is_supported_language("xx"), False)

    locale = i18n.load_locale("en")

    check(
        "i18n en locale 非空",
        isinstance(locale, dict) and len(locale) > 0,
    )

    missing = "this.key.should.not.exist"
    eq("i18n 缺失 key 原样返回", i18n.t(missing), missing)

    eq("i18n risk_label('HIGH')", i18n.risk_label("HIGH"), "HIGH")
    eq(
        "i18n risk_label(None) 使用 fallback",
        i18n.risk_label(None, fallback="N/A"),
        "N/A",
    )
    eq(
        "i18n risk_label 未知值原样返回",
        i18n.risk_label("CUSTOM"),
        "CUSTOM",
    )

    eq(
        "i18n mapped_label 未映射原样返回",
        i18n.mapped_label("Foo", {}),
        "Foo",
    )
    eq(
        "i18n mapped_label 空值 fallback",
        i18n.mapped_label(None, {}, fallback=""),
        "",
    )

    generic_key = "ontology.chain.generic"
    raw = i18n.t(generic_key)

    check(
        "i18n t 支持占位符格式化",
        raw != generic_key and "TEST" in i18n.t(generic_key, name="TEST"),
    )
    eq(
        "i18n t 格式化失败不抛异常",
        i18n.t(generic_key, wrong=1),
        raw,
    )


# ============================================================
# vision_risk.py —— 视觉风险辅助函数
# ============================================================

def test_vision_helpers():

    print("\n--- vision_risk: 解析与计算辅助函数 ---")

    # 固定为英文，保证风险链等翻译稳定。
    i18n.get_current_language = lambda: "en"
    i18n._cache.clear()

    eq(
        "clean_json_text 空字符串",
        vision_risk.clean_json_text(""),
        "",
    )
    eq(
        "clean_json_text 去除 markdown 包裹",
        vision_risk.clean_json_text('```json\n{"a": 1}\n```'),
        '{"a": 1}',
    )
    eq(
        "clean_json_text 提取 JSON 数组",
        vision_risk.clean_json_text("说明 [1, 2, 3] 结束"),
        "[1, 2, 3]",
    )

    eq(
        "normalize_location 正常坐标",
        vision_risk.normalize_location([0.2, 0.1, 0.5, 0.6]),
        [0.2, 0.1, 0.5, 0.6],
    )
    eq(
        "normalize_location 裁剪越界值",
        vision_risk.normalize_location([-1, 0, 2, 2]),
        [0.0, 0.0, 1.0, 1.0],
    )
    eq(
        "normalize_location 非法框(x2<=x1)",
        vision_risk.normalize_location([0.5, 0.5, 0.2, 0.2]),
        [],
    )
    eq(
        "normalize_location 长度错误",
        vision_risk.normalize_location([0.1, 0.2]),
        [],
    )
    eq(
        "normalize_location 非列表",
        vision_risk.normalize_location("nope"),
        [],
    )

    valid = vision_risk.parse_vision_result({
        "risks": [
            {
                "risk_category": "人员安全",
                "risk_name": "未佩戴安全帽",
                "severity": "high",
                "location": [0.1, 0.1, 0.3, 0.3],
            }
        ]
    })

    eq("parse_vision_result 数量", len(valid), 1)
    eq("parse_vision_result severity 规范化", valid[0]["severity"], "HIGH")
    check("parse_vision_result 注入 rule", bool(valid[0]["rule"]))
    eq(
        "parse_vision_result 非法坐标置空",
        vision_risk.parse_vision_result({
            "risks": [{
                "risk_category": "人员安全",
                "risk_name": "未佩戴安全帽",
                "location": [0.5, 0.5, 0.2, 0.2],
            }]
        })[0]["location"],
        [],
    )

    eq(
        "parse_vision_result 过滤幻觉分类",
        vision_risk.parse_vision_result({
            "risks": [{"risk_category": "不存在", "risk_name": "X"}]
        }),
        [],
    )
    eq(
        "parse_vision_result 非列表输入",
        vision_risk.parse_vision_result("bad"),
        [],
    )

    eq(
        "get_overall_visual_level 空列表 == LOW",
        vision_risk.get_overall_visual_level([]),
        "LOW",
    )
    eq(
        "get_overall_visual_level 取最高级",
        vision_risk.get_overall_visual_level(
            [{"severity": "LOW"}, {"severity": "HIGH"}]
        ),
        "HIGH",
    )
    eq(
        "get_overall_visual_level MEDIUM",
        vision_risk.get_overall_visual_level(
            [{"severity": "LOW"}, {"severity": "MEDIUM"}]
        ),
        "MEDIUM",
    )

    chains = vision_risk.generate_risk_chain(
        [{"risk_name": "未佩戴安全帽"}]
    )
    check("generate_risk_chain 非空", len(chains) >= 1)

    deduped = vision_risk.generate_risk_chain([
        {"risk_name": "未佩戴安全帽"},
        {"risk_name": "未佩戴安全帽"},
    ])
    eq("generate_risk_chain 去重", len(deduped), len(chains))


# ============================================================
# 主入口
# ============================================================

def main():

    print("=" * 55)
    print("RiskPilot 辅助函数测试")
    print("=" * 55)

    test_storage_risk_helpers()
    test_storage_terrain_helpers()
    test_storage_json_helper()
    test_storage_baseline_helper()
    test_storage_log_helper()
    test_weather_helper()
    test_i18n_helpers()
    test_vision_helpers()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 55)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:

        print("失败项：")

        for name in _FAILED:

            print(f"  - {name}")

        raise SystemExit(1)

    print("全部辅助函数测试通过 ✅")


if __name__ == "__main__":

    main()
