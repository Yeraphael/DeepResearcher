# DeepResearcher 下一步改造方案

## 背景

当前 LangGraph MVP 已经完成了“旧 orchestrator -> LangGraph 单图”的替换，但它仍然存在两个明显问题：

1. 研究任务执行仍然是串行的，整体耗时过高。
2. 最终报告质量相比旧框架明显下降。

这两个问题都已经在当前仓库中有直接证据。

## 现状问题

### 1. 当前图其实还是串行

当前实现位于：

- `backend/src/graph/builder.py`
- `backend/src/graph/nodes/search.py`

目前的主流程是：

```text
START
  -> ingest_request
  -> plan_tasks
  -> run_research_tasks
  -> compile_report
  -> persist_report
  -> END
```

其中 `run_research_tasks` 虽然名字上看像“任务执行节点”，但本质上还是一个节点内部的 `for task in todo_items` 顺序循环：

- 先执行 task 1 的 search
- 再执行 task 1 的 summarize
- 然后才执行 task 2
- 最后再执行 task 3/4/5

这意味着：

- LangGraph 只替换了编排壳，但没有真正利用动态图并行能力。
- 总耗时接近“所有任务耗时之和”，而不是“最慢几个任务中的最大值”。
- 对于搜索 + LLM 摘要这类 I/O 型任务，串行会非常慢。

### 2. 当前报告质量明显退化

用户给出的两个报告样本：

- 旧框架报告：`backend/notes/note_20260421_020726_37.md`
- 新框架报告：`backend/notes/note_20260421_155739_70.md`

直接对比可以看到：

- `37` 文件长度约 `7332` 字符
- `70` 文件长度约 `2706` 字符

结构差异也很明显：

`37` 包含：

- 背景概览
- 核心洞见
- 证据与数据
- 模型能力维度
- 产品与平台维度
- 企业落地与成本效率维度
- 生态与开发者支持维度
- 风险与挑战
- 参考来源

`70` 只有：

- 背景概览
- 核本洞见
- 证据与数据
- 风险与挑战
- 参考来源

问题包括：

- 信息密度明显下降
- 缺少按维度展开的比较段落
- 缺少表格化证据组织
- “核心洞见”甚至出现了标题质量问题
- 最终结论更像“摘要”，不像“证据化研究报告”

## 根因分析

### A. 并行问题的根因

当前实现仍是“单节点串行执行全部任务”，而不是 LangGraph 官方更适合本场景的：

- orchestrator-worker
- `Send`
- 动态 fan-out

这类研究任务的特点是：

- planner 先生成 3-5 个子任务
- 子任务数量运行时才知道
- 每个子任务执行逻辑相似
- 每个子任务之间天然可以并行

这正是 `Send` 最适合的场景。

### B. 报告质量问题的根因

当前报告质量退化，不是单一原因，而是多个因素叠加：

#### 1. 汇总输入退化

当前 `compile_report` 阶段主要依赖：

- 每个任务的 `task.summary`
- 每个任务的 `sources_summary`
- 简化后的 note 引用

但没有把“更结构化的中间研究产物”保留下来，例如：

- 每个子任务的关键发现列表
- 对比维度归属
- 证据条目
- 厂商级观察
- 风险与反证
- 可直接引用的来源清单

结果是最终报告 writer 只能基于“松散摘要”重写，容易变成泛化空话。

#### 2. report prompt 被压得太薄

当前 `backend/src/services/reporter.py` 的 prompt 过于简化，主要问题是：

- 没有强约束“必须按六大维度对比”
- 没有强约束“必须给出综合判断和排序逻辑”
- 没有要求输出对比表格
- 没有要求每条核心洞见绑定证据
- 没有要求引用粒度足够细

因此模型容易产出“泛研究报告模板”，而不是“面向比较任务的证据化报告”。

#### 3. 缺少显式的 report planning

当前是：

- 任务完成
- 直接一次性生成最终报告

缺少中间步骤：

- 先做 report outline / comparison frame
- 再按章节生成
- 最后做编辑和收敛

对于复杂比较型研究，这种一步到位通常效果不稳定。

#### 4. worker 输出不是结构化研究结果

现在 task summarizer 返回的是自由文本 Markdown。

更合适的其实是“结构化任务结果”，至少包含：

- `dimension`
- `key_findings`
- `evidence_points`
- `vendor_observations`
- `citations`
- `open_questions`

如果中间结果不结构化，最后的 reporter 就很难做高质量聚合。

## 目标方案

## 一、执行层改造成真正的 orchestrator-worker + Send

### 目标图结构

建议改成下面这个结构：

```text
START
  -> ingest_request
  -> plan_tasks
  -> dispatch_workers
      -> Send(worker_for_task_1)
      -> Send(worker_for_task_2)
      -> Send(worker_for_task_3)
      -> ...
  -> aggregate_results
  -> build_report_outline
  -> compile_report
  -> persist_report
  -> END
```

其中：

- `plan_tasks` 负责生成动态数量的子任务
- `dispatch_workers` 使用 `Send` 把每个子任务扔给并行 worker
- 每个 worker 独立完成 search + summarize
- `aggregate_results` 负责收拢 worker 输出
- `build_report_outline` 负责把任务结果组织成可写报告的框架
- `compile_report` 再根据 outline + structured task results 生成最终报告

### 为什么这个方案更适合

相比当前单节点串行循环，这个方案的好处是：

- 子任务数量可以运行时动态决定
- 每个 worker 可以处于同一个 super-step 并行执行
- 搜索和摘要属于 I/O 型操作，适合 `async def`
- 更符合 LangGraph 官方推荐模式

### 实施建议

#### 1. 引入 TaskSpec / TaskResult

建议在 `backend/src/graph/state.py` 里新增：

- `TaskSpec`
- `TaskResult`

示意：

```python
class TaskSpec(TypedDict):
    task_id: int
    title: str
    intent: str
    query: str
    dimension: str

class TaskResult(TypedDict):
    task_id: int
    title: str
    intent: str
    query: str
    dimension: str
    status: str
    key_findings: list[str]
    evidence_points: list[dict[str, str]]
    citations: list[dict[str, str]]
    sources_summary: str
    task_summary: str
    note_id: str | None
    note_path: str | None
    error: str | None
```

#### 2. 顶层状态不要再把“执行逻辑”塞进一个节点内部

顶层状态应偏向：

- planner 输入输出
- worker 结果聚合
- report 聚合产物

而不是把所有逻辑都塞到 `todo_items` 的原地修改里。

#### 3. worker 节点建议 `async def`

因为 worker 的主要时间花在：

- 搜索
- LLM 摘要

所以应改为异步节点，并在 runner 层使用：

- `.ainvoke()`
- `.astream()`

这样能显著改善并发吞吐。

#### 4. 增加并发上限

并行不等于无限并发。

建议增加配置项，例如：

- `max_parallel_research_tasks=3`

这样可以避免：

- 搜索 provider 限流
- LLM provider 并发过载
- 本地模型/代理连接数爆掉

## 二、报告层改造成“结构化聚合 + 两阶段写作”

### 目标

最终报告至少要恢复到旧版 `37` 的信息密度，并进一步稳定化。

### 建议分两段生成

#### 阶段 1：build_report_outline

这个节点不直接写最终报告，而是做：

- 确认研究问题的比较维度
- 汇总每个维度对应的任务结果
- 生成章节提纲
- 决定哪些内容要表格化
- 决定哪些内容要做综合判断

输出示例：

- `executive_judgment`
- `comparison_dimensions`
- `section_plan`
- `table_plan`
- `citation_groups`

#### 阶段 2：compile_report

基于：

- `TaskResult[]`
- `report_outline`
- notes 中的任务记录

生成最终报告。

### 报告 prompt 的改造方向

建议在 `backend/src/prompts.py` 新增一套更强的报告提示词，例如：

- `report_outline_instructions`
- `report_writer_instructions_v2`

其中 `report_writer_instructions_v2` 应明确要求：

1. 必须按用户指定的六大维度展开
2. 必须给出综合判断，而不是只做平铺罗列
3. 必须包含至少一个对比表
4. 每条核心洞见都必须绑定证据
5. 引用要细到任务/来源级
6. 风险与不确定性必须显式写出

### worker 输出要结构化

建议把 task summarizer 的目标从“自由文本总结”改成：

- 先生成结构化结果
- 再生成人类可读摘要

也就是说，worker 最终最好同时产出两份东西：

1. `structured_task_result`
2. `task_summary_markdown`

这样：

- SSE 前端继续展示 `task_summary_markdown`
- report 聚合则用 `structured_task_result`

## 三、建议的文件改动点

### 1. 图编排

- `backend/src/graph/builder.py`

改造方向：

- 去掉“大一统 `run_research_tasks` 节点”
- 增加 `dispatch_workers`
- 增加 `aggregate_results`
- 增加 `build_report_outline`

### 2. 状态模型

- `backend/src/graph/state.py`

改造方向：

- 增加 `TaskSpec`
- 增加 `TaskResult`
- 增加 `task_results`
- 增加 `report_outline`
- 为聚合字段定义 reducer

### 3. worker 节点

建议新增：

- `backend/src/graph/nodes/orchestrator.py`
- `backend/src/graph/nodes/worker.py`
- `backend/src/graph/nodes/aggregate.py`

其中：

- orchestrator 负责 `Send`
- worker 负责 search + summarize
- aggregate 负责聚合 task results

### 4. 报告层

- `backend/src/services/reporter.py`
- `backend/src/prompts.py`

改造方向：

- 拆成 outline + write 两步
- 使用更强约束 prompt
- 引入结构化输入而不是单纯拼 task summary

### 5. runner 层

- `backend/src/application/research_runner.py`

改造方向：

- 支持 async graph 执行
- 使用 `.astream()` / `.ainvoke()`
- 保持现有 `/research`、`/research/stream` API 不变

## 四、分阶段落地方案

### Phase 1：并行执行改造

目标：

- 保持现有 API 不变
- 保持 SSE 事件类型不变
- 先把串行执行改成 `Send` 并行 worker

交付标准：

- 任务规划后，多个 task 能同时进入 `in_progress`
- 总耗时明显下降
- `/research/stream` 仍然能看到每个任务的 `task_status`、`sources`、`task_summary_chunk`

### Phase 2：报告质量修复

目标：

- 恢复旧版报告的信息密度
- 输出稳定的多维度对比报告

交付标准：

- 最终报告长度、结构、证据密度至少接近 `note_20260421_020726_37.md`
- 必须包含多维度分节
- 必须包含对比表
- 必须包含综合结论与风险说明

### Phase 3：质量评估与回归

目标：

- 对报告质量做回归验证
- 防止以后再次出现“运行更先进、结果更差”的退化

建议加入：

- 报告长度阈值
- 必要章节完整性检查
- 关键对比维度覆盖检查
- 参考来源数量与格式检查

## 五、验收标准

### 并行层

- planner 输出 3-5 个任务后，worker 可动态并行执行
- 不再使用单节点串行 `for` 循环完成所有任务
- graph 使用 `Send` 实现动态 fan-out
- worker 节点支持 async

### 报告层

- 报告不再只是任务摘要重写
- 最终报告基于结构化 `TaskResult` 聚合生成
- 报告质量至少恢复到旧样本 `37` 的水位
- 对比型研究任务能稳定输出“维度化比较 + 证据 + 结论”

## 结论

这次问题的本质不是“LangGraph 不行”，而是当前实现还停留在：

- 编排层换成了 LangGraph
- 但执行模型仍是串行 orchestrator
- 报告聚合仍是一次性自由文本重写

下一步正确方向应当是：

1. 用 `orchestrator-worker + Send` 取代当前串行 `run_research_tasks`
2. 用 `async worker` 提升 I/O 并发性能
3. 用 `TaskResult` 结构化中间产物，修复报告输入质量
4. 用 `outline -> compile` 两阶段写作恢复最终报告质量

如果只做其中一半，例如只并行、不修报告，或者只修 prompt、不改中间结构，效果都会有限。最合理的路线是“执行层并行化 + 报告层结构化聚合”一起推进。
