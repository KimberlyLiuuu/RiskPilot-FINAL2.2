import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


class ScheduleAgent:

    def analyze(self, text):

        prompt = f"""
你是一名资深建筑工程进度管理专家。

你的唯一任务是分析施工日志中的【工期和进度风险】。

重点关注：

1. 材料延期
2. 人员不足
3. 设备故障
4. 恶劣天气
5. 关键工序延期
6. 施工计划偏差
7. 前置工序延误
8. 可能造成的整体工期影响

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
    "summary": "工期风险总结"
}}

要求：

- score 为 0-100 的整数
- level 只能是：低、中、高
- 不要编造施工日志中不存在的信息
- 如果没有明显工期风险，risks 返回空数组
- 整改建议必须具有可执行性
- 重点分析这些问题可能对施工进度造成的影响
- 只返回 JSON，不要 Markdown

施工日志：

{text}
"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是建筑工程进度管理专家，只输出合法JSON。"
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