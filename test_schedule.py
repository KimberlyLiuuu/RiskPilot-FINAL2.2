"""工期风险分析演示脚本（默认不调用网络或真实 AI）。"""
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

from agents.schedule_agent import ScheduleAgent


text = """
项目：幸福家园住宅项目

今日施工情况：

原计划今日到场的钢筋材料因供应商运输问题延期2天。

下午持续暴雨，混凝土浇筑工作暂停3小时。

现场2名施工人员请假，导致部分工序人员不足。

下午塔吊出现故障，维修30分钟后恢复正常。

今日没有人员伤亡事故。
"""


agent = ScheduleAgent()

result = agent.analyze(text)

print(result)