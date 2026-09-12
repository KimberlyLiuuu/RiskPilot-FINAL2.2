"""DeepSeek API 连通性演示脚本（默认不调用网络或真实 AI）。"""
import os
import sys


def _require_live_ai_authorization():
    """仅在显式设置 RUN_LIVE_AI_DEMOS=1 时才允许调用真实 DeepSeek。

    未授权时打印清晰的跳过提示并以退出码 0 结束；
    已授权但缺少 API key 时安全失败，绝不发起任何网络请求。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    if os.getenv("RUN_LIVE_AI_DEMOS", "").strip() != "1":
        print(
            "[SKIP] 已跳过真实 AI 演示：默认不调用网络或 AI。\n"
            "       如需运行，请显式设置环境变量 RUN_LIVE_AI_DEMOS=1。"
        )
        sys.exit(0)

    if not os.getenv("DEEPSEEK_API_KEY"):
        print(
            "[ERROR] RUN_LIVE_AI_DEMOS=1 已授权，但未检测到环境变量 "
            "DEEPSEEK_API_KEY，演示安全终止（未发起任何网络 / AI 调用）。"
        )
        sys.exit(1)


_require_live_ai_authorization()

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {
            "role": "user",
            "content": "请只回复：API连接成功！"
        }
    ]
)

print(response.choices[0].message.content)