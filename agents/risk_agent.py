import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


class RiskAgent:

    # ========================================================
    # DAILY LOG RISK ANALYSIS
    # ========================================================

    def analyze(self, text):

        prompt = f"""
你是一名资深建筑工程项目风险管理专家。

请分析下面的施工日志。

你必须返回一个 JSON 对象，不要输出 Markdown，不要输出解释文字。

JSON 格式必须严格按照下面结构：

{{
    "overall_score": 0,
    "overall_level": "低风险",
    "safety_score": 0,
    "schedule_score": 0,
    "quality_score": 0,
    "cost_score": 0,
    "risks": [
        {{
            "category": "安全",
            "title": "风险名称",
            "level": "高",
            "reason": "风险原因",
            "suggestion": "整改建议"
        }}
    ],
    "summary": "对当前项目风险情况进行简短总结"
}}

要求：

1. overall_score 为 0-100 的整数。
2. 四项风险评分均为 0-100。
3. 风险等级只能是：
   - 低风险
   - 中风险
   - 高风险
4. category 只能使用：
   - 安全
   - 工期
   - 质量
   - 成本
   - 材料
   - 设备
5. level 只能是：
   - 低
   - 中
   - 高
6. 不要凭空制造施工日志中不存在的事实。
7. 如果日志中没有某类明显风险，可以给较低评分。
8. suggestion 必须是具体、可执行的建议。

施工日志：

{text}
"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是建筑工程风险管理AI，"
                        "只输出合法JSON。"
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2
        )

        result = response.choices[0].message.content

        result = (
            result
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        return json.loads(result)

    # ========================================================
    # CURRENT RISK EXPLANATION
    # ========================================================

    def explain_current_risk(self, prompt, timeout=60):
        """
        Explain the existing Current Risk assessment.

        Unlike analyze(), this method does not build the prompt itself. The
        caller (utils.current_risk_display.build_current_risk_text_prompt)
        owns the prompt, including the requested target language, so the model
        can answer in the user's content locale instead of always English.

        The response is still raw JSON: the caller must validate it with
        validate_current_risk_text_payload() before anything is stored.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Current Risk prompt must be a non-empty string")

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RiskPilot's construction risk "
                        "intelligence assistant. "
                        "Return only valid JSON."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            timeout=timeout,
        )

        result = response.choices[0].message.content

        result = (
            result
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        return json.loads(result)

    # ========================================================
    # VISION ANALYSIS TEXT (stage 4B)
    # ========================================================

    def explain_vision_text(self, prompt, timeout=90):
        """
        Write the localized text of one Vision Risk detection.

        This is the **text** step only: the prompt already carries the
        canonical, language independent detection, so the model never sees the
        photo and can never change a recognition fact. The response is raw
        JSON and must be validated by
        ``utils.vision_display.validate_vision_text_payload`` before the
        caller stores it.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Vision text prompt must be a non-empty string")

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RiskPilot's construction risk "
                        "intelligence assistant. "
                        "Return only valid JSON."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            timeout=timeout,
        )

        result = response.choices[0].message.content

        result = (
            result
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        return json.loads(result)

    # ========================================================
    # RISK REPORT GENERATION
    # ========================================================

    def generate_report(self, prompt, timeout=90):
        """
        Generate a RiskPilot risk communication report.

        Unlike analyze(), this method does not wrap the request in the
        daily-log prompt. The Risk Report Center already builds the full
        prompt, including the requested report language.
        """

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RiskPilot's construction risk "
                        "intelligence assistant. "
                        "Return only valid JSON."
                    )
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            timeout=timeout,
        )

        result = response.choices[0].message.content

        result = (
            result
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        return json.loads(result)