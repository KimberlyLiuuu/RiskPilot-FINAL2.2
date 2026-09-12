import os
import json
import base64
import re
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Any

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from i18n import mapped_label, risk_label, t

from utils import vision_display

from utils.content_locale import get_content_locale

from utils.locale_registry import locale_display_name

from utils.localized_content import (
    CONTENT_TYPE_VISION,
    SOURCE_AI_GENERATED,
    localized_content_put,
    make_localized_record,
)


# ============================================================
# ENV / DEEPSEEK API
# ============================================================

load_dotenv()

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

VISION_MODEL = "deepseek-v4-flash-vision-exp"


# ============================================================
# CONSTRUCTION RISK ONTOLOGY
# ============================================================

RISK_ONTOLOGY = {

    "人员安全": {

        "未佩戴安全帽": {
            "rule": "进入施工区域必须佩戴安全帽",
            "suggestion": "立即停止相关作业，要求人员正确佩戴安全帽后再进入施工区域",
            "severity": "HIGH",
        },

        "未穿反光衣": {
            "rule": "施工区域人员应穿着高可见度反光衣",
            "suggestion": "要求相关人员穿着符合要求的反光衣后再进入施工区域",
            "severity": "MEDIUM",
        },

        "未系安全带": {
            "rule": "高处作业必须正确系挂安全带",
            "suggestion": "立即暂停相关高处作业，检查并正确佩戴安全带",
            "severity": "HIGH",
        },

        "危险区域人员进入": {
            "rule": "未经许可不得进入吊装、机械作业等危险区域",
            "suggestion": "立即疏散无关人员，并设置明显警戒区域",
            "severity": "HIGH",
        },
    },

    "设备安全": {

        "塔吊异常": {
            "rule": "塔吊作业前必须检查限位器、钢丝绳及相关安全装置",
            "suggestion": "停止塔吊作业，并由专业人员进行设备检查",
            "severity": "HIGH",
        },

        "机械设备附近人员过近": {
            "rule": "机械设备作业半径内禁止无关人员停留",
            "suggestion": "疏散机械作业范围内人员并加强现场指挥",
            "severity": "HIGH",
        },

        "设备防护缺失": {
            "rule": "机械转动部件及危险部位必须设置安全防护装置",
            "suggestion": "停止设备使用，补充防护装置后再恢复作业",
            "severity": "HIGH",
        },
    },

    "临边/高处": {

        "临边无防护": {
            "rule": "施工临边区域必须设置防护栏杆、安全网等防护设施",
            "suggestion": "立即设置临边防护设施并悬挂警示标志",
            "severity": "HIGH",
        },

        "洞口无盖板": {
            "rule": "楼板、地面等预留洞口必须设置盖板或防护栏杆",
            "suggestion": "立即覆盖洞口或设置可靠围护设施",
            "severity": "HIGH",
        },

        "脚手架防护不足": {
            "rule": "脚手架外侧应设置防护网，作业层应具备完整防护",
            "suggestion": "暂停相关脚手架作业并完善防护设施",
            "severity": "HIGH",
        },
    },

    "用电安全": {

        "电线裸露": {
            "rule": "电缆和导线不得存在裸露、破损等危险情况",
            "suggestion": "立即断电并由专业电工处理裸露或破损线路",
            "severity": "HIGH",
        },

        "临时用电不规范": {
            "rule": "施工现场临时用电必须符合安全配电和漏电保护要求",
            "suggestion": "检查并整改临时配电系统及漏电保护装置",
            "severity": "HIGH",
        },

        "配电箱防护异常": {
            "rule": "施工配电箱应具备防雨、防尘、防误触等防护措施",
            "suggestion": "检查并修复配电箱防护措施",
            "severity": "MEDIUM",
        },
    },

    "消防": {

        "易燃物堆放": {
            "rule": "易燃材料应分类存放并远离明火和高温区域",
            "suggestion": "整理并转移易燃材料，同时检查消防设施",
            "severity": "HIGH",
        },

        "灭火设备缺失": {
            "rule": "施工区域应根据消防要求配置有效灭火设备",
            "suggestion": "及时补充灭火器材并建立定期检查制度",
            "severity": "HIGH",
        },

        "动火区域异常": {
            "rule": "动火区域必须满足施工现场消防安全要求",
            "suggestion": "暂停动火作业，清除附近可燃物并检查消防措施",
            "severity": "HIGH",
        },
    },

    "环境/其他": {

        "现场积水": {
            "rule": "施工场地应保持排水通畅，避免明显积水",
            "suggestion": "及时排除积水并检查现场排水系统",
            "severity": "MEDIUM",
        },

        "材料堆放混乱": {
            "rule": "施工材料应分类、稳定、有序堆放",
            "suggestion": "重新整理材料并设置分类标识",
            "severity": "MEDIUM",
        },
    },
}


# ============================================================
# RISK CATEGORY MAPPING
# ============================================================

CATEGORY_TO_PROJECT_RISK = {
    "人员安全": "施工安全",
    "设备安全": "设备",
    "临边/高处": "施工安全",
    "用电安全": "施工安全",
    "消防": "施工安全",
    "环境/其他": "自然环境",
}


SEVERITY_SCORE = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


SEVERITY_PENALTY = {
    "LOW": 5,
    "MEDIUM": 15,
    "HIGH": 30,
}


# ============================================================
# DISPLAY-ONLY ONTOLOGY LABELS
# ============================================================
# The ontology keys, the JSON field names and the severity values used for
# recognition, matching and parsing stay exactly as they are. The helpers
# below only translate what is rendered on screen, and they fall back to
# the original text whenever a translation key is unavailable.

_CATEGORY_DISPLAY_KEYS = {
    name: f"ontology.category.{name}" for name in RISK_ONTOLOGY
}

_RISK_NAME_DISPLAY_KEYS = {}
_RISK_RULE_DISPLAY_KEYS = {}
_RISK_SUGGESTION_DISPLAY_KEYS = {}

for _category, _items in RISK_ONTOLOGY.items():

    for _risk_name in _items:

        _RISK_NAME_DISPLAY_KEYS[
            _risk_name
        ] = f"ontology.risk.{_risk_name}"

        _RISK_RULE_DISPLAY_KEYS[
            _risk_name
        ] = f"ontology.rule.{_risk_name}"

        _RISK_SUGGESTION_DISPLAY_KEYS[
            _risk_name
        ] = f"ontology.suggestion.{_risk_name}"


def risk_name_label(risk_name) -> str:
    """Display-only label for an ontology risk name."""
    return mapped_label(
        risk_name,
        _RISK_NAME_DISPLAY_KEYS,
    )


def risk_category_label(category) -> str:
    """Display-only label for an ontology risk category."""
    return mapped_label(
        category,
        _CATEGORY_DISPLAY_KEYS,
    )


def risk_rule_label(
    risk_name,
    fallback: str = "",
) -> str:
    """Display-only rule text resolved through a stable translation key."""
    key = _RISK_RULE_DISPLAY_KEYS.get(
        str(risk_name or "").strip()
    )

    if key:

        text = t(key)

        if text != key:
            return text

    return fallback


def risk_suggestion_label(
    risk_name,
    fallback: str = "",
) -> str:
    """Display-only suggestion text resolved through a stable key."""
    key = _RISK_SUGGESTION_DISPLAY_KEYS.get(
        str(risk_name or "").strip()
    )

    if key:

        text = t(key)

        if text != key:
            return text

    return fallback


def risk_chain_label(
    risk_name,
    fallback: str = "",
) -> str:
    """Display-only consequence chain resolved through a stable key."""
    name = str(risk_name or "").strip()

    if name:

        key = f"ontology.chain.{name}"
        text = t(key)

        if text != key:
            return text

    return fallback


# ============================================================
# IMAGE ENCODING
# ============================================================

def encode_image_to_base64(image: Image.Image) -> str:
    """
    Convert PIL image to base64 PNG string.
    """

    buffered = BytesIO()

    image.save(
        buffered,
        format="PNG",
    )

    return base64.b64encode(
        buffered.getvalue()
    ).decode("utf-8")


# ============================================================
# BUILD VISION PROMPT
# ============================================================

def build_vision_prompt() -> str:
    """
    构造施工现场视觉风险检测提示词（阶段 4B：只返回语言无关的 canonical
    detection）。

    识别事实（本体键、坐标、等级、置信度）与自然语言彻底分层：
    * 本 prompt 只产出机器可计算的检测事实，不产出标题 / 原因 / 建议；
    * 自然语言由 :func:`build_vision_text_prompt` 在目标语言下单独生成；
    * 因此识别结果与界面语言无关，切换语言不会重新识别图片。
    """

    ontology_desc = json.dumps(
        RISK_ONTOLOGY,
        ensure_ascii=False,
        indent=2
    )

    category_keys = list(RISK_ONTOLOGY.keys())

    prompt = f"""
You are RiskPilot's construction site visual safety detection model.

Analyse the uploaded construction site photo and return ONLY the detection
facts you can actually observe. Do NOT write any human readable sentence.

ONTOLOGY (the only allowed key space):

{ontology_desc}

Allowed values for "category_key" (verbatim, no translation):
{json.dumps(category_keys, ensure_ascii=False)}

============================================================
DETECTION RULES
============================================================

1. Carefully inspect the entire image before returning the result.

2. You MUST check every one of the following:
- workers without hard hats
- workers without reflective clothing
- missing guardrails at exposed edges
- unprotected floor openings
- unsafe ladders
- cables crossing wet ground or walkways
- standing water
- unstable or disorganized materials
- debris blocking access routes
- unsafe scaffolding

3. Report a finding whenever the unsafe condition is visually more likely
than not. Do not require absolute certainty.

4. The absence of an accident or injury does NOT mean the site is safe.
Identify unsafe conditions that could cause an accident.

5. Before returning an empty findings list, inspect the foreground,
middle ground and background separately. Return an empty list only when
none of the ontology risks are visually present.

6. For personal protective equipment, report missing equipment when the
worker and relevant body area are sufficiently visible.

7. Return every distinct visible risk, not only the most serious one.

============================================================
FIELDS OF ONE FINDING
============================================================

category_key:
    one of the allowed category keys above.

object_key:
    one of the risk names listed under that category, verbatim.

location:
    normalized bounding box.

severity:
    only HIGH, MEDIUM or LOW.

confidence:
    optional number between 0 and 1.

Do NOT return description, evidence, suggestion, reason, recommendation,
summary, score, penalty, level, or any translated text: the human readable
text is written later, in the user's language, from this detection only.

============================================================
OUTPUT FORMAT
============================================================

Return exactly one valid JSON object. No Markdown, no ```json fence, no
explanation outside the JSON.

{{
    "findings": [
        {{
            "category_key": "人员安全",
            "object_key": "未佩戴安全帽",
            "location": [0.60, 0.20, 0.80, 0.85],
            "severity": "HIGH",
            "confidence": 0.82
        }}
    ]
}}

If no risk is clearly visible:

{{
    "findings": []
}}
"""

    return prompt


def ontology_category_keys() -> List[str]:
    """本体允许的风险大类键集合。"""
    return list(RISK_ONTOLOGY.keys())


def ontology_object_keys() -> List[str]:
    """本体允许的具体风险键集合（跨全部大类）。"""
    names: List[str] = []

    for category in RISK_ONTOLOGY.values():

        names.extend(
            category.keys()
        )

    return names


# ============================================================
# JSON CLEANER
# ============================================================

def clean_json_text(content: str) -> str:
    """
    Remove common markdown wrappers around model JSON.
    """

    if not content:
        return ""

    content = content.strip()

    if content.startswith("```json"):
        content = content[7:]

    elif content.startswith("```"):
        content = content[3:]

    if content.endswith("```"):
        content = content[:-3]

    content = content.strip()

    # Try extracting JSON array if the model added extra text.
    match = re.search(
        r"\[[\s\S]*\]",
        content,
    )

    if match:
        return match.group(0).strip()

    return content


# ============================================================
# DEEPSEEK VISION API
# ============================================================

def call_deepseek_vision(
    image: Image.Image,
    prompt: str,
) -> Any:
    """
    调用视觉模型一次，返回原始 JSON（dict/list）。

    阶段 4B：**调用失败一律返回 None**（此前返回 ``{"risks": []}``，与"图片里
    没有风险"无法区分，会把失败当成一次成功的空检测）。调用方必须把 None 当作
    失败处理：不写缓存、不写项目、不 persist。

    图片只在本函数内转 base64，且不写日志、不带本地路径。
    """

    if not DEEPSEEK_API_KEY:
        st.error(t("vision.error_no_api_key"))
        return None

    # ========================================================
    # IMAGE -> BASE64
    # ========================================================

    base64_image = encode_image_to_base64(image)

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    # ========================================================
    # IMPORTANT:
    # 不使用 response_format=json_object
    # DeepSeek 官方说明该模式可能返回空 content
    # ========================================================

    payload = {
        "model": "deepseek-v4-flash-vision-exp",

        # 关闭思考模式
        "thinking": {
            "type": "disabled"
        },

        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                "data:image/png;base64,"
                                f"{base64_image}"
                            ),
                            "detail": "original",
                        },
                    },
                ],
            }
        ],

        "max_tokens": 3000,
    }

    response = None

    try:

        # ====================================================
        # SEND REQUEST
        # ====================================================

        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=90,
        )

        # ====================================================
        # HTTP ERROR
        # ====================================================

        if response.status_code != 200:

            st.error(
                t(
                    "vision.error_http",
                    code=response.status_code,
                )
            )

            try:
                error_data = response.json()

                st.code(
                    json.dumps(
                        error_data,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    language="json",
                )

            except Exception:

                st.code(
                    response.text,
                    language="text",
                )

            return None

        # ====================================================
        # GET RESPONSE
        # ====================================================

        result = response.json()

        choices = result.get("choices", [])

        if not choices:

            st.error(t("vision.error_no_choices"))

            st.code(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                ),
                language="json",
            )

            return None

        choice = choices[0]

        message = choice.get(
            "message",
            {}
        )

        content = message.get(
            "content",
            ""
        )

        # ====================================================
        # DEBUG EMPTY CONTENT
        # ====================================================

        if not content:

            st.error(t("vision.error_empty_content"))

            st.markdown(t("vision.debug_api_info"))

            debug_info = {
                "finish_reason": choice.get(
                    "finish_reason"
                ),
                "message": message,
                "usage": result.get(
                    "usage"
                ),
                "model": result.get(
                    "model"
                ),
            }

            st.code(
                json.dumps(
                    debug_info,
                    ensure_ascii=False,
                    indent=2,
                ),
                language="json",
            )

            return None

        # ====================================================
        # CLEAN RESPONSE
        # ====================================================

        content = content.strip()

        # 去掉 ```json
        if content.startswith("```json"):
            content = content[7:]

        elif content.startswith("```"):
            content = content[3:]

        if content.endswith("```"):
            content = content[:-3]

        content = content.strip()

        # ====================================================
        # TRY DIRECT JSON PARSE
        # ====================================================

        try:

            parsed = json.loads(content)

            # 模型直接返回列表
            if isinstance(parsed, list):
                return {
                    "risks": parsed
                }

            # 正常返回对象
            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

        # ====================================================
        # EXTRACT JSON OBJECT
        # ====================================================

        try:

            start = content.find("{")
            end = content.rfind("}")

            if start != -1 and end != -1:

                json_text = content[
                    start:end + 1
                ]

                parsed = json.loads(
                    json_text
                )

                if isinstance(
                    parsed,
                    dict,
                ):
                    return parsed

        except Exception:
            pass

        # ====================================================
        # EXTRACT JSON ARRAY
        # ====================================================

        try:

            start = content.find("[")
            end = content.rfind("]")

            if start != -1 and end != -1:

                json_text = content[
                    start:end + 1
                ]

                parsed = json.loads(
                    json_text
                )

                if isinstance(
                    parsed,
                    list,
                ):
                    return {
                        "risks": parsed
                    }

        except Exception:
            pass

        # ====================================================
        # CAN'T PARSE
        # ====================================================

        st.error(
            t("vision.error_invalid_json")
        )

        st.markdown(
            t("vision.raw_output")
        )

        st.code(
            content,
            language="text",
        )

        return None

    # ========================================================
    # TIMEOUT
    # ========================================================

    except requests.exceptions.Timeout:

        st.error(
            t("vision.error_timeout")
        )

        return None

    # ========================================================
    # NETWORK ERROR
    # ========================================================

    except requests.exceptions.RequestException as e:

        st.error(
            t(
                "vision.error_network",
                error=e,
            )
        )

        return None

    # ========================================================
    # OTHER ERROR
    # ========================================================

    except Exception as e:

        st.error(
            t(
                "vision.error_unexpected",
                error=e,
            )
        )

        return None
        
# ============================================================
# NORMALIZE RESULT
# ============================================================

def normalize_location(
    location: Any,
) -> List[float]:

    if not isinstance(
        location,
        (list, tuple),
    ):

        return []

    if len(location) != 4:

        return []

    normalized = []

    try:

        for value in location:

            number = float(value)

            number = max(
                0.0,
                min(
                    1.0,
                    number,
                ),
            )

            normalized.append(
                number
            )

    except Exception:

        return []

    x1, y1, x2, y2 = normalized

    if x2 <= x1 or y2 <= y1:

        return []

    return normalized


def parse_vision_result(
    raw_result: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        raw_result,
        dict,
    ):

        raw_result = raw_result.get(
        "findings",
        raw_result.get("risks", []),
        )

    if not isinstance(
        raw_result,
        list,
    ):

        return []

    cleaned_risks = []

    for item in raw_result:

        if not isinstance(
            item,
            dict,
        ):

            continue

        category = str(
        item.get(
        "category_key",
        item.get("risk_category", ""),
          )
        ).strip()

        risk_name = str(
        item.get(
        "object_key",
        item.get("risk_name", ""),
         )
        ).strip()
        if not category or not risk_name:

            continue

        # Reject hallucinated categories.
        if category not in RISK_ONTOLOGY:

            continue

        if risk_name not in RISK_ONTOLOGY[
            category
        ]:

            continue

        ontology_item = RISK_ONTOLOGY[
            category
        ][
            risk_name
        ]

        severity = str(
            item.get(
                "severity",
                ontology_item.get(
                    "severity",
                    "MEDIUM",
                ),
            )
        ).strip().upper()

        if severity not in [
            "LOW",
            "MEDIUM",
            "HIGH",
        ]:

            severity = ontology_item.get(
                "severity",
                "MEDIUM",
            )

        location = normalize_location(
            item.get(
                "location",
                [],
            )
        )

        description = str(
            item.get(
                "description",
                "",
            )
        ).strip()

        evidence = str(
            item.get(
                "evidence",
                "",
            )
        ).strip()

        suggestion = str(
            item.get(
                "suggestion",
                ontology_item.get(
                    "suggestion",
                    "",
                ),
            )
        ).strip()

        cleaned_risks.append({

            "risk_category":
                category,

            "risk_name":
                risk_name,

            "location":
                location,

            "severity":
                severity,

            # 识别置信度属于语言无关事实；缺失或非法一律为 None，绝不臆造。
            "confidence":
                vision_display.coerce_confidence(
                    item.get(
                        "confidence",
                        None,
                    )
                ),

            "description":
                description,

            "evidence":
                evidence,

            "suggestion":
                suggestion,

            "rule":
                ontology_item.get(
                    "rule",
                    "",
                ),
        })

    return cleaned_risks


# ============================================================
# IMAGE ANNOTATION
# ============================================================

def get_font(
    size: int,
):

    font_candidates = [

        "/System/Library/Fonts/PingFang.ttc",

        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",

        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "Arial.ttf",
    ]

    for path in font_candidates:

        try:

            return ImageFont.truetype(
                path,
                size=size,
            )

        except Exception:

            continue

    return ImageFont.load_default()


def draw_risk_boxes(
    image: Image.Image,
    risks: List[Dict[str, Any]],
) -> Image.Image:

    output = image.copy()

    draw = ImageDraw.Draw(
        output
    )

    width, height = output.size

    font = get_font(
        max(
            14,
            int(width * 0.018),
        )
    )

    for risk in risks:

        location = risk.get(
            "location",
            [],
        )

        if not location:

            continue

        if len(location) != 4:

            continue

        x_min, y_min, x_max, y_max = (
            location
        )

        x1 = int(
            x_min * width
        )

        y1 = int(
            y_min * height
        )

        x2 = int(
            x_max * width
        )

        y2 = int(
            y_max * height
        )

        severity = str(
            risk.get(
                "severity",
                "MEDIUM",
            )
        ).upper()

        if severity == "HIGH":

            color = (
                220,
                40,
                40,
            )

        elif severity == "MEDIUM":

            color = (
                240,
                150,
                20,
            )

        else:

            color = (
                40,
                160,
                80,
            )

        line_width = max(
            3,
            int(width / 300),
        )

        draw.rectangle(
            [
                x1,
                y1,
                x2,
                y2,
            ],
            outline=color,
            width=line_width,
        )

        label = (
            f"{risk_label(severity)} · "
            f"{risk_name_label(risk.get('risk_name', ''))}"
        )

        try:

            bbox = draw.textbbox(
                (
                    x1,
                    y1,
                ),
                label,
                font=font,
            )

            text_width = (
                bbox[2] - bbox[0]
            )

            text_height = (
                bbox[3] - bbox[1]
            )

        except Exception:

            text_width = 180
            text_height = 20

        label_y1 = max(
            0,
            y1 - text_height - 12,
        )

        label_y2 = y1

        draw.rectangle(
            [
                x1,
                label_y1,
                min(
                    width,
                    x1 + text_width + 14,
                ),
                label_y2,
            ],
            fill=color,
        )

        draw.text(
            (
                x1 + 7,
                label_y1 + 4,
            ),
            label,
            fill=(
                255,
                255,
                255,
            ),
            font=font,
        )

    return output


# ============================================================
# RISK SUMMARY
# ============================================================

def get_overall_visual_level(
    risks: List[Dict[str, Any]],
) -> str:

    if not risks:

        return "LOW"

    highest = max(
        risks,
        key=lambda risk:
        SEVERITY_SCORE.get(
            str(
                risk.get(
                    "severity",
                    "LOW",
                )
            ).upper(),
            1,
        ),
    )

    level = str(
        highest.get(
            "severity",
            "LOW",
        )
    ).upper()

    if level not in SEVERITY_SCORE:

        return "LOW"

    return level


# ============================================================
# CANONICAL DETECTION (stage 4B)
# ============================================================

#: Vision model that produced the canonical detection.
VISION_MODEL_ID = "deepseek-v4-flash-vision-exp"

#: Project field that stores the language-independent detection.
DETECTION_FIELD = vision_display.DETECTION_FIELD

#: Session cache namespaces.
DETECTION_CACHE = "vision_detection_cache"
TEXT_CACHE = "vision_text_cache"


def vision_allowed_categories() -> List[str]:
    """本体允许的风险大类键（用于 canonical detection 校验）。"""
    return ontology_category_keys()


def vision_allowed_objects() -> List[str]:
    """本体允许的具体风险键（用于 canonical detection 校验）。"""
    return ontology_object_keys()


def build_vision_detection(
    raw_result: Any,
    image_hash: str,
    model_id: str = VISION_MODEL_ID,
) -> Dict[str, Any]:
    """
    把视觉模型的原始响应转成 canonical detection（语言无关事实）。

    * 先经 :func:`parse_vision_result` 做本体幻觉过滤与等级规范化（沿用既有
      过滤语义，本体键保持不变）；
    * 再由 :func:`utils.vision_display.normalize_vision_detection` 归一化结构
      并派生 finding_id / penalty / overall_level / total_penalty；
    * 不合法或空响应返回 ``{}``。
    """
    if raw_result is None:

        return {}

    if not isinstance(raw_result, (dict, list)):

        # 响应形状完全不对：这是失败，不是"没有风险"。
        return {}

    if (
        isinstance(raw_result, dict)
        and "risks" not in raw_result
        and "findings" not in raw_result
    ):

        return {}

    risks = parse_vision_result(raw_result)

    return vision_display.normalize_vision_detection(
        risks,
        image_hash=image_hash,
        model_id=model_id,
        ontology_version=vision_display.VISION_ONTOLOGY_VERSION,
        allowed_objects=vision_allowed_objects(),
        allowed_categories=vision_allowed_categories(),
    )


def vision_detection_problems(
    detection: Dict[str, Any],
) -> List[str]:
    """canonical detection 的校验问题码（空列表表示合法）。"""
    return vision_display.validate_vision_detection(
        detection,
        vision_allowed_objects(),
        vision_allowed_categories(),
    )


def detection_findings_for_project(
    detection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    canonical findings（附兼容别名 `risk_name` / `risk_category` / `severity`）。

    只用于既有 `visual_*` 链接逻辑：不携带任何自然语言，因此写入项目的
    `visual_risks` 永远不会夹带某一语言的自由文本。
    """
    merged = vision_display.merge_vision_display(
        detection
    )

    if not merged:

        return []

    findings = []

    for finding in merged.get("findings") or []:

        findings.append({
            key: value
            for key, value in finding.items()
            if key not in (
                "title",
                "reason",
                "recommendation",
            ) or value
        })

    return findings


#: 无 Streamlit runtime 时（脚本 / 测试）使用的进程内缓存。
_FALLBACK_CACHES: Dict[str, Dict[str, Any]] = {}


def session_cache(name: str) -> Dict[str, Any]:
    """
    取一个会话级缓存字典。

    缓存只是加速手段：它丢失、跨会话重置都不影响协议正确性（真正的真相源是
    project 里的 detection 与 localized_content）。没有 Streamlit runtime 时退回
    进程内字典，保证模块可离线调用。
    """
    try:

        store = st.session_state

        cache = store.get(name)

        if not isinstance(cache, dict):

            cache = {}

            store[name] = cache

        return cache

    except Exception:

        cache = _FALLBACK_CACHES.get(name)

        if not isinstance(cache, dict):

            cache = {}

            _FALLBACK_CACHES[name] = cache

        return cache


def detection_cache() -> Dict[str, Any]:
    """会话级 canonical detection 缓存（不含 locale）。"""
    return session_cache(DETECTION_CACHE)


def text_cache() -> Dict[str, Any]:
    """会话级本地化文本缓存（含 locale）。"""
    return session_cache(TEXT_CACHE)


def remember_detection(
    project_id: str,
    detection: Dict[str, Any],
) -> None:
    """把 canonical detection 写入会话缓存（按 项目 + 图片 + 模型 + 本体 有界）。"""
    if not isinstance(detection, dict) or not detection:

        return

    key = vision_display.vision_detection_cache_key(
        project_id,
        detection.get("image_hash"),
        detection.get("model_id"),
        detection.get("ontology_version"),
    )

    vision_display.bounded_vision_cache_put(
        detection_cache(),
        key,
        detection,
        group=vision_display.vision_detection_cache_group(key),
    )


def cached_detection(
    project_id: str,
    image_hash: str,
    model_id: str = VISION_MODEL_ID,
) -> Dict[str, Any]:
    """读取会话缓存中的 canonical detection（同一图片、同一模型/本体）。"""
    key = vision_display.vision_detection_cache_key(
        project_id,
        image_hash,
        model_id,
        vision_display.VISION_ONTOLOGY_VERSION,
    )

    cached = detection_cache().get(key)

    if isinstance(cached, dict) and cached:

        return cached

    return {}


def remember_text(
    project_id: str,
    record_id: str,
    locale: str,
    input_hash: str,
    payload: Dict[str, Any],
) -> None:
    """把已校验的文本写入会话缓存（按 项目 + 记录 + 语言 有界）。"""
    key = vision_display.vision_text_cache_key(
        project_id,
        record_id,
        locale,
        input_hash,
    )

    vision_display.bounded_vision_cache_put(
        text_cache(),
        key,
        payload,
        group=vision_display.vision_text_cache_group(key),
    )


def detection_for_image(
    project: Dict[str, Any],
    project_id: str,
    image_hash: str,
) -> Dict[str, Any]:
    """
    当前图片的 canonical detection。

    只认"图片 hash 一致"的检测结果：同一项目换了图片就不会复用旧结果，
    项目之间也不会互相串用。
    """
    if not isinstance(project, dict) or not image_hash:

        return {}

    stored = project.get(DETECTION_FIELD)

    if isinstance(stored, dict) and stored.get("image_hash") == image_hash:

        return stored

    return cached_detection(project_id, image_hash)


def ai_generate_vision_text(
    detection: Dict[str, Any],
    project_id: str = "",
    locale: str = None,
) -> Dict[str, Any]:
    """
    用目标内容语言把 canonical detection 写成人类可读文本。

    只调用**文本**模型一次；不读取图片、不调用视觉模型、不改任何识别事实。
    目标语言由调用方显式传入（绝不从界面语言隐式推断），响应必须通过
    :func:`utils.vision_display.text_payload_for_store` 校验，否则返回
    ``{"success": False}`` 且不写入任何地方。

    返回 ``{"success": bool, "payload": {...} or None, "error": str}``。
    """
    context = (
        {
            "project_id": str(project_id),
        }
        if project_id
        else None
    )

    # 只把识别事实交给模型：本体规则与整改建议由显示层用 locale key 翻译，不进
    # prompt，否则模型可能把另一语言的确定性标签抄进正文。目标语言显式传入。
    prompt = vision_display.build_vision_text_prompt(
        detection,
        context=context,
        target_locale=locale,
    )

    try:

        from agents.risk_agent import RiskAgent

        result = RiskAgent().explain_vision_text(prompt)

    except Exception as exc:

        return {
            "success": False,
            "payload": None,
            "error": str(exc),
        }

    payload = vision_display.text_payload_for_store(
        result,
        detection,
    )

    if not payload:

        return {
            "success": False,
            "payload": None,
            "error": "invalid_vision_payload",
        }

    return {
        "success": True,
        "payload": payload,
        "error": "",
    }


def store_vision_text(
    project: Dict[str, Any],
    project_id: str,
    detection: Dict[str, Any],
    payload: Dict[str, Any],
    locale: str,
) -> Dict[str, Any]:
    """
    把已校验的文本写入 ``localized_content/vision_analysis/<record_id>/<locale>``。

    只覆盖当前语言这一个叶子：其他语言、其他图片（record_id）、其他项目一律不受
    影响。写入前仍会再校验一次 payload，非法内容不落盘、不入缓存。
    """
    validated = vision_display.text_payload_for_store(
        payload,
        detection,
    )

    if not validated:

        return {
            "ok": False,
            "reason": "invalid_payload",
            "project": project,
        }

    record_id = vision_display.vision_record_id(
        project_id,
        detection.get("image_hash"),
    )

    input_hash = vision_display.vision_localization_input_hash(detection)

    if not record_id or not input_hash:

        return {
            "ok": False,
            "reason": "identity_unavailable",
            "project": project,
        }

    record = make_localized_record(
        CONTENT_TYPE_VISION,
        record_id,
        locale,
        validated,
        source=SOURCE_AI_GENERATED,
        input_hash=input_hash,
    )

    if not record:

        return {
            "ok": False,
            "reason": "record_rejected",
            "project": project,
        }

    updated = localized_content_put(
        project,
        record,
        copy_on_write=True,
    )

    remember_text(
        project_id,
        record_id,
        vision_display.resolve_content_locale(locale),
        input_hash,
        validated,
    )

    return {
        "ok": True,
        "reason": "",
        "project": updated,
        "record_id": record_id,
        "input_hash": input_hash,
    }


def vision_display_state(
    project: Dict[str, Any],
    project_id: str,
    detection: Dict[str, Any],
) -> Dict[str, Any]:
    """统一的 Vision 显示状态（available / stale / missing / empty / invalid / legacy）。"""
    return vision_display.get_vision_display(
        project,
        detection=detection,
        locale=get_content_locale(st.session_state),
        project_id=project_id,
        session_cache=text_cache(),
        allowed_objects=vision_allowed_objects(),
        allowed_categories=vision_allowed_categories(),
    )


# ============================================================
# PROJECT LINKAGE
# ============================================================

def update_project_risk_from_visual(
    project: Dict[str, Any],
    risks: List[Dict[str, Any]],
) -> Dict[str, Any]:

    """
    Save visual risk separately.

    IMPORTANT:
    Visual detection does NOT modify baseline_score.
    Baseline risk represents the project's original baseline
    and should remain stable.
    """

    if not risks:

        project[
            "visual_risk_level"
        ] = "LOW"

        project[
            "visual_risk_penalty"
        ] = 0

        project[
            "visual_risks"
        ] = []

        project[
            "visual_risk_resolved"
        ] = True

        return project


    # New risks detected → mark them as unresolved
    project[
        "visual_risk_resolved"
    ] = False

    overall_level = (
        get_overall_visual_level(
            risks
        )
    )
    project[
    "visual_risk_resolved"
    ] = False

    project[
        "visual_risk_level"
    ] = overall_level

    project[
        "visual_risk_penalty"
    ] = SEVERITY_PENALTY.get(
        overall_level,
        0,
    )

    project[
        "visual_risks"
    ] = risks

    project[
        "visual_risk_updated_at"
    ] = datetime.now().isoformat(
        timespec="minutes"
    )

    if "risk_categories" not in project:

        project[
            "risk_categories"
        ] = {}

    for risk in risks:

        category = risk.get(
            "risk_category",
            "其他",
        )

        project_category = (
            CATEGORY_TO_PROJECT_RISK.get(
                category,
                "其他",
            )
        )

        current_level = project[
            "risk_categories"
        ].get(
            project_category,
            "LOW",
        )

        new_level = str(
            risk.get(
                "severity",
                "LOW",
            )
        ).upper()

        if (
            SEVERITY_SCORE.get(
                new_level,
                1,
            )
            >
            SEVERITY_SCORE.get(
                current_level,
                1,
            )
        ):

            project[
                "risk_categories"
            ][
                project_category
            ] = new_level

    return project


# ============================================================
# RISK CHAIN
# ============================================================

def generate_risk_chain(
    risks: List[Dict[str, Any]],
) -> List[str]:

    chain_map = {

        "现场积水": [
            (
                "现场积水 → 地面湿滑 → "
                "人员滑倒风险增加 → "
                "可能造成施工安全事故"
            ),
            (
                "现场积水 → 临时用电环境恶化 → "
                "电气风险增加"
            ),
        ],

        "未佩戴安全帽": [
            (
                "未佩戴安全帽 → "
                "头部缺少防护 → "
                "高处坠物或碰撞时伤害风险增加"
            ),
        ],

        "未系安全带": [
            (
                "高处作业未系安全带 → "
                "失足后缺少防坠保护 → "
                "严重人员安全风险 → "
                "可能导致项目停工调查"
            ),
        ],

        "电线裸露": [
            (
                "电线裸露 → "
                "人员或设备接触 → "
                "触电风险 → "
                "人员安全与设备风险增加"
            ),
        ],

        "脚手架防护不足": [
            (
                "脚手架防护不足 → "
                "人员或物体坠落风险增加 → "
                "现场安全风险升级"
            ),
        ],

        "临边无防护": [
            (
                "临边无防护 → "
                "人员误入或失足 → "
                "高处坠落风险增加"
            ),
        ],

        "洞口无盖板": [
            (
                "洞口无盖板 → "
                "人员误踩或物体坠落 → "
                "施工安全风险增加"
            ),
        ],

        "易燃物堆放": [
            (
                "易燃材料管理不当 → "
                "遇到火源 → "
                "火灾风险增加 → "
                "可能影响人员安全和项目进度"
            ),
        ],

        "材料堆放混乱": [
            (
                "材料堆放混乱 → "
                "通行受阻或材料倾倒 → "
                "人员受伤风险增加"
            ),
        ],

        "机械设备附近人员过近": [
            (
                "人员进入机械作业范围 → "
                "设备运动或盲区风险 → "
                "人员安全风险增加"
            ),
        ],
    }

    chains = []

    for risk in risks:

        name = str(
            risk.get(
                "risk_name",
                "",
            )
        )

        if name in chain_map:

            localized = risk_chain_label(name)

            if localized:

                # Display-only: the ontology key stays unchanged and one
                # chain is emitted per line.
                chains.extend(
                    localized.split(
                        "\n"
                    )
                )

            else:

                chains.extend(
                    chain_map[
                        name
                    ]
                )

        elif name:

            generic = t(
                "ontology.chain.generic",
                name=risk_name_label(name),
            )

            if generic != "ontology.chain.generic":

                chains.append(generic)

            else:

                chains.append(
                    f"{name} → "
                    "施工安全条件恶化 → "
                    "现场事故概率增加 → "
                    "需要及时整改"
                )

    # Remove duplicates while preserving order.
    return list(
        dict.fromkeys(
            chains
        )
    )


# ============================================================
# RENDER RISK CARD
# ============================================================

def ontology_item(
    risk_name: str,
    category: str = "",
) -> Dict[str, Any]:
    """
    本体的确定性条目（规则 / 标准整改建议 / 默认等级）。

    只读：canonical detection 不复制这些文本，显示层用 locale key 现查现译，
    所以界面语言与识别事实互不影响。
    """
    if category and risk_name in RISK_ONTOLOGY.get(category, {}):

        return RISK_ONTOLOGY[category][risk_name]

    for items in RISK_ONTOLOGY.values():

        if risk_name in items:

            return items[risk_name]

    return {}


def render_risk_card(
    risk: Dict[str, Any],
    index: int,
    text: Dict[str, Any] = None,
):
    """
    渲染一条风险卡片。

    卡片分三层，顺序固定：
    1. canonical 识别事实（与语言无关）：本地化等级标签、confidence、penalty、
       位置、本体规则；
    2. 本体确定性标签（locale key 翻译）；
    3. AI 本地化文本（未生成 / 过期时该层为空，由区块状态提示统一说明）。
    """

    severity = str(
        risk.get(
            "severity",
            "LOW",
        )
    ).upper()

    icon = {
        "HIGH": "🔴",
        "MEDIUM": "🟠",
        "LOW": "🟢",
    }.get(
        severity,
        "⚪",
    )

    with st.container(
        border=True
    ):

        title_col, level_col = (
            st.columns(
                [
                    4,
                    1,
                ]
            )
        )

        with title_col:

            st.markdown(
                f"### {index}. "
                f"{risk_name_label(risk.get('risk_name', t('vision.unknown_risk')))}"
            )

            st.caption(
                risk_category_label(
                    risk.get(
                        "risk_category",
                        "",
                    )
                )
            )

        with level_col:

            st.markdown(
                f"### {icon} {risk_label(severity)}"
            )

        # ------------------------------------------------
        # 1. CANONICAL FACTS (language independent)
        # ------------------------------------------------

        facts = []

        confidence = risk.get(
            "confidence",
            None,
        )

        if isinstance(
            confidence,
            (int, float),
        ):

            facts.append(
                t(
                    "vision.fact_confidence",
                    value=f"{float(confidence):.2f}",
                )
            )

        penalty = risk.get(
            "penalty",
            None,
        )

        if isinstance(
            penalty,
            int,
        ):

            facts.append(
                t(
                    "vision.fact_penalty",
                    value=penalty,
                )
            )

        location = risk.get(
            "location",
            [],
        )

        if isinstance(
            location,
            (list, tuple),
        ) and len(location) == 4:

            facts.append(
                t(
                    "vision.fact_location",
                    value=", ".join(
                        f"{float(value):.2f}"
                        for value in location
                    ),
                )
            )

        else:

            facts.append(
                t("vision.fact_location_unknown")
            )

        st.caption(
            " · ".join(facts)
        )

        # ------------------------------------------------
        # 2. DETERMINISTIC ONTOLOGY LABELS
        # ------------------------------------------------

        item = ontology_item(
            risk.get("risk_name", ""),
            risk.get("risk_category", ""),
        )

        rule = item.get(
            "rule",
            "",
        )

        if rule:

            st.markdown(
                t("vision.card_rule")
            )

            st.write(
                risk_rule_label(
                    risk.get("risk_name", ""),
                    rule,
                )
            )

        suggestion = item.get(
            "suggestion",
            "",
        )

        if suggestion:

            st.markdown(
                t("vision.card_action")
            )

            st.write(
                risk_suggestion_label(
                    risk.get("risk_name", ""),
                    suggestion,
                )
            )

        # ------------------------------------------------
        # 3. AI LOCALIZED TEXT (current content language only)
        # ------------------------------------------------

        if not isinstance(
            text,
            dict,
        ):

            return

        title = text.get(
            "title",
            "",
        )

        if title:

            st.markdown(
                t("vision.card_ai_title")
            )

            st.write(
                title
            )

        reason = text.get(
            "reason",
            "",
        )

        if reason:

            st.markdown(
                t("vision.card_ai_reason")
            )

            st.write(
                reason
            )

        recommendation = text.get(
            "recommendation",
            "",
        )

        if recommendation:

            st.markdown(
                t("vision.card_ai_recommendation")
            )

            st.write(
                recommendation
            )


# ============================================================
# PROJECT SELECTOR
# ============================================================

def get_available_projects():

    projects = st.session_state.get(
        "projects",
        {},
    )

    if not isinstance(
        projects,
        dict,
    ):

        return {}

    return {
        name: project
        for name, project in projects.items()
        if not bool(
            project.get(
                "archived",
                False,
            )
        )
    }


# ============================================================
# STREAMLIT PAGE
# ============================================================

def upload_error_label(code: str) -> str:
    """上传校验问题码的本地化文案（未知码原样显示，便于排查）。"""
    label = t(f"vision.upload_error_{code}")

    if label == f"vision.upload_error_{code}":

        return str(code)

    return label


def render_vision_text_state(
    state: Dict[str, Any],
) -> None:
    """
    渲染"AI 文本（当前语言）"区块。

    只有当前内容语言的版本会被当作当前分析；过期版本只能进只读折叠区；
    其他语言的版本永远不会被顶替显示。
    """
    ui_state = state.get("ui_state")
    language = locale_display_name(state.get("locale"))

    if ui_state == vision_display.DISPLAY_AVAILABLE:

        st.success(
            t("vision.state_available", language=language)
        )

        if state.get("source") == "deterministic":

            st.caption(t("vision.caption_deterministic_source"))

        display = state.get("display") or {}
        summary = display.get("summary")

        if summary:

            with st.container(border=True):

                st.markdown(summary)

        return

    if ui_state == vision_display.DISPLAY_LEGACY_COMPATIBLE:

        st.info(
            t("vision.state_legacy", language=language)
        )

        legacy_text = state.get("legacy_text")

        if legacy_text:

            with st.container(border=True):

                st.markdown(legacy_text)

        st.caption(t("vision.caption_legacy_readonly"))

        return

    if ui_state == vision_display.DISPLAY_STALE:

        st.warning(
            t("vision.state_stale", language=language)
        )

        stale_display = state.get("stale_display") or {}
        stale_text = state.get("stale_text") or {}

        stale_findings = stale_display.get("findings") or []
        stale_blocks = stale_text.get("findings") or []

        if stale_display or stale_text:

            with st.expander(t("vision.stale_expander")):

                st.caption(t("vision.caption_stale_readonly"))

                if stale_display.get("summary") or stale_text.get("summary"):

                    st.markdown(
                        stale_display.get("summary")
                        or stale_text.get("summary")
                    )

                for index, finding in enumerate(
                    stale_findings or stale_blocks,
                    start=1,
                ):

                    st.markdown(
                        f"**{index}.** "
                        f"{finding.get('title') or ''}"
                    )

                    for field in (
                        "reason",
                        "recommendation",
                    ):

                        if finding.get(field):

                            st.write(finding[field])

        return

    if ui_state == vision_display.DISPLAY_INVALID:

        st.error(t("vision.state_invalid"))

        code = str(state.get("reason") or "")

        if code:

            st.caption(t("vision.error_code", code=code))

        return

    # No version for the current language yet.
    if ui_state == vision_display.DISPLAY_MISSING:

        st.info(
            t(
                "vision.state_missing",
                language=language,
                saved=", ".join(
                    locale_display_name(code)
                    for code in state.get("saved_locales") or []
                ),
            )
        )

    else:

        st.info(
            t("vision.state_no_text")
        )

    st.caption(t("vision.caption_generate_hint"))


def vision_risk_page(
    persist=None,
):
    """
    Vision Risk 页面（阶段 4B）。

    分工：

    * 识别事实（图片 hash、finding、等级、penalty、confidence、location）由视觉
      模型产生并且**与语言无关**，只在上传新图片并点击"识别"时更新一次；
    * AI 文本（title / reason / recommendation / summary）由**文本**模型按当前内容
      语言单独生成，只写入当前语言那一个叶子；
    * 切换界面语言只是重新读取当前语言的版本，绝不重新上传图片、绝不重新调用视觉
      模型、绝不改变任何识别结果。

    ``persist`` 为可选的落盘回调（app.py 传入 ``persist_project``）；本函数自己不
    直接操作磁盘。
    """
    st.title(
        t("vision.title")
    )

    st.caption(
        t("vision.caption")
    )

    st.markdown("---")

    # --------------------------------------------------------
    # PROJECT
    # --------------------------------------------------------

    projects = get_available_projects()

    if not projects:

        st.info(
            t("vision.no_active_project")
        )

        return

    project_names = list(
        projects.keys()
    )

    current_selected = st.session_state.get(
        "selected_project"
    )

    if current_selected in project_names:

        default_index = project_names.index(
            current_selected
        )

    else:

        default_index = 0

    selected_project_name = st.selectbox(
        t("vision.link_project"),
        project_names,
        index=default_index,
        key="vision_project_selector",
    )

    st.session_state[
        "selected_project"
    ] = selected_project_name

    if selected_project_name not in projects:

        st.info(
            t("vision.no_active_project")
        )

        return

    project = projects[
        selected_project_name
    ]

    project_id = selected_project_name

    locale = vision_display.resolve_content_locale(
        get_content_locale(st.session_state)
    )

    # --------------------------------------------------------
    # UPLOAD
    # --------------------------------------------------------

    uploaded_file = st.file_uploader(
        t("vision.upload"),
        type=[
            "jpg",
            "jpeg",
            "png",
            "bmp",
        ],
        key="vision_image_uploader",
    )

    if uploaded_file is None:

        st.info(
            t("vision.upload_hint")
        )

        return

    image_bytes = uploaded_file.getvalue()

    upload_problems = (
        vision_display.validate_image_upload(
            getattr(uploaded_file, "name", ""),
            getattr(uploaded_file, "type", ""),
            (
                len(image_bytes)
                if isinstance(
                    image_bytes,
                    (bytes, bytearray),
                )
                else None
            ),
            (
                image_bytes[:16]
                if isinstance(
                    image_bytes,
                    (bytes, bytearray),
                )
                else None
            ),
        )
    )

    if upload_problems:

        st.error(
            upload_error_label(upload_problems[0])
        )

        return

    try:

        image = Image.open(
            BytesIO(image_bytes)
        ).convert(
            "RGB"
        )

    except Exception:

        st.error(
            t("vision.error_unreadable_short")
        )

        return

    image_hash = vision_display.compute_image_hash(
        image_bytes
    )

    if not image_hash:

        st.error(
            t("vision.error_image_hash")
        )

        return

    st.image(
        image,
        caption=t("vision.image_caption"),
        use_container_width=True,
    )

    # --------------------------------------------------------
    # CURRENT DETECTION (facts are reused, never re-detected)
    # --------------------------------------------------------

    detection = detection_for_image(
        project,
        project_id,
        image_hash,
    )

    state = vision_display_state(
        project,
        project_id,
        detection,
    )

    plan = vision_display.vision_generation_plan(
        state
    )

    # --------------------------------------------------------
    # ANALYZE BUTTON (the only path that calls the vision model)
    # --------------------------------------------------------

    analyze_key = vision_display.vision_action_key(
        project_id,
        image_hash,
        locale,
        vision_display.OPERATION_ANALYZE,
    )

    analyze_clicked = st.button(
        t("vision.button_analyze"),
        type="primary",
        use_container_width=True,
        key=analyze_key or "vision_analyze",
    )

    if analyze_clicked:

        with st.spinner(
            t("vision.spinner_analyzing")
        ):

            raw_result = call_deepseek_vision(
                image,
                build_vision_prompt(),
            )
            

        new_detection = build_vision_detection(
            raw_result,
            image_hash,
        )

        problems = vision_display.validate_vision_detection(
            new_detection,
            vision_allowed_objects(),
            vision_allowed_categories(),
        )

        if raw_result is None:

            st.error(
                t("vision.error_vision_failed")
            )

        elif problems:

            st.error(
                t("vision.error_invalid_detection")
            )

            st.caption(
                t("vision.error_code", code=problems[0])
            )

        else:

            project[
                DETECTION_FIELD
            ] = new_detection

            update_project_risk_from_visual(
                project,
                detection_findings_for_project(
                    new_detection
                ),
            )

            st.session_state.projects[
                selected_project_name
            ] = project

            remember_detection(
                project_id,
                new_detection,
            )

            detection = new_detection

            state = vision_display_state(
                project,
                project_id,
                detection,
            )

            # The same click may also write the current language version:
            # at most one text call, and only when the detection has findings
            # and the current language has no usable version yet (brand new ->
            # empty, written in another language -> missing, outdated ->
            # stale, legacy singleton -> legacy_compatible). An ``available``
            # version is never rewritten.
            if (
                new_detection.get("findings")
                and vision_display.vision_text_needs_generation(
                    state
                )
            ):

                with st.spinner(
                    t("vision.spinner_generating_text")
                ):

                    text_result = ai_generate_vision_text(
                        new_detection,
                        project_id,
                        locale,
                    )

                if text_result.get("success"):

                    stored = store_vision_text(
                        project,
                        project_id,
                        new_detection,
                        text_result.get("payload"),
                        locale,
                    )

                    if stored.get("ok"):

                        project = stored["project"]

                        st.session_state.projects[
                            selected_project_name
                        ] = project

                        state = vision_display_state(
                            project,
                            project_id,
                            detection,
                        )

                        st.success(
                            t("vision.success_text_generated")
                        )

                    else:

                        st.warning(
                            t("vision.warn_text_failed")
                        )

                else:

                    st.warning(
                        t("vision.warn_text_failed")
                    )

            if callable(persist):

                persist()

    # --------------------------------------------------------
    # GENERATE TEXT BUTTON (text model only)
    # --------------------------------------------------------

    state = vision_display_state(
        project,
        project_id,
        detection,
    )

    plan = vision_display.vision_generation_plan(
        state
    )

    if detection and plan.get("can_generate_text"):

        generate_key = vision_display.vision_action_key(
            project_id,
            image_hash,
            locale,
            vision_display.OPERATION_GENERATE_TEXT,
            state.get("record_id"),
        )

        generate_clicked = st.button(
            t("vision.button_generate_text"),
            use_container_width=True,
            key=generate_key or "vision_generate_text",
        )

        if generate_clicked:

            with st.spinner(
                t("vision.spinner_generating_text")
            ):

                text_result = ai_generate_vision_text(
                    detection,
                    project_id,
                    locale,
                )

            if text_result.get("success"):

                stored = store_vision_text(
                    project,
                    project_id,
                    detection,
                    text_result.get("payload"),
                    locale,
                )

                if stored.get("ok"):

                    project = stored["project"]

                    st.session_state.projects[
                        selected_project_name
                    ] = project

                    st.success(
                        t("vision.success_text_generated")
                    )

                    if callable(persist):

                        persist()

                else:

                    st.warning(
                        t("vision.warn_text_failed")
                    )

            else:

                st.warning(
                    t("vision.warn_text_failed")
                )

            state = vision_display_state(
                project,
                project_id,
                detection,
            )

    st.markdown("---")

    # --------------------------------------------------------
    # NO DETECTION YET
    # --------------------------------------------------------

    if not detection:

        st.info(
            t("vision.state_empty")
        )

        st.caption(
            t("vision.caption_ai_limits")
        )

        return

    if state.get("ui_state") == vision_display.DISPLAY_INVALID:

        st.error(
            t("vision.state_invalid")
        )

        code = str(state.get("reason") or "")

        if code:

            st.caption(
                t("vision.error_code", code=code)
            )

        return

    # --------------------------------------------------------
    # CANONICAL FACTS (language independent)
    # --------------------------------------------------------

    facts = detection_findings_for_project(
        state.get("detection") or detection
    )

    if not facts:

        st.success(
            t("vision.success_no_risk")
        )

        st.caption(
            t("vision.caption_ai_limits")
        )

        return

    counts = state.get("level_counts") or {}

    c1, c2, c3, c4, c5 = st.columns(
        5
    )

    with c1:

        st.metric(
            t("vision.metric_detected"),
            len(facts),
        )

    with c2:

        st.metric(
            t("vision.metric_high"),
            counts.get("HIGH", 0),
        )

    with c3:

        st.metric(
            t("vision.metric_medium"),
            counts.get("MEDIUM", 0),
        )

    with c4:

        st.metric(
            t("vision.metric_overall"),
            risk_label(state.get("overall_level") or "LOW"),
        )

    with c5:

        st.metric(
            t("vision.metric_penalty"),
            state.get("total_penalty") or 0,
        )

    st.caption(
        t("vision.caption_facts")
    )

    # --------------------------------------------------------
    # AI TEXT (current content language only)
    # --------------------------------------------------------

    st.markdown("---")

    st.subheader(
        t("vision.subheader_text")
    )

    render_vision_text_state(
        state
    )

    # --------------------------------------------------------
    # ANNOTATED IMAGE
    # --------------------------------------------------------

    st.markdown("---")

    st.subheader(
        t("vision.subheader_result")
    )

    annotated_image = draw_risk_boxes(
        image,
        facts,
    )

    st.image(
        annotated_image,
        caption=t("vision.caption_annotations"),
        use_container_width=True,
    )

    st.caption(
        t("vision.caption_annotation_note")
    )

    # --------------------------------------------------------
    # RISKS
    # --------------------------------------------------------

    st.markdown("---")

    st.subheader(
        t("vision.subheader_risks")
    )

    text_findings = {
        finding.get("finding_id"): finding
        for finding in (
            (state.get("display") or {}).get("findings") or []
        )
    }

    for index, finding in enumerate(
        facts,
        start=1,
    ):

        render_risk_card(
            finding,
            index,
            text=text_findings.get(finding.get("finding_id")),
        )

    # --------------------------------------------------------
    # RISK CHAINS
    # --------------------------------------------------------

    st.markdown("---")

    st.subheader(
        t("vision.subheader_chains")
    )

    chains = generate_risk_chain(
        facts
    )

    for chain in chains:

        st.info(
            chain
        )

    # --------------------------------------------------------
    # PROJECT INTEGRATION
    # --------------------------------------------------------

    st.markdown("---")

    st.subheader(
        t("vision.subheader_integration")
    )

    visual_level = project.get(
        "visual_risk_level",
        "LOW",
    )

    penalty = project.get(
        "visual_risk_penalty",
        0,
    )

    baseline = project.get(
        "baseline_score",
        0,
    )

    col1, col2, col3 = st.columns(
        3
    )

    with col1:

        st.metric(
            t("vision.metric_visual_risk"),
            risk_label(visual_level),
        )

    with col2:

        st.metric(
            t("vision.metric_visual_signal"),
            f"+{penalty}",
        )

    with col3:

        st.metric(
            t("vision.metric_baseline_risk"),
            baseline,
        )

    st.success(
        t(
            "vision.success_linked",
            name=selected_project_name,
        )
    )

    st.caption(
        t("vision.caption_linked_note")
    )

    # --------------------------------------------------------
    # AI DISCLAIMER
    # --------------------------------------------------------

    st.markdown("---")

    st.caption(
        t("vision.caption_disclaimer")
    )


# ============================================================
# DIRECT RUN
# ============================================================

if __name__ == "__main__":
    vision_risk_page()