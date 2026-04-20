# DeepResearcher Bug 解决记录

## 1. `.env` 配置不生效

### 现象

- `.env` 已配置阿里兼容模型
- 运行时却回退到默认的 `ollama + llama3.2`

### 原因

- 后端之前没有自动加载 `backend/.env`

### 解决

- 在 [backend/src/config.py](F:\Project\agent\DeepResearcher\backend\src\config.py) 中加入 `load_dotenv()`
- 现在启动时会自动读取 `.env`

## 2. Windows 下搜索工具导入时编码崩溃

### 现象

报错类似：

```text
UnicodeEncodeError: 'gbk' codec can't encode character ...
```

### 原因

- 第三方搜索工具初始化时输出 Unicode 日志
- Windows 控制台默认 `gbk` 编码无法处理

### 解决

- 在 [backend/src/services/search.py](F:\Project\agent\DeepResearcher\backend\src\services\search.py) 中先处理输出编码

## 3. 前端失败后仍显示“研究流程完成”

### 现象

- 页面日志已经提示“研究失败”
- 顶部状态仍显示“研究流程完成”

### 原因

- 旧前端只根据 `loading` 判断状态

### 解决

- 旧的单次研究状态机已废弃
- 前端改为基于 `sessionList + activeSessionId + activeSessionDetail` 的 session 模式
- 失败、完成、草稿、运行中均由后端持久化状态驱动

## 4. 新建研究会把旧研究从左侧清空

### 现象

- 完成一个研究后点击“开始新研究”
- 之前的主题会从左侧消失

### 根因

- 旧前端只有一份内存态 `form / todoTasks / reportMarkdown`
- “开始新研究”本质是在清空当前页面状态，而不是创建新的研究 session
- 后端没有持久化研究历史，因此刷新或重启后无法恢复

### 解决

- 新增 SQLite 持久化存储
- 新增 `research_sessions / research_tasks / research_steps / research_reports / research_tool_calls`
- 前端左侧历史列表改为读取 `GET /api/research/sessions`
- 点击左侧任意历史项改为读取 `GET /api/research/sessions/{id}`
- “开始新研究”改为 `POST /api/research/sessions` 创建新的 draft session

## 5. SQLite 数据库连接未关闭，Windows 下文件被锁住

### 现象

- 自动化测试或重启场景下，临时数据库文件无法删除
- Windows 提示文件被占用

### 原因

- `sqlite3.Connection` 用 `with` 只会提交/回滚，不会自动关闭连接

### 解决

- 在 [backend/src/session_store.py](F:\Project\agent\DeepResearcher\backend\src\session_store.py) 中增加真正会关闭连接的 `_connection()` 上下文

## 6. 规划阶段的 `tool_call` 被误记成正式任务

### 现象

- 真实研究完成后，任务总数虚高
- 左侧进度可能显示异常，例如 `5 / 1`

### 原因

- 后端曾把任意带 `task_id` 的 `tool_call` 都自动 upsert 为 `research_tasks`
- 规划阶段创建 note 的工具调用也会带 `task_id`
- 这类“规划笔记”不应该被当成真正的研究任务

### 解决

- 调整 [backend/src/session_store.py](F:\Project\agent\DeepResearcher\backend\src\session_store.py)
- `tool_call` 仅写入 `research_tool_calls`
- 只有对应任务已存在时，才回填任务笔记元数据

## 7. 真实链路验证结果

### 已确认成功

- `tavily + MiniMax-M2.1` 可完成真实研究
- session 状态可落库为 `completed`
- 左侧历史摘要中的任务总数和完成数已恢复正确

### 已确认失败但可恢复

- `duckduckgo` 在某些主题下会返回 `No results found`
- session 状态会落库为 `failed`
- 失败历史仍会保留在左侧，可点击查看详情和错误信息
