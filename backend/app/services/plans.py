"""套餐定义（对应 docs/api-contract.md §6.1 与 aaa.md §六.5 定价设计）。

套餐是代码常量而非数据库表：价格调整属于发版行为，避免运行期数据漂移。
"""

from __future__ import annotations

PLANS: list[dict] = [
    {
        "code": "free",
        "name": "免费体验",
        "price": 0.0,
        "currency": "CNY",
        "period": "once",
        "quota": 3,
        "features": ["3 次基础诊断", "总分与维度分", "红黄绿风险标注"],
    },
    {
        "code": "single_basic",
        "name": "单份基础报告",
        "price": 9.9,
        "currency": "CNY",
        "period": "once",
        "quota": 1,
        "features": ["单份完整报告", "关键词覆盖分析", "PDF 导出"],
    },
    {
        "code": "single_deep",
        "name": "单份深度报告",
        "price": 19.9,
        "currency": "CNY",
        "period": "once",
        "quota": 1,
        "features": ["完整维度评分", "逐条改写建议", "证据链溯源", "PDF 导出"],
    },
    {
        "code": "monthly_basic",
        "name": "月度会员",
        "price": 39.0,
        "currency": "CNY",
        "period": "month",
        "quota": -1,
        "features": ["不限次诊断", "历史报告对比", "二次诊断追踪"],
    },
    {
        "code": "monthly_pro",
        "name": "月度专业版",
        "price": 99.0,
        "currency": "CNY",
        "period": "month",
        "quota": -1,
        "features": ["不限次诊断", "历史对比", "优先队列", "岗位定向知识库"],
    },
    {
        "code": "hr_basic",
        "name": "HR 基础版",
        "price": 999.0,
        "currency": "CNY",
        "period": "month",
        "quota": 100,
        "features": ["100 份/月", "批量排序", "CSV / PDF 导出"],
    },
    {
        "code": "hr_pro",
        "name": "HR 专业版",
        "price": 2999.0,
        "currency": "CNY",
        "period": "month",
        "quota": 500,
        "features": ["500 份/月", "批量排序", "开放 API", "定制知识库"],
    },
]

PLAN_MAP: dict[str, dict] = {p["code"]: p for p in PLANS}


def get_plan(code: str) -> dict | None:
    return PLAN_MAP.get(code)
