import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


class ManagerAgent:

    def analyze(
        self,
        safety_result,
        schedule_result,
        quality_result
    ):

        prompt = f"""
你是一名建筑项目风险管理总负责人。

现在你已经收到三个专业AI Agent的分析结果：

【安全风险分析】
{safety_result}

【工期风险分析】
{schedule_result}

【质量风险分析】
{quality_result}

你的任务不是重新分析施工日志，而是：

1. 综合三个专业Agent的分析结果
2. 识别重复出现的风险
3. 判断哪些风险最需要优先处理
4. 综合计算项目总体风险
5. 给项目管理人员提出可执行的行动建议

请特别注意：

- 不要简单平均三个Agent的分数
- 安全风险涉及人员生命安全时，应提高优先级
- 多个Agent同时发现的风险，应提高优先级
- 不要制造专家分析中不存在的新事实
- 区分“已经发生的问题”和“潜在风险”
- 建议必须具体、可执行

请严格返回以下JSON：

{{
    "overall_score": 0,
    "overall_level": "低",
    "priority": "最需要优先处理的风险",
    "reason": "为什么这个风险最需要优先处理",
    "actions": [
        "第一项行动建议",
        "第二项行动建议",
        "第三项行动建议"
    ],
    "summary": "项目总体风险总结"
}}

要求：

- overall_score 为 0-100 的整数
- overall_level 只能是：低、中、高
- priority 必须是具体风险
- actions 必须是项目管理人员可以实际执行的措施
- 只返回JSON
- 不要输出Markdown

三个Agent的分析结果：

安全：
{safety_result}

工期：
{schedule_result}

质量：
{quality_result}
"""

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是建筑项目风险管理总负责人，只输出合法JSON。"
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