"""Service that consolidates task results into the final report."""

from __future__ import annotations

import json

from hello_agents import ToolAwareSimpleAgent

try:
    from ..config import Configuration
    from ..models import SummaryState
    from ..utils import strip_thinking_tokens
    from .text_processing import strip_tool_calls
except ImportError:  # pragma: no cover - script-mode fallback
    from config import Configuration
    from models import SummaryState
    from services.text_processing import strip_tool_calls
    from utils import strip_thinking_tokens


class ReportingService:
    """Generates the final structured report."""

    def __init__(self, report_agent: ToolAwareSimpleAgent, config: Configuration) -> None:
        self._agent = report_agent
        self._config = config

    def generate_report(self, state: SummaryState) -> str:
        """Generate a structured report based on completed tasks."""

        tasks_block = []
        for task in state.todo_items:
            summary_block = task.summary or "暂无可用信息"
            sources_block = task.sources_summary or "暂无来源"
            tasks_block.append(
                f"### 任务 {task.id}: {task.title}\n"
                f"- 任务意图：{task.intent}\n"
                f"- 搜索查询：{task.query}\n"
                f"- 执行状态：{task.status}\n"
                f"- 任务总结：\n{summary_block}\n"
                f"- 来源概览：\n{sources_block}\n"
            )

        note_references = []
        for task in state.todo_items:
            if task.note_id:
                note_references.append(
                    f"- 任务 {task.id}《{task.title}》：note_id={task.note_id}"
                )

        notes_section = "\n".join(note_references) if note_references else "- 暂无可用任务笔记"
        read_template = json.dumps({"action": "read", "note_id": "<note_id>"}, ensure_ascii=False)

        prompt = (
            f"研究主题：{state.research_topic}\n"
            f"任务概览：\n{''.join(tasks_block)}\n"
            f"可用任务笔记：\n{notes_section}\n"
            f"如需读取任务笔记，请使用 [TOOL_CALL:note:{read_template}]。\n"
            "请基于任务总结与任务笔记生成一份 Markdown 研究报告，至少包含：\n"
            "1. 背景概览\n"
            "2. 核心洞见\n"
            "3. 证据与数据\n"
            "4. 风险与挑战\n"
            "5. 参考来源\n"
            "不要输出任何 [TOOL_CALL:...] 指令。最终报告的持久化由系统代码负责。"
        )

        response = self._agent.run(prompt)
        self._agent.clear_history()

        report_text = response.strip()
        if self._config.strip_thinking_tokens:
            report_text = strip_thinking_tokens(report_text)

        report_text = strip_tool_calls(report_text).strip()
        return report_text or "报告生成失败，请检查输入。"
