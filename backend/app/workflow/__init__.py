"""workflow 包：Agent 编排。

- `engine.py`：手写状态机（legacy 基线，行为不变）；
- `graph.py` + `nodes.py` + `state.py` + `steps.py`：LangGraph 1.2 图编排；
- 两者共用 `agents/*` 与 `services/*`，由 `WORKFLOW_ENGINE` 开关分派。
"""

from .engine import TOTAL_STEPS, launch_workflow, run_analysis, run_workflow

__all__ = ["TOTAL_STEPS", "launch_workflow", "run_analysis", "run_workflow"]
