# DeepResearcher 基于 LangGraph 的重构评估与实施计划

> 适用项目：`F:\Project\agent\DeepResearcher`  
> 参考基线：本地项目源码、`hello-agents==0.2.9` 实际依赖、`jjyaoao/HelloAgents` 公开仓库说明  
> 说明：对参考仓库主分支未直接展开的内部细节，统一标注为“基于仓库常见实现推断”

---

## 一、结论先行

### 1.1 总体判断

**结论：用 LangGraph 重构当前项目是可行的，而且值得做，但不建议一次性全量推倒重写。**

原因不是“HelloAgents 不好”，而是**你当前项目已经从教学型 Agent 编排进入了工程型工作流系统的阶段**，而现有实现已经出现了典型的工程瓶颈：

- 工作流是隐式的，主要靠 [`backend/src/agent.py`](F:\Project\agent\DeepResearcher\backend\src\agent.py) 中的命令式流程和线程控制驱动
- 状态是内存态 `dataclass`，没有 durable execution、没有 checkpoint、没有恢复点
- 工具调用有两套机制并存：
  - 一套是 `hello-agents` 的 `ToolAwareSimpleAgent` + `[TOOL_CALL:...]`
  - 一套是本地直接函数调用 `dispatch_search(...)`
- “多智能体”本质上是多个 prompt 角色共享同一个协调器，不是真正可组合、可路由、可恢复的图结构
- 当前“研究循环”并非真正 loop graph，只是“规划一次 -> 并发执行任务 -> 汇总一次”
- Memory 当前主要是 `NoteTool` 的 Markdown 文件沉淀，不是可检索、可路由、可分层的短期/长期记忆系统

**LangGraph 能解决的不是“换个框架写同样逻辑”，而是把当前隐式流程变成显式状态图，把当前脆弱的内存态协程/线程式编排，升级为可恢复、可扩展、可观测的 workflow runtime。**

### 1.2 是否值得重构

**值得重构的前提：**

- 你希望后续引入真正的多智能体协作，而不是只有多个 prompt persona
- 你希望支持会话恢复、人工介入、失败重试、长任务续跑
- 你希望引入 RAG / 长期记忆 / 任务重规划 / 子图并发
- 你希望后端成为一个稳定的 Agent Workflow 服务，而不是 demo 型串联脚本

**不值得大动干戈的场景：**

- 仅做本地 demo，单次研究完成即可
- 任务规模始终很小，且无需恢复、追踪、持久化
- 不打算引入复杂路由、HITL、RAG、任务并行、状态回放

### 1.3 建议的重构策略

**建议采用“分阶段局部替换，最终收敛到 LangGraph 主干”的路线，而不是一步到位全重写。**

推荐策略：

1. **先保留现有 FastAPI API、前端 SSE 协议、搜索/报告服务逻辑**
2. **先把 `DeepResearchAgent` 替换成 LangGraph `graph` 执行器**
3. **再逐步把状态、记忆、重试、持久化、RAG、子图并发迁移进去**

### 1.4 保留 / 适配 / 重写建议

| 类别 | 处理建议 | 说明 |
|---|---|---|
| FastAPI 入口 | 保留 | `/research` 与 `/research/stream` 可以继续存在，仅替换内部执行引擎 |
| 前端 SSE 事件消费 | 保留并适配 | 事件类型可延续，但后端事件源改为 graph 事件流 |
| Prompt 文案资产 | 保留并适配 | 当前 planner/summarizer/reporter prompt 可继续复用 |
| 搜索服务封装 | 保留并适配 | `dispatch_search`、`prepare_research_context` 可作为 tool/node 内部实现 |
| Note 输出沉淀 | 保留并适配 | 从“模型发 note tool 指令”迁移为“节点显式落库/落笔记”更稳 |
| `SummaryState` / `TodoItem` | 重构 | 从松散 dataclass 升级为 graph state + typed submodels |
| `DeepResearchAgent` | 重写 | 其职责应被 graph builder / runner / event translator 替代 |
| 任务并发机制 | 重写 | 当前基于 `Thread` 的执行方式应迁移为 graph 分支/子图并发 |
| 会话持久化 | 新增并接管 | 当前基本缺失，需要 checkpointer + session store |
| Memory/RAG 层 | 重构新增 | 当前只有 NoteTool 沉淀，不足以支撑长期记忆与召回 |

### 1.5 MVP 重构路径

**MVP 不做“全功能 LangGraph 平台”，只做最值钱的 5 件事：**

1. 把当前线性流程改成 LangGraph 单图
2. 把当前内存态 `SummaryState` 改成 graph state
3. 接入 SQLite checkpointer，支持断点恢复
4. 把当前工具调用统一成“显式节点调用 + 少量 agent tool calling”
5. 保持现有 API 和前端兼容

这一步完成后，系统就已经从“脚本式 agent orchestration”升级为“可恢复工作流”，ROI 很高。

---

## 二、现状架构分析

## 2.1 当前项目实际基座

从 [`backend/pyproject.toml`](F:\Project\agent\DeepResearcher\backend\pyproject.toml) 看，当前后端核心依赖是：

- `fastapi`
- `hello-agents==0.2.9`
- `tavily-python`
- `ddgs`
- `openai`
- `loguru`

这意味着当前项目**不是直接基于最新主线版 HelloAgents 仓库能力开发**，而是基于较早期的 `0.2.9` 包版本。  
而 `jjyaoao/HelloAgents` 当前公开主仓库 README 已经展示出更工程化的 `v1.0` 结构与能力，这两者之间有明显代差。

### 关键影响

- 你现在的项目**实际运行能力**，应以本地安装的 `hello-agents==0.2.9` 为准
- 参考仓库可以提供设计方向，但不能直接等同于你当前系统能力
- 这也是为什么当前项目虽然“看起来像多 Agent”，但在 durable workflow、session、graph orchestration 上仍明显不够

## 2.2 当前 Agent 组织方式

当前后端核心在 [`backend/src/agent.py`](F:\Project\agent\DeepResearcher\backend\src\agent.py)。

### 当前角色拆分

当前系统有 3 个核心角色：

- `todo_agent`
  - 用于任务规划
  - 对应 `PlanningService`
- `summarizer_agent`
  - 通过 `_summarizer_factory()` 动态创建
  - 用于单任务总结
- `report_agent`
  - 用于最终报告生成

### 实际组织方式

这不是 LangGraph 式多节点图，也不是真正自治的多智能体系统，而是：

- 一个总协调器 `DeepResearchAgent`
- 多个不同 system prompt 的 `ToolAwareSimpleAgent`
- 若干 service 函数负责把 prompt、解析、搜索、落笔记拼起来

**本质上是“单 orchestrator + 多 prompt specialist”的角色化流水线。**

### 当前优点

- 角色边界比较清楚
- 对 prompt 职责拆分比较自然
- 后续迁移到 LangGraph 时，很适合映射成多个 node

### 当前问题

- Agent 之间不共享统一图状态，只共享 Python 对象
- Agent 是“被主程序调用”，不是“图中的状态节点”
- 没有 routing policy，没有条件边，没有失败分支
- 角色之间的协作协议不稳定，主要靠 prompt 约束和字符串后处理

## 2.3 当前 Tool Calling 实现方式

当前项目的工具调用是**混合式实现**。

### 类型 A：模型内工具调用

来自 `hello-agents` 的 `ToolAwareSimpleAgent`，工具调用格式是：

```text
[TOOL_CALL:tool_name:{...}]
```

当前项目真正注册到 `ToolRegistry` 的工具只有：

- `NoteTool`

也就是说：

- planner 会被 prompt 要求写 note
- summarizer 会被 prompt 要求 read/update note
- reporter 会被 prompt 要求 read note / create conclusion note

### 类型 B：代码直调工具

搜索并不是由 agent 自主 tool call 完成，而是由协调器直接调用：

- [`backend/src/services/search.py`](F:\Project\agent\DeepResearcher\backend\src\services\search.py)
  - `dispatch_search(...)`
  - `prepare_research_context(...)`

也就是：

- 搜索不是 graph 内显式 node
- 也不是 agent 层统一工具
- 而是协调器自己调用 service 函数

### 这会造成什么问题

1. **工具调用语义不统一**
   - note 走模型 tool call
   - search 走代码直调
2. **可观测性割裂**
   - tool tracker 只能完整追 note 事件
   - 搜索结果虽然能通过 SSE 发给前端，但不是统一 tool event 生命周期
3. **异常边界不一致**
   - search 异常在 Python 层处理
   - note 异常常以模型输出文本形式表现
4. **后续迁移困难**
   - 你无法直接把“当前 orchestrator 中的函数调用流”映射成“统一工具图”

### 结论

当前工具架构适合 demo，不适合继续扩展。  
在 LangGraph 里应改成：

- **确定性操作优先做成节点**
  - search
  - dedup
  - persist
  - report save
- **开放式调用保留为 agent tools**
  - 例如规划阶段允许 agent 调用部分工具
- **能不用 LLM 决定的，就不要交给 LLM tool calling**

## 2.4 当前上下文 / 状态管理方式

当前工作流状态定义在 [`backend/src/models.py`](F:\Project\agent\DeepResearcher\backend\src\models.py)：

- `TodoItem`
- `SummaryState`
- `SummaryStateInput`
- `SummaryStateOutput`

### 当前状态特征

`SummaryState` 包含：

- `research_topic`
- `web_research_results`
- `sources_gathered`
- `research_loop_count`
- `running_summary`
- `todo_items`
- `structured_report`
- `report_note_id`
- `report_note_path`

### 当前状态管理方式

- 由 `DeepResearchAgent.run()` / `run_stream()` 直接 new 一个 `SummaryState`
- 在进程内逐步修改
- 流式模式下多线程共享同一个 `state`
- 通过 `_state_lock` 局部保护共享 list append 与 counter 增加

### 当前问题

1. **状态不是 durable 的**
   - 服务重启就丢
   - 请求中断无法恢复
2. **状态类型不够严格**
   - `web_research_results` / `sources_gathered` 都是宽松 list
   - 缺少结构化 schema
3. **状态边界不明确**
   - 哪些字段是输入、派生、运行时、输出，没有严格区分
4. **并发安全有限**
   - 只有部分共享写操作加锁
   - 任务 summary、tool event、搜索结果仍依赖隐式时序
5. **没有 session 级状态**
   - 当前更像“一次请求一个状态对象”
   - 不是“一个会话多次研究 / 一次研究多次续跑”

## 2.5 当前多轮对话机制

表面上底层 `hello-agents` 有 `_history`，但当前项目基本**主动清空**历史：

- `planner_agent.run(...)` 后 `clear_history()`
- `summarizer_agent` 每次现建现用，结束后 `clear_history()`
- `report_agent.run(...)` 后 `clear_history()`

### 实际结果

当前系统并没有真正意义上的多轮对话机制，而是：

- 每个步骤都是独立 prompt 执行
- 共享的信息通过 Python state/context text 注入
- agent 自身 history 几乎不保留

### 工程意义

这说明当前系统更适合迁移到 LangGraph，因为它本来就不是 chat-first 结构，而是 workflow-first。

## 2.6 当前 Memory 机制

当前 Memory 主要有两层：

### 层 1：工作流内短期状态

- `SummaryState`
- `TodoItem`

这是临时内存态，不持久

### 层 2：NoteTool 文件沉淀

- `backend/notes/*.md`
- `backend/notes/notes_index.json`

由 `NoteTool` 进行 Markdown + frontmatter 结构化保存

### 这算不算真正 memory

**严格说，不算完整 memory architecture。**

更准确地说：

- 它是“运行过程笔记沉淀”
- 不是“短期记忆 + 长期记忆 + 语义检索 + 会话恢复”体系

### 当前缺口

- 没有 session memory
- 没有 semantic retrieval memory
- 没有 episodic memory for replay
- 没有 memory compaction / summarization
- 没有基于任务、会话、用户维度的 memory namespace

## 2.7 当前编排机制

当前编排完全依赖 [`backend/src/agent.py`](F:\Project\agent\DeepResearcher\backend\src\agent.py) 中的命令式逻辑。

### 非流式路径

```text
topic
-> plan todo list
-> for each task: search + summarize
-> generate report
-> persist report
```

### 流式路径

```text
topic
-> plan todo list
-> 为每个 task 创建 Thread
-> 每个线程执行 search + summarize
-> 通过 Queue 聚合事件
-> 全部结束后生成 final report
```

### 这是当前最大的工程瓶颈

因为这套编排：

- 没有显式节点边界
- 没有 state reducer
- 没有 checkpoint
- 没有中断恢复
- 没有 retry policy
- 没有 graph introspection
- 没有 branch policy
- 没有 task-level subgraph

## 2.8 当前可扩展性瓶颈

### 瓶颈 1：主协调器过胖

`DeepResearchAgent` 同时负责：

- 初始化 LLM
- 初始化工具
- 管理流式事件
- 任务并发
- 状态写入
- 报告落地
- note 事件处理

这会导致：

- 修改成本高
- 任何新能力都会继续堆进 `agent.py`

### 瓶颈 2：配置项与运行机制脱节

例如：

- `use_tool_calling` 在当前代码中仅配置和日志出现，**没有实际接管 agent 类型选择**
- `max_web_research_loops` 已配置，但当前主流程并没有真正按 graph loop 使用它

### 瓶颈 3：状态结构不适合复杂路由

如果后续加入：

- 任务重规划
- 缺口分析
- 人工确认
- RAG 检索分支
- 多智能体投票

当前 `SummaryState` 不足以支持

### 瓶颈 4：前后端事件协议与执行引擎强绑定

当前 SSE 事件是 `agent.py` 手搓出来的：

- `status`
- `todo_list`
- `task_status`
- `sources`
- `task_summary_chunk`
- `tool_call`
- `final_report`
- `done`

这不是问题本身，但意味着：

- 一旦执行引擎变化，必须重新梳理事件源
- 如果没有统一 event translator，前端会被后端内部实现牵着走

### 瓶颈 5：并发模型脆弱

当前流式执行使用 `Thread + Queue + shared state + shared llm instance`。

这类实现的风险包括：

- 对底层 client 线程安全有隐含依赖
- 任务间共享对象过多
- 出错恢复粒度粗
- 无法优雅恢复单 task

该风险属于**基于仓库常见实现推断**，但从当前代码组织看，确实不够稳。

---

## 三、与 HelloAgents 参考仓库的关系判断

## 3.1 参考仓库当前主线能力

根据 `jjyaoao/HelloAgents` README，当前主线版强调：

- Function Calling 架构
- Context engineering
- SessionStore
- 流式输出
- 子代理机制
- Skills
- TodoWrite / DevLog
- Trace / observability

## 3.2 当前项目实际使用能力

你当前项目实际上只用到了其中很小的一部分：

- `HelloAgentsLLM`
- `ToolAwareSimpleAgent`
- `ToolRegistry`
- `NoteTool`
- `SearchTool`

**很多理论上可用的能力，并没有接入当前 DeepResearcher：**

- `FunctionCallAgent`
- `MemoryManager`
- `ContextBuilder`
- `RAGTool`
- 更完整的 session/persistence 能力

## 3.3 这意味着什么

你的项目当前更像：

> “基于 HelloAgents 早期工具与 agent 抽象，手工拼出来的研究工作流应用”

而不是：

> “完整利用 HelloAgents 工程化能力搭建的可持久多智能体系统”

这恰恰说明：

- 直接继续在当前结构上加需求，边际成本会越来越高
- 用 LangGraph 统一执行模型，比继续在 `agent.py` 内堆逻辑更合理

---

## 四、LangGraph 重构可行性与收益判断

## 4.1 可行性

**可行性：高**

原因：

- 当前流程天然具备 graph 化边界
  - plan
  - search
  - summarize
  - gap analysis
  - report
- 当前已经有明确 state object
- 当前已经有任务粒度对象 `TodoItem`
- 当前已有事件流需求，LangGraph 对 streaming / event tracing 更适配

## 4.2 是否值得

**值得，尤其在以下目标下：**

- 长任务研究
- 任务失败恢复
- 任务重规划
- 结果持续沉淀
- 后续引入 RAG / Memory / 人工审阅
- 多智能体分工明确

## 4.3 适合全量重构的场景

以下场景建议全量切到 LangGraph 主干：

1. 你要做“研究型产品后端”
2. 你要支持多会话、多用户、多次续跑
3. 你要引入复杂条件路由和人工节点
4. 你要做任务级并发与子图复用
5. 你要建设长期演进架构

## 4.4 更适合局部改造的场景

以下场景可以先局部改造：

1. 你只想先解决断点恢复和状态持久化
2. 你暂时不需要多智能体协商
3. 你只想把 `agent.py` 解耦为节点，但不想改前端 API
4. 你要先保住现有功能稳定

## 4.5 LangGraph 相比现有实现解决的具体问题

| 当前问题 | LangGraph 能解决什么 |
|---|---|
| 工作流隐式，逻辑散在 `agent.py` | 把节点、边、分支显式化 |
| 状态只在内存里 | 提供 checkpointer / durable execution |
| 中途失败无法恢复 | 从 checkpoint 恢复继续执行 |
| 并发线程共享状态脆弱 | 用 graph 分支和 reducer 管理状态汇合 |
| 缺少条件路由 | 用 conditional edges 做 router |
| 缺少任务级子流程 | 用 subgraph 表达 per-task lifecycle |
| 缺少可观测性 | 与 tracing / LangSmith 更自然集成 |
| 工具调用混乱 | 将确定性步骤与 agent tool 区分建模 |
| 很难引入 HITL | graph 节点天然适合插入人工确认节点 |

---

## 五、现状架构到目标架构的映射分析

## 5.1 总览映射表

| 层次 | 当前实现 | 目标 LangGraph 实现 | 改造建议 |
|---|---|---|---|
| 入口层 | FastAPI `main.py` | FastAPI + graph runner | 保留 |
| 会话层 | 一次请求一个 `SummaryState` | `thread_id/session_id` + checkpoint | 重构 |
| 状态层 | `dataclass SummaryState` | `TypedDict/Pydantic` graph state | 重构 |
| 路由层 | `if/for` 手工控制 | `conditional edges` + router node | 重写 |
| 节点层 | service 函数 + agent method | graph nodes / task subgraph | 重构 |
| 工具层 | NoteTool + 直调 search | LangChain tools / deterministic service nodes | 重构 |
| 记忆层 | Note markdown | short-term checkpoint + long-term memory store | 新增/重构 |
| 检索层 | `SearchTool` 直调 + 字符串整理 | retrieval node + source normalizer + optional vector recall | 重构 |
| 模型层 | `HelloAgentsLLM` | ChatOpenAI / OpenAI-compatible adapter | 适配 |
| 编排层 | `DeepResearchAgent` | LangGraph StateGraph / subgraphs | 重写 |
| 持久化层 | notes 文件 | checkpoint DB + artifacts DB + vector store | 新增 |
| 输出层 | 手搓 SSE events | graph events -> event translator -> SSE | 适配 |

## 5.2 入口层

### 当前

[`backend/src/main.py`](F:\Project\agent\DeepResearcher\backend\src\main.py) 提供：

- `/healthz`
- `/research`
- `/research/stream`

### 目标

保留 FastAPI 接口不变，但内部改为：

- `invoke_graph(request)`
- `stream_graph_events(request)`

### 建议

- API contract 不要先动
- 先把执行引擎替换掉
- 前端零或低成本适配

## 5.3 会话层

### 当前

- 无真正 session
- 每次请求创建一个新 `SummaryState`

### 目标

引入：

- `session_id`
- `thread_id`
- `run_id`

其中建议：

- `session_id`：前端会话
- `thread_id`：LangGraph checkpoint key
- `run_id`：一次具体执行

### 建议

- MVP 阶段即引入 `thread_id`
- 允许同一研究主题断点续跑

## 5.4 状态层

### 当前

状态字段过少且过宽：

- 对任务状态的描述不够细
- 对错误、重试、分支、引用、artifact 缺少专门字段

### 目标

拆成：

- 请求输入
- 运行控制字段
- 任务状态
- 证据状态
- 报告状态
- 持久化元数据
- UI 输出状态

### 设计建议

建议使用：

- graph 顶层 state：`TypedDict`
- 复杂对象：`Pydantic BaseModel`

原因：

- LangGraph reducer 友好
- Pydantic 适合校验复杂子结构

## 5.5 路由层

### 当前

没有独立 router；主要靠：

- 规划后 `for task in todo_items`
- 搜索失败时 `skipped`
- 最后汇总

### 目标

应显式建以下路由：

- `should_replan`
- `should_retry_search`
- `should_continue_task`
- `should_finalize_report`
- `should_require_human_review`

## 5.6 节点层

### 当前

节点职责散落在：

- `PlanningService`
- `dispatch_search`
- `SummarizationService`
- `ReportingService`
- `_persist_final_report`

### 目标

每个节点应是可单测、可重试、可 tracing 的纯函数或轻量类方法。

建议拆成：

- `plan_tasks_node`
- `dispatch_tasks_node`
- `search_task_node`
- `normalize_sources_node`
- `summarize_task_node`
- `evaluate_coverage_node`
- `replan_node`
- `compile_report_node`
- `persist_artifacts_node`

## 5.7 工具层

### 当前

- note tool 由 LLM 控制
- search 由代码控制

### 目标

建议两层：

1. **Deterministic tools**
   - 搜索
   - source normalize
   - note save
   - artifact persist
2. **Agent-facing tools**
   - 仅在 planner/reviewer/researcher agent 中暴露必要工具

### 关键建议

**不要把所有动作都包装成 LLM tool calling。**  
工程上应遵循：

- 能由程序直接决定的，直接节点化
- 只有“需要模型选择是否调用”的动作，才暴露为 agent tool

## 5.8 记忆层

### 当前

- `NoteTool` 文件沉淀

### 目标

至少三层：

1. `checkpoint memory`
   - 当前运行恢复
2. `artifact memory`
   - 研究产物 / task notes / report
3. `retrieval memory`
   - 向量检索 / 语义召回

## 5.9 检索层

### 当前

检索层是：

- `SearchTool`
- `prepare_research_context`
- 字符串拼接

### 目标

拆为：

- `web_search_provider`
- `source_fetcher`
- `source_deduper`
- `citation_normalizer`
- `retrieval_joiner`

## 5.10 模型层

### 当前

`HelloAgentsLLM` 作为统一 OpenAI-compatible client。

### 目标

推荐改成：

- LangChain `ChatOpenAI` 或兼容封装
- 通过统一 model factory 管理

### 原因

- 更好接 LangGraph
- tool binding / structured output / tracing 更成熟

## 5.11 编排层

### 当前

`DeepResearchAgent`

### 目标

`StateGraph(ResearchState)` + task subgraph

### 结论

编排层应完全重写。

## 5.12 持久化层

### 当前

- markdown notes
- 无 checkpoint DB

### 目标

至少包括：

- graph checkpoint store
- relational metadata store
- vector store

## 5.13 输出层

### 当前

手工 SSE

### 目标

保留 SSE，但增加：

- graph event -> ui event translator
- 统一 event schema version

---

## 六、重构时需要改动的内容

## 6.1 目录结构如何调整

当前 `backend/src` 结构是扁平 services 风格，适合 demo，不适合 graph-based workflow。

### 目标目录结构示例

```text
backend/
├── src/
│   ├── api/
│   │   ├── app.py
│   │   ├── routes_health.py
│   │   └── routes_research.py
│   ├── application/
│   │   ├── dtos/
│   │   │   ├── requests.py
│   │   │   ├── responses.py
│   │   │   └── events.py
│   │   ├── services/
│   │   │   ├── research_runner.py
│   │   │   └── event_translator.py
│   │   └── usecases/
│   │       ├── start_research.py
│   │       └── resume_research.py
│   ├── domain/
│   │   ├── models/
│   │   │   ├── task.py
│   │   │   ├── source.py
│   │   │   ├── report.py
│   │   │   └── session.py
│   │   └── policies/
│   │       ├── routing.py
│   │       └── retries.py
│   ├── infrastructure/
│   │   ├── llm/
│   │   │   ├── factory.py
│   │   │   └── openai_compatible.py
│   │   ├── persistence/
│   │   │   ├── checkpoint.py
│   │   │   ├── repositories.py
│   │   │   └── sqlite.py
│   │   ├── search/
│   │   │   ├── providers.py
│   │   │   ├── fetcher.py
│   │   │   └── normalizer.py
│   │   ├── memory/
│   │   │   ├── vector_store.py
│   │   │   ├── note_store.py
│   │   │   └── retriever.py
│   │   └── observability/
│   │       ├── logging.py
│   │       └── tracing.py
│   ├── graph/
│   │   ├── builder.py
│   │   ├── state.py
│   │   ├── reducers.py
│   │   ├── nodes/
│   │   │   ├── ingest.py
│   │   │   ├── planner.py
│   │   │   ├── router.py
│   │   │   ├── search.py
│   │   │   ├── summarize.py
│   │   │   ├── evaluate.py
│   │   │   ├── report.py
│   │   │   └── persist.py
│   │   └── subgraphs/
│   │       └── task_execution.py
│   ├── prompts/
│   │   ├── planner.py
│   │   ├── summarizer.py
│   │   └── reporter.py
│   ├── tools/
│   │   ├── note_tools.py
│   │   ├── search_tools.py
│   │   └── rag_tools.py
│   ├── settings.py
│   └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── docs/
    └── langgraph-refactor-plan.md
```

## 6.2 核心代码组织如何调整

### 当前

- orchestrator 在 `agent.py`
- service 与 orchestration 混杂

### 目标

- `graph/` 负责 workflow
- `application/` 负责请求入口与响应适配
- `infrastructure/` 负责外部依赖
- `domain/` 负责核心对象与规则

### 原则

- graph node 不直接关心 FastAPI
- API 层不直接知道 prompt 细节
- infrastructure 不直接修改 graph state

## 6.3 数据结构如何调整

建议拆分以下对象：

### `ResearchRequest`

- `topic`
- `search_backend`
- `session_id`
- `thread_id`
- `user_id`
- `stream`

### `ResearchTask`

- `task_id`
- `title`
- `intent`
- `query`
- `status`
- `attempt`
- `last_error`
- `note_ref`
- `summary`
- `coverage_score`
- `needs_followup`

### `SourceDocument`

- `source_id`
- `title`
- `url`
- `snippet`
- `raw_content`
- `provider`
- `retrieved_at`
- `task_id`
- `score`

### `ResearchArtifact`

- `artifact_id`
- `artifact_type`
- `task_id`
- `content`
- `metadata`

### `ResearchReport`

- `topic`
- `executive_summary`
- `sections`
- `citations`
- `risks`
- `gaps`
- `markdown`

## 6.4 运行机制如何调整

### 当前运行机制

- 请求到来
- new `DeepResearchAgent`
- 内存跑完整个流程

### 目标运行机制

- 请求到来
- 构造 graph config
- 绑定 `thread_id`
- `graph.invoke()` 或 `graph.stream()`
- state 自动 checkpoint
- 中断可恢复

### 推荐

MVP 就把 `thread_id` 放进 `RunnableConfig.configurable`

例如：

```python
config = {
    "configurable": {
        "thread_id": thread_id,
        "session_id": session_id,
    }
}
```

## 6.5 工具调用如何改造

### 建议原则

| 当前动作 | 建议方式 |
|---|---|
| Web search | 作为显式 node，必要时也可封成 tool |
| Note read/write | 从“模型决定何时读写”迁移为“节点显式落地” |
| Source normalize | 纯函数，不做 tool |
| Report persist | 纯节点，不做 tool |
| Gap analysis | agent node 或 structured LLM node |

### 为什么 note 不宜继续完全依赖 LLM 调用

因为 note 落地属于：

- 强副作用
- 需要幂等
- 需要准确时机
- 需要可恢复

这类动作更适合作为 graph node 显式执行，而不是靠模型在 prompt 中“自觉”发 tool call。

## 6.6 会话状态如何迁移

### 当前

- 单次请求内 `SummaryState`

### 迁移方案

1. 先把 `SummaryState` 字段映射到新的 `ResearchGraphState`
2. 用 `thread_id` 建立会话关联
3. 用 checkpointer 保存中间态
4. note 文件保留为 artifact，不再承担唯一状态来源

### 迁移建议

短期内：

- 允许 `backend/notes` 继续存在
- 但其角色从“状态源”降级为“产物副本”

## 6.7 错误处理、fallback、重试、streaming 如何设计

### 错误处理

分 4 层：

1. provider error
   - 模型/搜索 API 错误
2. node error
   - 某任务搜索失败
3. graph routing error
   - 路由状态不完整
4. output translation error
   - SSE 转换失败

### fallback

建议：

- 搜索优先级
  - `perplexity -> tavily -> duckduckgo -> searxng`
- 模型优先级
  - 主模型失败时可退到低成本 summarizer/reporter 模型

### 重试

建议配置：

- 搜索节点：指数退避 `2~3` 次
- 报告节点：`1~2` 次
- 持久化节点：`3` 次

### streaming

建议不要让 graph 直接产出前端协议，而是：

```text
LangGraph event
-> internal domain event
-> SSE translator
-> frontend event
```

这样以后即便前端协议调整，也不会影响 graph 内部设计。

## 6.8 checkpointer 如何接入

### MVP 建议

- 开发环境：`SQLite`
- 生产环境：`Postgres`

### 接入点

- 在 `graph/builder.py` 中 compile graph 时接入 checkpointer

### 保存内容

- graph state
- 当前节点位置
- 中间任务状态
- 错误上下文

### 不建议放进 checkpoint 的内容

- 大块网页原文全文
- 冗长 UI 派生字段
- 可从 artifact store 重新读取的大对象

### 建议做法

- checkpoint 保存索引/引用
- 原始大文本存 artifact storage

---

## 七、推荐技术栈

## 7.1 LangGraph

**推荐：必须引入**

用途：

- workflow orchestration
- state transitions
- conditional routing
- checkpoint / resume
- subgraph

### 推荐使用边界

- 让 LangGraph 只负责“流程与状态”
- 不要让它承担所有 infrastructure 细节

## 7.2 LangChain

**推荐：轻量使用，不要过度耦合**

建议只用在：

- model wrappers
- structured output
- retriever / vector store integration
- tool definition

不建议：

- 把所有 domain service 都改造成 LangChain chain

## 7.3 FastAPI

**推荐：继续保留**

原因：

- 当前 API 层已经够轻
- 与 SSE 集成简单
- 非阻塞/异步友好

## 7.4 Pydantic / TypedDict / dataclass

**推荐组合：`TypedDict + Pydantic`**

建议：

- graph state：`TypedDict`
- 复杂子结构：`Pydantic BaseModel`
- 简单纯值对象：`dataclass` 也可，但不再作为 graph 主状态

## 7.5 SQLite / Redis / Postgres / MySQL

### MVP

- `SQLite`
  - 适合本地开发
  - 适合 checkpointer

### 进阶

- `Postgres`
  - 推荐作为主持久化数据库
  - 存 session、run、task、artifact metadata、checkpoint backend

### Redis

适合：

- 短期缓存
- 并发队列
- 事件缓冲

不建议把 Redis 当主持久化数据库。

### MySQL

可用，但若从 LangGraph / AI 工程生态兼容性考虑，**优先 Postgres**。

## 7.6 Qdrant / FAISS / Milvus

### MVP

- 不必一开始上向量库

### 中期推荐

- `Qdrant`

原因：

- Python 集成成熟
- 和 LangChain/LangGraph 配套顺手
- 本地和服务化都方便

### FAISS

适合：

- 本地单机原型

### Milvus

适合：

- 大规模、专门的向量平台

对当前项目而言偏重。

## 7.7 LangSmith / tracing / logging

### 推荐

- 本地：`structlog/loguru + OpenTelemetry-like trace id`
- 研发环境：`LangSmith`

### 最低要求

必须有：

- `run_id`
- `thread_id`
- `task_id`
- `node_name`
- `latency_ms`
- `provider`
- `retry_count`

## 7.8 OpenAI-compatible model client

推荐：

- `langchain_openai.ChatOpenAI`
- 自己封一层 `ModelFactory`

原因：

- 当前已经有 OpenAI-compatible 接口需求
- 后续支持 Ollama / vLLM / LM Studio / custom endpoint 都方便

## 7.9 RAG pipeline 相关组件

推荐最小闭环：

- text splitter
- embeddings
- vector store
- retriever
- reranker 可选
- citation normalizer

建议不要一开始就把 RAG 混进 planner 主流程。  
应先作为：

- source recall 补充
- task evidence augmentation

---

## 八、目标状态设计建议

## 8.1 顶层 State 建议

```python
from typing import TypedDict, Literal, NotRequired

class ResearchGraphState(TypedDict):
    session_id: str
    thread_id: str
    run_id: str
    topic: str
    user_query: str
    status: Literal["pending", "running", "needs_input", "completed", "failed"]
    current_stage: str
    planner_version: str
    tasks: list[dict]
    task_order: list[str]
    active_task_id: NotRequired[str]
    sources_by_task: dict[str, list[dict]]
    summaries_by_task: dict[str, str]
    artifacts_by_task: dict[str, list[str]]
    global_findings: list[str]
    coverage_gaps: list[str]
    report_markdown: str
    retries: dict[str, int]
    last_error: NotRequired[dict]
    ui_events: list[dict]
```

## 8.2 为什么不建议继续沿用当前 `SummaryState`

因为它缺失以下关键域：

- 会话标识
- 节点阶段
- 任务级错误
- 重试计数
- gap 状态
- artifact 引用
- 可恢复元数据

## 8.3 任务子状态建议

```python
class TaskState(BaseModel):
    task_id: str
    title: str
    intent: str
    query: str
    status: str
    attempt: int = 0
    note_id: str | None = None
    source_ids: list[str] = []
    summary: str | None = None
    coverage_score: float | None = None
    needs_followup: bool = False
    last_error: str | None = None
```

---

## 九、Graph / Node 设计建议

## 9.1 第一阶段目标图

```text
START
  -> ingest_request
  -> plan_tasks
  -> dispatch_tasks
      -> task_subgraph(task_1)
      -> task_subgraph(task_2)
      -> task_subgraph(task_3)
  -> evaluate_coverage
  -> maybe_replan
  -> compile_report
  -> persist_artifacts
  -> END
```

## 9.2 Task Subgraph 建议

```text
task_start
  -> search_web
  -> normalize_sources
  -> summarize_task
  -> persist_task_note
  -> task_done
```

如需更强能力，可扩成：

```text
task_start
  -> search_web
  -> assess_results
  -> if insufficient -> refine_query -> search_web
  -> summarize_task
  -> persist_task_note
  -> task_done
```

这时 `max_web_research_loops` 才真正变成 graph loop control，而不是摆设配置。

## 9.3 节点职责建议

### `ingest_request`

- 校验输入
- 生成 `run_id/thread_id`
- 初始化 state

### `plan_tasks`

- 调 planner model
- 产出结构化任务列表

### `dispatch_tasks`

- 决定任务执行顺序
- 是否并行

### `search_web`

- 调搜索 provider
- 返回结构化 sources

### `normalize_sources`

- 去重
- 切片
- citation 标准化

### `summarize_task`

- 使用 task prompt
- 产出任务总结

### `evaluate_coverage`

- 判断是否有信息缺口
- 决定是否 replan

### `compile_report`

- 聚合任务产物
- 生成最终 markdown report

### `persist_artifacts`

- 写 note
- 写 artifact
- 写最终报告

## 9.4 哪些节点适合 agent，哪些适合 deterministic code

| 节点 | 建议 |
|---|---|
| 任务规划 | agent/LLM |
| 查询改写 | agent/LLM |
| Web search | deterministic code |
| 去重/裁剪/规范化 | deterministic code |
| 证据覆盖评估 | LLM + structured output |
| 报告汇总 | LLM |
| 持久化 | deterministic code |

---

## 十、工具调用与异常处理改造建议

## 10.1 工具调用改造原则

### 当前问题

- 工具调用由 prompt 强约束触发
- 副作用操作依赖模型服从性

### 目标原则

- side effect 尽量不由 LLM 自主触发
- 节点内显式调用 repository/service
- agent tool 只留给真正不确定的动作

## 10.2 异常处理设计

建议定义统一异常层：

- `ProviderError`
- `ToolExecutionError`
- `RetryableNodeError`
- `FatalWorkflowError`
- `PersistenceError`

并让每个节点明确标记：

- 是否可重试
- 是否可回退
- 是否可降级

## 10.3 重试策略建议

| 节点 | 重试 | fallback |
|---|---|---|
| search_web | 2-3 次 | 切后端 |
| summarize_task | 1-2 次 | 降模型 |
| compile_report | 1 次 | 简化模板 |
| persist_artifacts | 3 次 | 本地临时文件 |

## 10.4 Streaming 设计

当前前端已经消费以下事件类型：

- `status`
- `todo_list`
- `task_status`
- `sources`
- `task_summary_chunk`
- `tool_call`
- `final_report`
- `done`

建议新后端内部事件改为：

- `graph.node.started`
- `graph.node.completed`
- `task.updated`
- `artifact.created`
- `report.updated`
- `workflow.error`

然后由 translator 转为现有前端事件，保证平滑迁移。

---

## 十一、记忆 / RAG / 持久化集成建议

## 11.1 记忆架构建议

建议分三层：

### 短期记忆

- graph checkpoint state
- 当前会话 task progress

### 工作产物记忆

- task notes
- report drafts
- citations

### 长期语义记忆

- 过往研究主题
- 已确认结论
- 常用资料摘要

## 11.2 当前 NoteTool 如何处理

### 建议

**保留，但降级为 artifact sink，不再作为主流程唯一状态源。**

理由：

- note 文件适合人读
- 不适合作为 durable workflow runtime 的唯一真相源

## 11.3 RAG 集成建议

引入顺序建议：

1. 先完成 Web 搜索工作流 graph 化
2. 再加入本地文档检索
3. 再做历史研究 artifact 向量召回

### 不建议

不要一开始就把：

- Web 搜索
- 本地知识库
- 历史研究 memory

全部混成一个 retrieval 黑盒。  
应保留来源维度，便于后续可解释性和 citation。

## 11.4 持久化建议

### 最低配置

- SQLite
  - checkpoint
  - session/run/task metadata

### 推荐配置

- Postgres
  - 主库
- Qdrant
  - vector store
- 本地或对象存储
  - artifact raw text / exported report

---

## 十二、分阶段路线图

## Phase 0：基线固化

### 目标

- 冻结当前行为
- 建立回归样本

### 工作项

- 为当前 `/research` 输出建立 golden cases
- 保存 3-5 个代表性研究主题
- 记录现有 SSE 事件序列

### 交付物

- `tests/fixtures/research_cases/*.json`
- `tests/integration/test_current_workflow.py`
- 当前输出快照

## Phase 1：MVP Graph 化

### 目标

- 用 LangGraph 重写主流程
- 保持 API 不变

### 范围

- 单图
- 无 RAG
- 无复杂重规划
- 支持 checkpoint

### 工作项

- 建立 `graph/state.py`
- 建立 `graph/builder.py`
- 实现 planner/search/summarize/report 节点
- 让 FastAPI 改调 graph runner

### 交付物

- LangGraph 主执行链
- `/research` 行为对齐
- `/research/stream` 基础兼容

## Phase 2：状态持久化与恢复

### 目标

- 支持断点恢复
- 支持 run replay

### 工作项

- 接入 SQLite / Postgres checkpointer
- 增加 `thread_id/session_id/run_id`
- 支持 resume API

### 交付物

- checkpoint backend
- resume 能力
- run metadata 查询能力

## Phase 3：任务子图与并发

### 目标

- 任务执行从手工线程升级为图分支/子图

### 工作项

- 建 `task_execution` subgraph
- 引入 task reducer
- 统一任务状态汇合

### 交付物

- task subgraph
- 更稳健的并发任务执行
- 可追踪 task lifecycle

## Phase 4：Coverage / Replan / HITL

### 目标

- 真正形成 research loop

### 工作项

- 增加 coverage evaluation node
- 增加 query refine / replan node
- 增加人工确认节点

### 交付物

- 条件路由
- 循环式研究图
- 人工介入机制

## Phase 5：RAG / 长期记忆 / 观测

### 目标

- 进入产品级后端形态

### 工作项

- 接入 vector store
- 接入长期 artifact retrieval
- 接 LangSmith / tracing

### 交付物

- RAG augmentation
- 长期研究记忆
- 完整链路观测

---

## 十三、每阶段交付物清单

| 阶段 | 交付物 |
|---|---|
| Phase 0 | 基线测试、输出快照、事件快照 |
| Phase 1 | LangGraph MVP、兼容现有 API |
| Phase 2 | checkpoint、resume、session metadata |
| Phase 3 | task subgraph、并发汇合 |
| Phase 4 | coverage/replan/HITL |
| Phase 5 | RAG、memory、tracing |

---

## 十四、风险与规避策略

## 14.1 风险：一次性重写导致功能回归

### 规避

- 先做 Phase 0 快照
- 保持 API contract 不变
- 分阶段替换 orchestrator

## 14.2 风险：前端事件协议失配

### 规避

- 引入 event translator
- graph 内部事件与 SSE 协议解耦

## 14.3 风险：RAG 提前引入导致复杂度过高

### 规避

- MVP 不上 RAG
- 先解决 workflow 和 persistence

## 14.4 风险：把所有动作都 tool 化，导致图与 agent 责任不清

### 规避

- 先定义 deterministic node 边界
- 再定义 agent 可调用的有限工具

## 14.5 风险：状态对象膨胀

### 规避

- state 中只放必要索引和中间结论
- 大对象落 artifact store

---

## 十五、最终实施建议

## 15.1 最推荐的实施方案

**推荐方案：**

- 保留现有前端与 FastAPI 入口
- 在后端新建 `graph/` 目录
- 第一阶段完全不碰前端交互协议
- 用 LangGraph 替换 `DeepResearchAgent`
- 保留现有 prompt 与 search service
- 把 note 写入从“模型自主调用”迁移为“节点显式持久化”

## 15.2 对当前代码的明确处理建议

### 保留

- `backend/src/main.py` 的 API 形式
- `backend/src/prompts.py` 的业务 prompt 资产
- `backend/src/services/search.py` 中的 provider 调用逻辑
- 前端 SSE 处理逻辑

### 适配

- `config.py`
- `models.py`
- `services/reporter.py`
- `services/summarizer.py`
- `services/planner.py`

### 重写

- `backend/src/agent.py`
- 流式事件生产方式
- 状态生命周期
- 任务并发执行模型
- 会话恢复与持久化

## 15.3 一个现实可执行的开工顺序

1. 新建 `graph/state.py` 和 `graph/builder.py`
2. 先实现 `plan -> search -> summarize -> report` 单图
3. 用现有 API 调这个图
4. 接 SQLite checkpointer
5. 再拆 task subgraph
6. 最后引入 replan / memory / RAG

## 15.4 最终结论

如果你的目标只是“跑通一个研究 demo”，当前架构还能继续凑合。  
如果你的目标是“把 DeepResearcher 演进成真正可维护的多智能体研究工作流后端”，**现在就是切 LangGraph 的合适时机。**

核心理由不是追新，而是：

- 当前流程已经具备 graph 化价值
- 当前状态已经到了必须持久化的临界点
- 当前多角色协作已经需要显式路由和恢复机制
- 当前 `agent.py` 已经成为未来扩展的主要阻力

**因此，建议采用“保留 API 与资产、重写编排层、逐步迁移状态与持久化”的 LangGraph 重构路线。**

---

## 十六、附：基于仓库常见实现推断的事项

以下判断带有“基于仓库常见实现推断”性质，后续重构前建议再做一次源码核实：

1. 当前 `Thread + shared llm/search tool` 的并发方式在线程安全与资源隔离上存在潜在边界风险
2. 后续若直接引入真正多智能体协商，当前 `SummaryState` 很快会失控
3. 若继续把 note/read/write 完全交给模型自治，副作用一致性会成为主要问题
4. 如果未来研究任务规模增大，仅靠 markdown notes 无法承担主状态源职责

