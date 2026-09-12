import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


class SafetyAgent:

    def analyze(self, text):

        prompt = f"""
你是一名建筑施工安全风险专家。

你的唯一任务是分析施工日志中的【安全风险】。

重点关注：

1. 人员安全
2. 机械设备安全
3. 高处作业
4. 临时用电
5. 火灾风险
6. 极端天气
7. 施工现场危险行为
8. 其他可能造成人身伤害的因素

请严格返回 JSON：

{{
    "score": 0,
    "level": "低",
    "risks": [
        {{
            "title": "风险名称",
            "reason": "风险原因",
            "suggestion": "具体整改建议"
        }}
    ],
    "summary": "安全风险总结"
}}

要求：

- score 为 0-100 的整数
- level 只能是：低、中、高
- 不要编造日志中不存在的信息
- 如果没有明显安全风险，risks 返回空数组
- 整改建议必须具有可执行性
- 只返回 JSON，不要 Markdown

施工日志：

{text}
"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是建筑施工安全风险分析专家，只输出合法JSON。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2
        )

        result = response.choices[0].message.content

        result = result.replace(
            "```json", ""
        ).replace(
            "```", ""
        ).strip()

        import json

        return json.loads(result)