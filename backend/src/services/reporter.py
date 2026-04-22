"""Service that consolidates task results into the final report."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from hello_agents import ToolAwareSimpleAgent

try:
    from ..config import Configuration
    from ..models import SummaryState
    from ..prompts import report_outline_instructions
    from ..utils import strip_thinking_tokens
    from .text_processing import strip_tool_calls
except ImportError:  # pragma: no cover - script-mode fallback
    from config import Configuration
    from models import SummaryState
    from prompts import report_outline_instructions
    from services.text_processing import strip_tool_calls
    from utils import strip_thinking_tokens


logger = logging.getLogger(__name__)

OUTLINE_MAX_TASKS = 6
OUTLINE_FINDING_LIMIT = 220
REPORT_MAX_TASKS = 6
REPORT_SUMMARY_LIMIT = 900
REPORT_SOURCE_LIMIT = 650
REPORT_POINT_LIMIT = 240


class ReportingService:
    """Generates the final structured report."""

    def __init__(self, report_agent: ToolAwareSimpleAgent, config: Configuration) -> None:
        self._agent = report_agent
        self._config = config

    def build_report_outline(
        self,
        state: SummaryState,
        *,
        task_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build a structured report outline before writing the final markdown."""

        normalized_tasks = self._normalize_tasks(state, task_results)
        if not normalized_tasks:
            return self._fallback_outline(state, normalized_tasks)

        prompt = (
            f"研究主题：{state.research_topic}\n"
            f"任务材料：\n{self._render_task_digest(normalized_tasks)}\n\n"
            f"{report_outline_instructions.strip()}"
        )

        try:
            response = self._agent.run(prompt)
        except Exception as exc:  # pragma: no cover - exercised through integration/fallback paths
            logger.warning("Report outline generation failed; using fallback outline: %s", exc)
            return self._fallback_outline(state, normalized_tasks)
        finally:
            self._safe_clear_history()

        outline = self._parse_outline_response(response)
        if outline:
            return outline
        return self._fallback_outline(state, normalized_tasks)

    def generate_report(
        self,
        state: SummaryState,
        *,
        report_outline: dict[str, Any] | None = None,
        task_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a structured report based on completed tasks."""

        normalized_tasks = self._normalize_tasks(state, task_results)
        outline = report_outline or self._fallback_outline(state, normalized_tasks)
        comparison_table = self._build_comparison_table(normalized_tasks)
        tasks_block = self._render_task_dossier(normalized_tasks)

        prompt = (
            f"研究主题：{state.research_topic}\n\n"
            f"报告提纲（JSON）：\n{json.dumps(outline, ensure_ascii=False, indent=2)}\n\n"
            f"候选对比表（请在正文中保留并完善，除非信息明显不足）：\n{comparison_table}\n\n"
            f"任务材料：\n{tasks_block}\n\n"
            "请输出一份完整 Markdown 研究报告，并严格满足这些要求：\n"
            "1. 一级章节必须依次为：背景概览、核心洞见、证据与数据、风险与挑战、参考来源。\n"
            "2. “核心洞见”至少写 4 条，每条都要给出判断理由，并绑定任务编号或来源线索。\n"
            "3. “证据与数据”必须按提纲中的 comparison_dimensions 展开，每个维度都要写成独立小节。\n"
            "4. 如果主题是比较/评估/选型，必须包含至少一个 Markdown 对比表；优先使用上面的候选对比表。\n"
            "5. 不能只把任务摘要重写一遍，而要做综合判断：谁领先、领先在哪、代价是什么、还有哪些不确定性。\n"
            "6. 如果材料足够，请写得充分一些，避免退化成短摘要。\n"
            "7. 参考来源按任务分组列出标题或链接线索，保证可追溯。\n"
            "8. 禁止输出任何 [TOOL_CALL:...] 指令。\n"
        )

        try:
            response = self._agent.run(prompt)
        except Exception as exc:
            logger.warning("Report generation failed; using fallback report: %s", exc)
            return self._build_fallback_report(
                state,
                outline,
                normalized_tasks,
                reason=str(exc),
            )
        finally:
            self._safe_clear_history()

        report_text = response.strip()
        if self._config.strip_thinking_tokens:
            report_text = strip_thinking_tokens(report_text)

        report_text = strip_tool_calls(report_text).strip()
        return report_text or self._build_fallback_report(
            state,
            outline,
            normalized_tasks,
            reason="LLM 返回空报告",
        )

    def _normalize_tasks(
        self,
        state: SummaryState,
        task_results: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Merge TodoItems with structured worker results for report writing."""

        result_by_id = {
            int(result.get("task_id") or 0): result
            for result in (task_results or [])
            if result.get("task_id") is not None
        }

        normalized_tasks: list[dict[str, Any]] = []
        for task in state.todo_items:
            result = result_by_id.get(task.id, {})
            summary = (task.summary or result.get("summary") or "暂无可用信息").strip()
            sources_summary = (
                task.sources_summary or result.get("sources_summary") or "暂无来源"
            ).strip()
            citations = list(result.get("citations") or self._extract_citations(sources_summary))
            key_findings = list(result.get("key_findings") or self._extract_key_findings(summary))
            evidence_points = list(
                result.get("evidence_points")
                or key_findings
                or self._extract_key_findings(sources_summary)
            )

            normalized_tasks.append(
                {
                    "task_id": task.id,
                    "title": task.title,
                    "intent": task.intent,
                    "query": task.query,
                    "dimension": task.dimension or result.get("dimension") or task.title,
                    "status": task.status,
                    "summary": summary,
                    "sources_summary": sources_summary,
                    "key_findings": key_findings,
                    "evidence_points": evidence_points,
                    "citations": citations,
                    "error": result.get("error"),
                }
            )

        return normalized_tasks

    def _render_task_digest(self, tasks: list[dict[str, Any]]) -> str:
        """Render a compact version of task results for outline generation."""

        blocks: list[str] = []
        for task in tasks[:OUTLINE_MAX_TASKS]:
            findings = [
                self._truncate(str(item), OUTLINE_FINDING_LIMIT)
                for item in (task["key_findings"] or [task["summary"]])[:4]
            ]
            blocks.append(
                "\n".join(
                    [
                        f"- task_id: {task['task_id']}",
                        f"  title: {task['title']}",
                        f"  dimension: {task['dimension']}",
                        f"  intent: {task['intent']}",
                        f"  key_findings: {findings[:4]}",
                        f"  citations: {task['citations'][:3]}",
                    ]
                )
            )
        if len(tasks) > OUTLINE_MAX_TASKS:
            blocks.append(f"- 其余任务：{len(tasks) - OUTLINE_MAX_TASKS} 个，已在最终汇总阶段保留索引")
        return "\n".join(blocks)

    def _render_task_dossier(self, tasks: list[dict[str, Any]]) -> str:
        """Render the detailed task bundle used by the report-writing prompt."""

        blocks: list[str] = []
        for task in tasks[:REPORT_MAX_TASKS]:
            finding_lines = self._format_bullets(task["key_findings"] or ["暂无关键发现"])
            evidence_lines = self._format_bullets(task["evidence_points"] or ["暂无证据条目"])
            citation_lines = self._format_bullets(task["citations"] or ["暂无来源线索"])
            blocks.append(
                "\n".join(
                    [
                        f"### 任务 {task['task_id']}: {task['title']}",
                        f"- 比较维度：{task['dimension']}",
                        f"- 任务目标：{task['intent']}",
                        f"- 检索查询：{task['query']}",
                        f"- 执行状态：{task['status']}",
                        f"- 任务总结：\n{self._truncate(task['summary'], REPORT_SUMMARY_LIMIT)}",
                        f"- 关键发现：\n{finding_lines}",
                        f"- 证据要点：\n{evidence_lines}",
                        f"- 来源概览：\n{self._truncate(task['sources_summary'], REPORT_SOURCE_LIMIT)}",
                        f"- 引用线索：\n{citation_lines}",
                    ]
                )
            )
        if len(tasks) > REPORT_MAX_TASKS:
            omitted = len(tasks) - REPORT_MAX_TASKS
            blocks.append(f"### 其余任务\n- 为控制最终报告提示词长度，另有 {omitted} 个任务仅进入来源索引和兜底报告。")
        return "\n\n".join(blocks)

    def _build_comparison_table(self, tasks: list[dict[str, Any]]) -> str:
        """Build a concrete comparison-table seed for the report writer."""

        if not tasks:
            return "| 维度 | 对应任务 | 关键结论 | 代表证据 |\n| --- | --- | --- | --- |\n| 暂无 | 暂无 | 暂无 | 暂无 |"

        rows = ["| 维度 | 对应任务 | 关键结论 | 代表证据 |", "| --- | --- | --- | --- |"]
        for task in tasks:
            key_finding = self._truncate((task["key_findings"] or [task["summary"]])[0], 80)
            citation = self._truncate((task["citations"] or ["待补充来源线索"])[0], 80)
            rows.append(
                "| {dimension} | 任务 {task_id}: {title} | {finding} | {citation} |".format(
                    dimension=self._table_cell(task["dimension"]),
                    task_id=task["task_id"],
                    title=self._table_cell(task["title"]),
                    finding=self._table_cell(key_finding),
                    citation=self._table_cell(citation),
                )
            )
        return "\n".join(rows)

    def _build_fallback_report(
        self,
        state: SummaryState,
        outline: dict[str, Any],
        tasks: list[dict[str, Any]],
        *,
        reason: str,
    ) -> str:
        """Build a deterministic report when the final report LLM call fails."""

        raw_dimensions = outline.get("comparison_dimensions") if isinstance(outline, dict) else []
        dimensions = [
            str(item).strip()
            for item in (raw_dimensions or [])
            if str(item).strip()
        ]
        if not dimensions:
            dimensions = []
            for task in tasks:
                dimension = str(task.get("dimension") or task.get("title") or "").strip()
                if dimension and dimension not in dimensions:
                    dimensions.append(dimension)
        if not dimensions:
            dimensions = ["背景概览", "证据与数据", "风险与挑战"]

        insight_lines: list[str] = []
        for task in tasks:
            findings = task["key_findings"] or task["evidence_points"] or [task["summary"]]
            for finding in findings[:2]:
                insight_lines.append(
                    "- 任务 {task_id}（{dimension}）：{finding}".format(
                        task_id=task["task_id"],
                        dimension=task["dimension"],
                        finding=self._truncate(str(finding), REPORT_POINT_LIMIT),
                    )
                )
                if len(insight_lines) >= 6:
                    break
            if len(insight_lines) >= 6:
                break
        if not insight_lines:
            insight_lines.append("- 当前任务未产生足够结构化发现，请补充搜索结果或检查模型配置。")

        evidence_sections: list[str] = []
        for dimension in dimensions:
            matching_tasks = [
                task for task in tasks if str(task.get("dimension") or task.get("title")) == dimension
            ]
            if not matching_tasks:
                continue
            lines = [f"### {dimension}"]
            for task in matching_tasks:
                evidence = task["evidence_points"] or task["key_findings"] or [task["summary"]]
                lines.append(
                    "任务 {task_id}：{title}\n{bullets}".format(
                        task_id=task["task_id"],
                        title=task["title"],
                        bullets=self._format_bullets(evidence, max_items=4),
                    )
                )
            evidence_sections.append("\n\n".join(lines))
        if not evidence_sections:
            evidence_sections.append("### 证据概览\n暂无足够证据条目。")

        risks = [
            "- 最终报告 LLM 调用未完成，本报告由代码兜底汇总生成，综合判断的文风和深度会弱于完整模型报告。",
            "- 若搜索结果中缺少可验证来源，结论需要二次核验后再用于决策。",
            "- 不同任务之间的证据强弱可能不均衡，建议优先复查来源数量较少的维度。",
        ]

        references = self._render_reference_groups(tasks)
        failure_note = self._truncate(reason, 240)

        return "\n\n".join(
            [
                f"# 研究报告：{state.research_topic}",
                f"> 说明：最终报告 LLM 调用失败（{failure_note}），系统已基于已完成的并行 worker 结果生成兜底报告，避免研究流程中断。",
                "## 背景概览\n本报告围绕研究主题和已完成的子任务结果进行汇总。由于最终报告撰写模型超时，以下内容优先保留可追溯事实、任务维度和来源线索。",
                "## 核心洞见\n" + "\n".join(insight_lines),
                "## 证据与数据\n" + self._build_comparison_table(tasks) + "\n\n" + "\n\n".join(evidence_sections),
                "## 风险与挑战\n" + "\n".join(risks),
                "## 参考来源\n" + references,
            ]
        )

    def _render_reference_groups(self, tasks: list[dict[str, Any]]) -> str:
        """Render source lines grouped by task for fallback reports."""

        if not tasks:
            return "- 暂无来源线索"

        groups: list[str] = []
        for task in tasks:
            citations = task["citations"] or self._extract_citations(task["sources_summary"])
            if not citations:
                citations = [self._truncate(task["sources_summary"], REPORT_SOURCE_LIMIT)]
            groups.append(
                "### 任务 {task_id}: {title}\n{citations}".format(
                    task_id=task["task_id"],
                    title=task["title"],
                    citations=self._format_bullets(citations, max_items=6),
                )
            )
        return "\n\n".join(groups)

    def _parse_outline_response(self, raw_response: str) -> dict[str, Any] | None:
        """Parse the outline LLM output into a JSON object."""

        text = raw_response.strip()
        if self._config.strip_thinking_tokens:
            text = strip_thinking_tokens(text)
        text = strip_tool_calls(text).strip()

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        section_plan = payload.get("section_plan")
        if not isinstance(section_plan, list):
            payload["section_plan"] = []

        comparison_dimensions = payload.get("comparison_dimensions")
        if not isinstance(comparison_dimensions, list):
            payload["comparison_dimensions"] = []

        return payload

    def _fallback_outline(
        self,
        state: SummaryState,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Deterministic outline used when LLM outline planning fails."""

        dimensions: list[str] = []
        for task in tasks:
            dimension = str(task.get("dimension") or task.get("title") or "").strip()
            if dimension and dimension not in dimensions:
                dimensions.append(dimension)

        if not dimensions:
            dimensions = ["背景概览", "能力比较", "落地与成本", "风险与挑战"]

        task_ids = [task["task_id"] for task in tasks]
        return {
            "executive_judgment": f"围绕“{state.research_topic}”做多维证据化比较，并给出综合判断。",
            "comparison_dimensions": dimensions[:6],
            "section_plan": [
                {
                    "heading": "背景概览",
                    "purpose": "说明研究主题、评估口径与比较范围",
                    "task_ids": task_ids[:1],
                },
                {
                    "heading": "核心洞见",
                    "purpose": "提炼最关键的综合结论与排序逻辑",
                    "task_ids": task_ids,
                },
                {
                    "heading": "证据与数据",
                    "purpose": "按比较维度展开任务证据与关键数据",
                    "task_ids": task_ids,
                },
            ],
            "table_plan": {
                "title": "主要维度对比表",
                "columns": ["维度", "领先者/方案", "证据", "备注"],
            },
            "citation_focus": [task["title"] for task in tasks[:5]],
        }

    def _safe_clear_history(self) -> None:
        """Clear agent chat history without masking the original reporting error."""

        clear_history = getattr(self._agent, "clear_history", None)
        if not callable(clear_history):
            return

        try:
            clear_history()
        except Exception:  # pragma: no cover - defensive cleanup
            logger.debug("Failed to clear report agent history", exc_info=True)

    @classmethod
    def _format_bullets(
        cls,
        items: list[Any],
        *,
        max_items: int = 5,
        limit: int = REPORT_POINT_LIMIT,
    ) -> str:
        """Render a bounded bullet list so report prompts stay under timeout-prone sizes."""

        clean_items: list[str] = []
        for item in items[:max_items]:
            text = cls._truncate(str(item), limit).replace("\n", " ").strip()
            if text:
                clean_items.append(f"- {text}")
        return "\n".join(clean_items) if clean_items else "- 暂无可用信息"

    @staticmethod
    def _table_cell(value: Any) -> str:
        """Escape markdown table separators in generated table cells."""

        return str(value).replace("\n", " ").replace("|", "\\|").strip()

    @staticmethod
    def _truncate(text: str | None, limit: int) -> str:
        if not text:
            return "暂无可用信息"
        normalized = text.strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit]}..."

    @staticmethod
    def _extract_key_findings(text: str, *, max_items: int = 5) -> list[str]:
        findings: list[str] = []
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if candidate.startswith(("###", "##")):
                continue
            if re.match(r"^[-*]\s+", candidate):
                findings.append(re.sub(r"^[-*]\s+", "", candidate))
            elif re.match(r"^\d+[.)、]\s+", candidate):
                findings.append(re.sub(r"^\d+[.)、]\s+", "", candidate))
            if len(findings) >= max_items:
                return findings

        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", text)
            if paragraph.strip()
        ]
        return paragraphs[:max_items]

    @staticmethod
    def _extract_citations(text: str, *, max_items: int = 6) -> list[str]:
        citations: list[str] = []
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if "http://" in candidate or "https://" in candidate:
                citations.append(candidate.lstrip("- ").strip())
            if len(citations) >= max_items:
                break
        return citations
