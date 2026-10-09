"""agents 包：8 个工作流节点。"""

from .ats_agent import AtsAgent
from .impact_agent import ImpactAgent
from .jd_match_agent import JdMatchAgent
from .parse_agent import ParseAgent
from .qc_agent import QcAgent
from .report_agent import ReportAgent
from .risk_agent import RiskAgent
from .suggestion_agent import SuggestionAgent

# 顺序即契约 §4.2 的 step_name 顺序，不要随意调整
AGENT_PIPELINE = [
    ParseAgent,
    JdMatchAgent,
    AtsAgent,
    ImpactAgent,
    RiskAgent,
    SuggestionAgent,
    ReportAgent,
    QcAgent,
]

__all__ = [
    "AGENT_PIPELINE",
    "AtsAgent",
    "ImpactAgent",
    "JdMatchAgent",
    "ParseAgent",
    "QcAgent",
    "ReportAgent",
    "RiskAgent",
    "SuggestionAgent",
]
