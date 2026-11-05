"""EA Stack · 探针包：L2 后端 / L4 异步规划 / 端点 / 指标。

对外只暴露一个入口：`python -m sim.test_vla_l4`。
这里的模块不参与主闭环（sim.main），也不被 CI 的 sim job 导入，
除非显式跑探针——主闭环不依赖 requests。
"""

__all__ = [
    "base", "endpoints", "fake_ollama", "l4_async", "metrics",
    "ollama_vlm", "plan_schema", "runner", "scripted",
]
