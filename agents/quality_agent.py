import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


class QualityAgent:

    def analyze(self, text):

        prompt = f"""
你是一名资深建筑工程质量管理专家。

你的唯一任务是分析施工日志中的【工程质量风险】。

重点关注：

1. 混凝土施工
2. 钢筋工程
3. 材料质量
4. 施工工艺
5. 工序衔接
6. 质量检查
7. 施工环境对质量的影响
8. 设备状态对施工质量的影响
9. 可能导致返工或质量缺陷的问题

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
    "summary": "工程质量风险总结"
}}

要求：

- score 为 0-100 的整数
- level 只能是：低、中、高
- 不要编造施工日志中不存在的信息
- 如果没有明显质量风险，risks 返回空数组
- 必须区分“已经发生的质量问题”和“可能产生的质量风险”
- 整改建议必须具有可执行性
- 不要直接认定工程已经出现质量缺陷，除非日志中明确说明
- 只返回 JSON，不要 Markdown

施工日志：

{text}
"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是建筑工程质量管理专家，只输出合法JSON。"
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

        return json.loads(result)