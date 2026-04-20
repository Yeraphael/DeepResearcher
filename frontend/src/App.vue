<template>
  <main class="app-shell">
    <aside class="sidebar">
      <header class="brand-card">
        <div class="brand-icon">深</div>
        <div>
          <p class="eyebrow">DeepResearcher</p>
          <h1>深度研究助手</h1>
          <p class="brand-copy">
            左侧永久保留历史主题，点击任意 session 即可恢复完整研究详情。
          </p>
        </div>
      </header>

      <button
        type="button"
        class="primary-btn create-btn"
        @click="createNewSession"
        :disabled="sessionCreating || hasRunningSession"
      >
        {{ sessionCreating ? "正在创建..." : "＋ 开始新研究" }}
      </button>

      <p v-if="sidebarMessage" class="sidebar-message">
        {{ sidebarMessage }}
      </p>

      <section class="sidebar-section">
        <div class="section-head">
          <h2>历史研究</h2>
          <span>{{ sessionList.length }}</span>
        </div>

        <div v-if="sessionListLoading && !sessionList.length" class="empty-card">
          正在加载历史研究...
        </div>
        <div v-else-if="!sessionList.length" class="empty-card">
          还没有历史研究。先创建一个新的 research session，之后它会持久化保存在后端 SQLite 中。
        </div>
        <ul v-else class="session-list">
          <li v-for="session in sessionList" :key="session.id">
            <button
              type="button"
              class="session-card"
              :class="[
                `session-${session.status}`,
                { active: session.id === activeSessionId }
              ]"
              @click="selectSession(session.id)"
            >
              <div class="session-card-head">
                <span class="status-badge" :class="statusClass(session.status)">
                  {{ formatSessionStatus(session.status) }}
                </span>
                <span class="session-time">{{ formatDateTime(session.updated_at) }}</span>
              </div>

              <h3>{{ session.display_topic }}</h3>
              <p class="session-progress">{{ formatSessionProgress(session) }}</p>

              <p v-if="session.report_excerpt" class="session-excerpt">
                {{ session.report_excerpt }}
              </p>
              <p v-if="session.error_message" class="session-error">
                {{ session.error_message }}
              </p>
            </button>
          </li>
        </ul>
      </section>
    </aside>

    <section class="workspace">
      <div v-if="detailLoading && !activeSessionDetail" class="center-card">
        正在加载研究详情...
      </div>

      <div v-else-if="activeSessionDetail" class="workspace-inner">
        <header class="workspace-header">
          <div>
            <p class="eyebrow">Research Session #{{ activeSessionDetail.id }}</p>
            <h2>{{ activeSessionDetail.display_topic }}</h2>
            <p class="meta-line">
              创建于 {{ formatDateTime(activeSessionDetail.created_at) }}
              <span v-if="activeSessionDetail.started_at">
                · 开始于 {{ formatDateTime(activeSessionDetail.started_at) }}
              </span>
              <span v-if="activeSessionDetail.completed_at">
                · 完成于 {{ formatDateTime(activeSessionDetail.completed_at) }}
              </span>
            </p>
          </div>

          <div class="header-actions">
            <span class="status-chip" :class="statusClass(activeSessionDetail.status)">
              {{ formatSessionStatus(activeSessionDetail.status) }}
            </span>
            <button
              type="button"
              class="ghost-btn"
              @click="reloadActiveSession"
              :disabled="detailLoading"
            >
              {{ detailLoading ? "刷新中..." : "刷新详情" }}
            </button>
            <button
              v-if="isRunningActiveSession"
              type="button"
              class="ghost-btn danger-btn"
              @click="disconnectStream"
            >
              断开实时连接
            </button>
          </div>
        </header>

        <section v-if="isDraftSession" class="card composer-card">
          <div class="card-head">
            <div>
              <h3>配置并启动研究</h3>
              <p>点击“开始研究”会在当前 draft session 内正式运行，并保留所有历史 session。</p>
            </div>
          </div>

          <label class="field">
            <span>研究主题</span>
            <textarea
              v-model="draftForm.topic"
              rows="4"
              placeholder="例如：截至 2026 年全球多模态模型的竞争格局"
            ></textarea>
          </label>

          <div class="field-row">
            <label class="field">
              <span>搜索引擎</span>
              <select v-model="draftForm.searchApi">
                <option value="">沿用后端配置</option>
                <option v-for="option in searchOptions" :key="option" :value="option">
                  {{ option }}
                </option>
              </select>
            </label>
          </div>

          <div class="composer-actions">
            <button
              type="button"
              class="primary-btn"
              @click="runActiveSession"
              :disabled="runSubmitting || !draftForm.topic.trim()"
            >
              {{ runSubmitting ? "研究进行中..." : "开始研究" }}
            </button>
            <button
              type="button"
              class="ghost-btn"
              @click="reloadActiveSession"
              :disabled="detailLoading"
            >
              恢复草稿
            </button>
          </div>
        </section>

        <p v-if="activeSessionDetail.error_message" class="error-banner">
          {{ activeSessionDetail.error_message }}
        </p>

        <div class="workspace-grid">
          <div class="grid-column side-column">
            <section class="card">
              <div class="card-head">
                <h3>流程记录</h3>
                <button
                  type="button"
                  class="ghost-btn small-btn"
                  @click="logsCollapsed = !logsCollapsed"
                  :disabled="!activeSessionDetail.progress_logs.length"
                >
                  {{ logsCollapsed ? "展开" : "收起" }}
                </button>
              </div>

              <ul
                v-if="!logsCollapsed && activeSessionDetail.progress_logs.length"
                class="timeline-list"
              >
                <li
                  v-for="(log, index) in activeSessionDetail.progress_logs"
                  :key="`${log}-${index}`"
                >
                  <span class="timeline-dot"></span>
                  <p>{{ log }}</p>
                </li>
              </ul>
              <p v-else class="muted">
                {{ logsCollapsed ? "流程记录已收起。" : "当前还没有流程记录。" }}
              </p>
            </section>

            <section class="card">
              <div class="card-head">
                <h3>任务清单</h3>
                <span class="task-counter">
                  {{ activeSessionDetail.completed_tasks }} / {{ activeSessionDetail.total_tasks || 0 }}
                </span>
              </div>

              <ul v-if="sessionTasks.length" class="task-list">
                <li v-for="task in sessionTasks" :key="task.id">
                  <button
                    type="button"
                    class="task-card"
                    :class="[
                      `task-${task.status}`,
                      { active: task.id === selectedTaskId }
                    ]"
                    @click="selectedTaskId = task.id"
                  >
                    <div class="task-card-head">
                      <span>{{ task.title }}</span>
                      <span class="status-badge" :class="statusClass(task.status)">
                        {{ formatTaskStatus(task.status) }}
                      </span>
                    </div>
                    <p>{{ task.intent || task.query || "暂无任务说明" }}</p>
                  </button>
                </li>
              </ul>
              <p v-else class="muted">
                {{ isDraftSession ? "等待启动研究后生成任务。" : "当前 session 暂无任务。" }}
              </p>
            </section>
          </div>

          <div class="grid-column main-column">
            <section class="card report-card">
              <div class="card-head">
                <div>
                  <h3>最终报告</h3>
                  <p class="muted">持久化保存在数据库中，点击历史 session 可完整恢复。</p>
                </div>
                <div class="meta-chip-row">
                  <span v-if="activeSessionDetail.report_note_id" class="meta-chip">
                    笔记：{{ activeSessionDetail.report_note_id }}
                  </span>
                  <span v-if="activeSessionDetail.report_note_path" class="meta-chip path-chip">
                    {{ activeSessionDetail.report_note_path }}
                  </span>
                </div>
              </div>

              <pre v-if="activeSessionDetail.report_markdown" class="pre-block report-pre">{{
                activeSessionDetail.report_markdown
              }}</pre>
              <p v-else class="muted">
                {{ isDraftSession ? "填写主题并启动研究后，这里会展示最终报告。" : "该研究尚未生成最终报告。" }}
              </p>
            </section>

            <section class="card">
              <div class="card-head">
                <div>
                  <h3>任务详情</h3>
                  <p class="muted" v-if="currentTask">
                    当前查看任务 #{{ currentTask.id }}
                  </p>
                </div>
              </div>

              <template v-if="currentTask">
                <div class="detail-stack">
                  <section class="detail-block">
                    <h4>{{ currentTask.title }}</h4>
                    <p class="muted">{{ currentTask.intent || "暂无任务说明" }}</p>
                    <div class="meta-chip-row">
                      <span class="meta-chip">查询：{{ currentTask.query || "暂无" }}</span>
                      <span v-if="currentTask.note_id" class="meta-chip">
                        笔记：{{ currentTask.note_id }}
                      </span>
                      <span v-if="currentTask.note_path" class="meta-chip path-chip">
                        {{ currentTask.note_path }}
                      </span>
                    </div>
                  </section>

                  <section v-if="currentTask.notices.length" class="detail-block">
                    <h4>系统提示</h4>
                    <ul class="bullet-list">
                      <li v-for="(notice, index) in currentTask.notices" :key="`${notice}-${index}`">
                        {{ notice }}
                      </li>
                    </ul>
                  </section>

                  <section class="detail-block">
                    <h4>来源摘要</h4>
                    <ul v-if="currentTaskSourceItems.length" class="source-list">
                      <li v-for="(item, index) in currentTaskSourceItems" :key="`${item.url}-${index}`">
                        <a
                          v-if="item.url"
                          :href="item.url"
                          class="source-link"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {{ item.title || item.url }}
                        </a>
                        <span v-else>{{ item.title }}</span>
                        <p v-if="item.snippet" class="muted">{{ item.snippet }}</p>
                      </li>
                    </ul>
                    <pre v-else-if="currentTask.sources_summary" class="pre-block compact-pre">{{
                      currentTask.sources_summary
                    }}</pre>
                    <p v-else class="muted">暂无来源摘要。</p>
                  </section>

                  <section class="detail-block">
                    <h4>任务总结</h4>
                    <pre class="pre-block compact-pre">{{
                      currentTask.summary || "暂无任务总结。"
                    }}</pre>
                  </section>

                  <section v-if="currentTaskToolCalls.length" class="detail-block">
                    <h4>工具调用</h4>
                    <ul class="tool-list">
                      <li v-for="toolCall in currentTaskToolCalls" :key="toolCall.id">
                        <div class="tool-call-head">
                          <strong>{{ toolCall.agent }} → {{ toolCall.tool }}</strong>
                          <span class="muted">{{ formatDateTime(toolCall.created_at) }}</span>
                        </div>
                        <pre class="pre-block compact-pre">{{
                          formatJson(toolCall.parameters)
                        }}</pre>
                        <pre
                          v-if="toolCall.result"
                          class="pre-block compact-pre"
                        >{{ toolCall.result }}</pre>
                      </li>
                    </ul>
                  </section>
                </div>
              </template>
              <p v-else class="muted">
                当前 session 还没有任务详情。
              </p>
            </section>
          </div>
        </div>
      </div>

      <div v-else class="center-card">
        <p class="eyebrow">Persisted Research Sessions</p>
        <h2>深度研究历史已改造成后端持久化模式</h2>
        <p>
          点击左侧任意研究主题即可恢复完整详情，或者新建一个 draft session 再启动新的研究。
        </p>
        <button
          type="button"
          class="primary-btn"
          @click="createNewSession"
          :disabled="sessionCreating"
        >
          {{ sessionCreating ? "正在创建..." : "开始新研究" }}
        </button>
      </div>
    </section>
  </main>
</template>

<script lang="ts" setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import {
  createResearchSession,
  getResearchSession,
  listResearchSessions,
  runResearchSessionStream,
  type ResearchSessionDetail,
  type ResearchSessionSummary,
  type ResearchStreamEvent,
  type ResearchTask,
  type ResearchToolCall
} from "./services/api";

interface SourceItem {
  title: string;
  url: string;
  snippet: string;
}

const searchOptions = [
  "advanced",
  "duckduckgo",
  "tavily",
  "perplexity",
  "searxng"
];

const sessionList = ref<ResearchSessionSummary[]>([]);
const sessionDetails = reactive<Record<number, ResearchSessionDetail>>({});
const activeSessionId = ref<number | null>(null);
const selectedTaskId = ref<number | null>(null);
const logsCollapsed = ref(false);

const sessionListLoading = ref(false);
const detailLoading = ref(false);
const sessionCreating = ref(false);
const runSubmitting = ref(false);
const runningSessionId = ref<number | null>(null);
const sidebarMessage = ref("");

const draftForm = reactive({
  topic: "",
  searchApi: ""
});

let currentController: AbortController | null = null;
let localStepId = -1;

const activeSessionDetail = computed(() => {
  if (activeSessionId.value === null) {
    return null;
  }
  return sessionDetails[activeSessionId.value] ?? null;
});

const sessionTasks = computed(() => activeSessionDetail.value?.tasks ?? []);
const currentTask = computed(() => {
  const tasks = sessionTasks.value;
  if (!tasks.length) {
    return null;
  }

  if (selectedTaskId.value !== null) {
    return tasks.find((task) => task.id === selectedTaskId.value) ?? tasks[0];
  }

  return tasks[0];
});

const currentTaskToolCalls = computed(() => {
  if (!currentTask.value || !activeSessionDetail.value) {
    return [] as ResearchToolCall[];
  }

  return activeSessionDetail.value.tool_calls.filter(
    (item) => item.task_id === currentTask.value?.id
  );
});

const currentTaskSourceItems = computed(() =>
  parseSourceItems(currentTask.value?.sources_summary ?? "")
);

const isDraftSession = computed(
  () => activeSessionDetail.value?.status === "draft"
);
const isRunningActiveSession = computed(
  () => runningSessionId.value !== null && runningSessionId.value === activeSessionId.value
);
const hasRunningSession = computed(() => runningSessionId.value !== null);

watch(
  () => activeSessionDetail.value,
  (detail) => {
    if (!detail) {
      draftForm.topic = "";
      draftForm.searchApi = "";
      selectedTaskId.value = null;
      return;
    }

    draftForm.topic = detail.topic ?? "";
    draftForm.searchApi = detail.search_api ?? "";

    const taskExists = detail.tasks.some((task) => task.id === selectedTaskId.value);
    if (!taskExists) {
      selectedTaskId.value = detail.tasks[0]?.id ?? null;
    }
  },
  { immediate: true }
);

function buildEmptyDetail(
  summary?: Partial<ResearchSessionSummary> & { id?: number }
): ResearchSessionDetail {
  return {
    id: summary?.id ?? 0,
    topic: summary?.topic ?? "",
    display_topic: summary?.display_topic ?? summary?.topic ?? "未命名研究",
    search_api: summary?.search_api ?? null,
    status: summary?.status ?? "draft",
    error_message: summary?.error_message ?? null,
    created_at: summary?.created_at ?? "",
    updated_at: summary?.updated_at ?? "",
    started_at: summary?.started_at ?? null,
    completed_at: summary?.completed_at ?? null,
    total_tasks: summary?.total_tasks ?? 0,
    completed_tasks: summary?.completed_tasks ?? 0,
    failed_tasks: summary?.failed_tasks ?? 0,
    report_excerpt: summary?.report_excerpt ?? "",
    report_markdown: "",
    report_note_id: null,
    report_note_path: null,
    tasks: [],
    steps: [],
    tool_calls: [],
    progress_logs: []
  };
}

function cacheSessionDetail(detail: ResearchSessionDetail): ResearchSessionDetail {
  const normalized: ResearchSessionDetail = {
    ...detail,
    display_topic: detail.display_topic || detail.topic || "未命名研究",
    tasks: Array.isArray(detail.tasks) ? [...detail.tasks] : [],
    steps: Array.isArray(detail.steps) ? [...detail.steps] : [],
    tool_calls: Array.isArray(detail.tool_calls) ? [...detail.tool_calls] : [],
    progress_logs: Array.isArray(detail.progress_logs) ? [...detail.progress_logs] : []
  };
  sessionDetails[normalized.id] = normalized;
  upsertSessionSummary(normalized);
  return normalized;
}

function upsertSessionSummary(
  detail: ResearchSessionDetail | ResearchSessionSummary
): void {
  const summary: ResearchSessionSummary = {
    id: detail.id,
    topic: detail.topic,
    display_topic: detail.display_topic || detail.topic || "未命名研究",
    search_api: detail.search_api,
    status: detail.status,
    error_message: detail.error_message,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    started_at: detail.started_at,
    completed_at: detail.completed_at,
    total_tasks: detail.total_tasks,
    completed_tasks: detail.completed_tasks,
    failed_tasks: detail.failed_tasks,
    report_excerpt: detail.report_excerpt
  };

  const index = sessionList.value.findIndex((item) => item.id === summary.id);
  if (index === -1) {
    sessionList.value = [summary, ...sessionList.value];
  } else {
    sessionList.value.splice(index, 1, summary);
  }

  sessionList.value = [...sessionList.value].sort((a, b) => {
    const timeA = Date.parse(a.updated_at || a.created_at || "") || 0;
    const timeB = Date.parse(b.updated_at || b.created_at || "") || 0;
    return timeB - timeA || b.id - a.id;
  });
}

function appendProgressLog(detail: ResearchSessionDetail, message: string): void {
  const normalized = message.trim();
  if (!normalized) {
    return;
  }

  const lastItem = detail.progress_logs[detail.progress_logs.length - 1];
  if (lastItem === normalized) {
    return;
  }

  detail.progress_logs.push(normalized);
}

function extractOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function ensureTask(detail: ResearchSessionDetail, taskId: number): ResearchTask {
  const existing = detail.tasks.find((task) => task.id === taskId);
  if (existing) {
    return existing;
  }

  const fallbackTask: ResearchTask = {
    id: taskId,
    title: `任务 ${taskId}`,
    intent: "",
    query: "",
    status: "pending",
    summary: "",
    sources_summary: "",
    notices: [],
    note_id: null,
    note_path: null,
    stream_token: null
  };

  detail.tasks.push(fallbackTask);
  return fallbackTask;
}

function refreshTaskCounts(detail: ResearchSessionDetail): void {
  detail.total_tasks = detail.tasks.length;
  detail.completed_tasks = detail.tasks.filter(
    (task) => task.status === "completed"
  ).length;
  detail.failed_tasks = detail.tasks.filter((task) => task.status === "failed").length;
  detail.report_excerpt = detail.report_markdown
    ? detail.report_markdown.slice(0, 160)
    : "";
  detail.display_topic = detail.topic || "未命名研究";
}

function applyStep(detail: ResearchSessionDetail, event: ResearchStreamEvent): void {
  detail.steps.push({
    id: localStepId--,
    type: typeof event.type === "string" ? event.type : "unknown",
    step:
      typeof event.step === "number"
        ? event.step
        : typeof event.step === "string" && event.step.trim()
        ? Number(event.step)
        : null,
    task_id:
      typeof event.task_id === "number"
        ? event.task_id
        : typeof event.task_id === "string" && event.task_id.trim()
        ? Number(event.task_id)
        : null,
    payload: ensureRecord(event),
    created_at: new Date().toISOString()
  });
}

function applyTaskMetadata(task: ResearchTask, payload: Record<string, unknown>): void {
  const title = extractOptionalString(payload.title);
  const intent = extractOptionalString(payload.intent);
  const query = extractOptionalString(payload.query);
  const summary = extractOptionalString(payload.summary);
  const sourcesSummary = extractOptionalString(payload.sources_summary);
  const noteId = extractOptionalString(payload.note_id);
  const notePath = extractOptionalString(payload.note_path);
  const streamToken = extractOptionalString(payload.stream_token);

  if (title) {
    task.title = title;
  }
  if (intent) {
    task.intent = intent;
  }
  if (query) {
    task.query = query;
  }
  if (summary) {
    task.summary = summary;
  }
  if (sourcesSummary) {
    task.sources_summary = sourcesSummary;
  }
  if (noteId) {
    task.note_id = noteId;
  }
  if (notePath) {
    task.note_path = notePath;
  }
  if (streamToken) {
    task.stream_token = streamToken;
  }
}

function applyStreamEvent(sessionId: number, event: ResearchStreamEvent): void {
  const detail = sessionDetails[sessionId] ?? buildEmptyDetail({ id: sessionId });
  sessionDetails[sessionId] = detail;

  detail.updated_at = new Date().toISOString();
  applyStep(detail, event);

  if (event.type === "status") {
    const message = extractOptionalString(event.message);
    if (message) {
      appendProgressLog(detail, message);
    }

    const taskId = toTaskId(event.task_id);
    if (taskId !== null && message) {
      const task = ensureTask(detail, taskId);
      task.notices.push(message);
    }
  } else if (event.type === "todo_list") {
    const tasks = Array.isArray(event.tasks) ? event.tasks : [];
    detail.tasks = tasks.map((item, index) => {
      const payload = ensureRecord(item);
      const rawId = toTaskId(payload.id) ?? index + 1;
      return {
        id: rawId,
        title: extractOptionalString(payload.title) ?? `任务 ${rawId}`,
        intent: extractOptionalString(payload.intent) ?? "",
        query: extractOptionalString(payload.query) ?? "",
        status: extractOptionalString(payload.status) ?? "pending",
        summary: "",
        sources_summary: "",
        notices: [],
        note_id: extractOptionalString(payload.note_id),
        note_path: extractOptionalString(payload.note_path),
        stream_token: extractOptionalString(payload.stream_token)
      };
    });
    appendProgressLog(
      detail,
      detail.tasks.length ? "已生成任务清单" : "未生成任务清单，使用默认任务继续"
    );
  } else if (event.type === "task_status") {
    const payload = ensureRecord(event);
    const taskId = toTaskId(payload.task_id);
    if (taskId !== null) {
      const task = ensureTask(detail, taskId);
      applyTaskMetadata(task, payload);
      const nextStatus = extractOptionalString(payload.status);
      if (nextStatus) {
        task.status = nextStatus;
      }

      if (task.status === "in_progress") {
        task.summary = "";
        task.sources_summary = "";
        task.notices = [];
        appendProgressLog(detail, `开始执行任务：${task.title}`);
      } else if (task.status === "completed") {
        appendProgressLog(detail, `完成任务：${task.title}`);
      } else if (task.status === "skipped") {
        appendProgressLog(detail, `任务跳过：${task.title}`);
      } else if (task.status === "failed") {
        const failureDetail =
          extractOptionalString(payload.detail) ?? "任务执行失败";
        task.summary = failureDetail;
        detail.status = "failed";
        detail.error_message = failureDetail;
        appendProgressLog(detail, `任务失败：${task.title}`);
      }
    }
  } else if (event.type === "sources") {
    const payload = ensureRecord(event);
    const taskId = toTaskId(payload.task_id);
    if (taskId !== null) {
      const task = ensureTask(detail, taskId);
      const latestSources =
        extractOptionalString(payload.latest_sources) ??
        extractOptionalString(payload.sources_summary) ??
        extractOptionalString(payload.raw_context);
      if (latestSources) {
        task.sources_summary = latestSources;
        appendProgressLog(detail, `已更新任务来源：${task.title}`);
      }
      applyTaskMetadata(task, payload);
    }

    const backend = extractOptionalString(payload.backend);
    if (backend) {
      appendProgressLog(detail, `当前使用搜索后端：${backend}`);
    }
  } else if (event.type === "task_summary_chunk") {
    const payload = ensureRecord(event);
    const taskId = toTaskId(payload.task_id);
    if (taskId !== null) {
      const task = ensureTask(detail, taskId);
      task.summary += typeof payload.content === "string" ? payload.content : "";
      applyTaskMetadata(task, payload);
    }
  } else if (event.type === "tool_call") {
    const payload = ensureRecord(event);
    const taskId = toTaskId(payload.task_id);
    const toolCall: ResearchToolCall = {
      id:
        typeof payload.event_id === "number"
          ? payload.event_id
          : Math.abs(localStepId),
      event_id:
        typeof payload.event_id === "number"
          ? payload.event_id
          : typeof payload.event_id === "string" && payload.event_id.trim()
          ? Number(payload.event_id)
          : null,
      task_id: taskId,
      agent: extractOptionalString(payload.agent) ?? "Agent",
      tool: extractOptionalString(payload.tool) ?? "tool",
      parameters: ensureRecord(payload.parameters),
      result: typeof payload.result === "string" ? payload.result : "",
      note_id: extractOptionalString(payload.note_id),
      note_path: extractOptionalString(payload.note_path),
      created_at: new Date().toISOString()
    };
    detail.tool_calls.push(toolCall);

    if (taskId !== null) {
      const task = ensureTask(detail, taskId);
      applyTaskMetadata(task, payload);
      appendProgressLog(detail, `${toolCall.agent} 调用了 ${toolCall.tool}`);
    } else {
      appendProgressLog(detail, `${toolCall.agent} 调用了 ${toolCall.tool}`);
    }
  } else if (event.type === "report_note") {
    detail.report_note_id = extractOptionalString(event.note_id);
    detail.report_note_path = extractOptionalString(event.note_path);
  } else if (event.type === "final_report") {
    detail.report_markdown =
      extractOptionalString(event.report) ?? "报告生成失败，未获得有效内容";
    detail.report_note_id = extractOptionalString(event.note_id) ?? detail.report_note_id;
    detail.report_note_path =
      extractOptionalString(event.note_path) ?? detail.report_note_path;
    appendProgressLog(detail, "最终报告已生成");
  } else if (event.type === "error") {
    const errorDetail =
      extractOptionalString(event.detail) ?? "研究过程中发生错误";
    detail.status = "failed";
    detail.error_message = errorDetail;
    detail.completed_at = new Date().toISOString();
    appendProgressLog(detail, "研究失败，已停止流程");
  } else if (event.type === "done") {
    if (detail.status !== "failed") {
      detail.status = "completed";
      detail.completed_at = new Date().toISOString();
    }
  }

  if (detail.status === "draft" && event.type !== "todo_list") {
    detail.status = "running";
  }

  refreshTaskCounts(detail);
  upsertSessionSummary(detail);
}

function resetDetailForRun(detail: ResearchSessionDetail): void {
  detail.topic = draftForm.topic.trim();
  detail.display_topic = detail.topic || "未命名研究";
  detail.search_api = draftForm.searchApi || null;
  detail.status = "running";
  detail.error_message = null;
  detail.started_at = new Date().toISOString();
  detail.completed_at = null;
  detail.updated_at = new Date().toISOString();
  detail.report_markdown = "";
  detail.report_note_id = null;
  detail.report_note_path = null;
  detail.report_excerpt = "";
  detail.tasks = [];
  detail.steps = [];
  detail.tool_calls = [];
  detail.progress_logs = [];
  detail.total_tasks = 0;
  detail.completed_tasks = 0;
  detail.failed_tasks = 0;
  selectedTaskId.value = null;
  logsCollapsed.value = false;
  upsertSessionSummary(detail);
}

async function loadSessionList(): Promise<void> {
  sessionListLoading.value = true;
  sidebarMessage.value = "";
  try {
    const sessions = await listResearchSessions();
    sessionList.value = sessions;
    if (sessions.length && activeSessionId.value === null) {
      await selectSession(sessions[0].id);
    }
  } catch (error) {
    sidebarMessage.value = error instanceof Error ? error.message : "加载历史研究失败";
  } finally {
    sessionListLoading.value = false;
  }
}

async function selectSession(sessionId: number, force = true): Promise<void> {
  activeSessionId.value = sessionId;
  logsCollapsed.value = false;

  const cached = sessionDetails[sessionId];
  if (cached && !force) {
    return;
  }

  detailLoading.value = true;
  try {
    const detail = await getResearchSession(sessionId);
    cacheSessionDetail(detail);
  } catch (error) {
    sidebarMessage.value = error instanceof Error ? error.message : "加载研究详情失败";
  } finally {
    detailLoading.value = false;
  }
}

async function reloadActiveSession(): Promise<void> {
  if (activeSessionId.value === null) {
    return;
  }
  await selectSession(activeSessionId.value, true);
}

async function createNewSession(): Promise<void> {
  if (hasRunningSession.value) {
    sidebarMessage.value = "当前有研究正在进行，请等待完成后再创建新的 session。";
    return;
  }

  sessionCreating.value = true;
  sidebarMessage.value = "";

  try {
    const detail = await createResearchSession();
    const cached = cacheSessionDetail(detail);
    activeSessionId.value = cached.id;
    selectedTaskId.value = null;
  } catch (error) {
    sidebarMessage.value = error instanceof Error ? error.message : "创建研究 session 失败";
  } finally {
    sessionCreating.value = false;
  }
}

async function runActiveSession(): Promise<void> {
  const detail = activeSessionDetail.value;
  if (!detail || activeSessionId.value === null) {
    return;
  }

  const topic = draftForm.topic.trim();
  if (!topic) {
    detail.error_message = "请输入研究主题";
    return;
  }

  if (currentController) {
    currentController.abort();
    currentController = null;
  }

  resetDetailForRun(detail);
  const controller = new AbortController();
  currentController = controller;
  runningSessionId.value = detail.id;
  runSubmitting.value = true;

  try {
    await runResearchSessionStream(
      detail.id,
      {
        topic,
        search_api: draftForm.searchApi || undefined
      },
      (event) => applyStreamEvent(detail.id, event),
      { signal: controller.signal }
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      appendProgressLog(
        detail,
        "已断开实时连接，可稍后点击“刷新详情”恢复后台研究结果。"
      );
      detail.error_message =
        "已断开实时连接，可稍后点击“刷新详情”恢复后台研究结果。";
    } else {
      const message = error instanceof Error ? error.message : "研究请求失败";
      detail.status = "failed";
      detail.error_message = message;
      detail.completed_at = new Date().toISOString();
      appendProgressLog(detail, "研究失败，已停止流程");
    }
    refreshTaskCounts(detail);
    upsertSessionSummary(detail);
  } finally {
    runSubmitting.value = false;
    runningSessionId.value = null;
    if (currentController === controller) {
      currentController = null;
    }
    await selectSession(detail.id, true);
  }
}

function disconnectStream(): void {
  if (!currentController) {
    return;
  }
  currentController.abort();
}

function ensureRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function toTaskId(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatJson(value: unknown): string {
  if (!value) {
    return "";
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function parseSourceItems(raw: string): SourceItem[] {
  if (!raw.trim()) {
    return [];
  }

  return raw
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const markdownMatch = line.match(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/);
      if (markdownMatch) {
        return {
          title: markdownMatch[1],
          url: markdownMatch[2],
          snippet: line.replace(markdownMatch[0], "").replace(/^[-*•\s]+/, "").trim()
        };
      }

      const pipeParts = line
        .replace(/^[-*•\s]+/, "")
        .split("|")
        .map((item) => item.trim())
        .filter(Boolean);
      if (pipeParts.length >= 2 && /^https?:\/\//.test(pipeParts[1])) {
        return {
          title: pipeParts[0],
          url: pipeParts[1],
          snippet: pipeParts.slice(2).join(" | ")
        };
      }

      const urlMatch = line.match(/https?:\/\/\S+/);
      return {
        title: pipeParts[0] || line,
        url: urlMatch?.[0] ?? "",
        snippet: urlMatch ? line.replace(urlMatch[0], "").trim() : ""
      };
    });
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(date);
}

function formatSessionProgress(session: ResearchSessionSummary): string {
  if (session.status === "draft") {
    return "草稿 session，等待开始";
  }
  if (!session.total_tasks) {
    return session.status === "failed" ? "研究失败" : "尚未生成任务";
  }
  return `${session.completed_tasks} / ${session.total_tasks} 任务完成`;
}

function formatSessionStatus(status: string): string {
  const mapping: Record<string, string> = {
    draft: "等待开始",
    running: "研究进行中",
    completed: "研究流程完成",
    failed: "研究失败"
  };
  return mapping[status] ?? status;
}

function formatTaskStatus(status: string): string {
  const mapping: Record<string, string> = {
    pending: "待执行",
    in_progress: "进行中",
    completed: "已完成",
    failed: "已失败",
    skipped: "已跳过"
  };
  return mapping[status] ?? status;
}

function statusClass(status: string): string {
  return `status-${status}`;
}

onMounted(async () => {
  await loadSessionList();
});

onBeforeUnmount(() => {
  if (currentController) {
    currentController.abort();
    currentController = null;
  }
});
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  background:
    radial-gradient(circle at top left, rgba(219, 234, 254, 0.95), transparent 30%),
    linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%);
  color: #172554;
}

.sidebar {
  padding: 28px 22px;
  border-right: 1px solid rgba(96, 165, 250, 0.18);
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(16px);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.brand-card,
.card,
.empty-card,
.center-card {
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 24px 48px rgba(30, 64, 175, 0.08);
}

.brand-card {
  padding: 20px;
  display: flex;
  gap: 16px;
}

.brand-icon {
  width: 56px;
  height: 56px;
  border-radius: 18px;
  display: grid;
  place-items: center;
  font-size: 26px;
  font-weight: 700;
  color: #eff6ff;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  box-shadow: 0 16px 28px rgba(59, 130, 246, 0.25);
}

.eyebrow {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6366f1;
}

.brand-card h1,
.workspace-header h2,
.center-card h2 {
  margin: 0;
  font-size: 30px;
  line-height: 1.15;
  color: #0f172a;
}

.brand-copy,
.meta-line,
.muted {
  color: #64748b;
}

.brand-copy {
  margin: 10px 0 0;
  line-height: 1.55;
}

.primary-btn,
.ghost-btn,
.session-card,
.task-card {
  font: inherit;
}

.primary-btn,
.ghost-btn {
  border: none;
  border-radius: 16px;
  cursor: pointer;
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease,
    opacity 0.18s ease;
}

.primary-btn {
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  color: #fff;
  padding: 14px 18px;
  font-weight: 700;
  box-shadow: 0 16px 26px rgba(79, 70, 229, 0.22);
}

.primary-btn:not(:disabled):hover,
.ghost-btn:not(:disabled):hover,
.session-card:not(:disabled):hover,
.task-card:not(:disabled):hover {
  transform: translateY(-1px);
}

.ghost-btn {
  padding: 11px 16px;
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
}

.ghost-btn:disabled,
.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.danger-btn {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.small-btn {
  padding: 8px 12px;
  font-size: 13px;
}

.create-btn {
  width: 100%;
}

.sidebar-message {
  margin: 0;
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  line-height: 1.5;
}

.sidebar-section {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-head,
.card-head,
.session-card-head,
.task-card-head,
.tool-call-head,
.header-actions,
.meta-chip-row,
.composer-actions {
  display: flex;
  align-items: center;
}

.section-head {
  justify-content: space-between;
}

.section-head h2,
.card-head h3,
.detail-block h4 {
  margin: 0;
  color: #0f172a;
}

.section-head span,
.task-counter {
  min-width: 28px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(99, 102, 241, 0.1);
  color: #4f46e5;
  font-size: 12px;
  text-align: center;
}

.empty-card {
  padding: 18px;
  line-height: 1.6;
  color: #64748b;
}

.session-list,
.task-list,
.timeline-list,
.bullet-list,
.source-list,
.tool-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.session-list {
  display: grid;
  gap: 12px;
  overflow-y: auto;
  padding-right: 4px;
}

.session-card,
.task-card {
  width: 100%;
  text-align: left;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(239, 246, 255, 0.9));
  border-radius: 22px;
  padding: 16px;
  cursor: pointer;
}

.session-card.active,
.task-card.active {
  border-color: rgba(59, 130, 246, 0.45);
  box-shadow: 0 18px 30px rgba(59, 130, 246, 0.14);
}

.session-card h3,
.task-card-head span:first-child {
  color: #0f172a;
}

.session-card h3 {
  margin: 12px 0 10px;
  font-size: 18px;
  line-height: 1.45;
}

.session-time,
.session-progress,
.session-excerpt,
.session-error,
.task-card p {
  margin: 0;
  line-height: 1.5;
}

.session-time,
.session-progress,
.task-card p {
  color: #64748b;
}

.session-excerpt {
  margin-top: 10px;
  color: #475569;
}

.session-error {
  margin-top: 10px;
  color: #b91c1c;
}

.status-badge,
.status-chip,
.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.status-chip {
  font-size: 13px;
}

.status-draft {
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
}

.status-running,
.status-in_progress {
  background: rgba(59, 130, 246, 0.12);
  color: #2563eb;
}

.status-completed {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.status-failed {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.status-skipped,
.status-pending {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.workspace {
  padding: 28px;
  min-width: 0;
}

.workspace-inner {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.workspace-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
}

.meta-line {
  margin: 10px 0 0;
  line-height: 1.6;
}

.header-actions,
.composer-actions,
.meta-chip-row {
  gap: 10px;
  flex-wrap: wrap;
}

.card {
  padding: 22px;
}

.composer-card p {
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.6;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 18px;
}

.field span {
  font-weight: 700;
  color: #334155;
}

.field-row {
  display: grid;
  grid-template-columns: minmax(200px, 320px);
}

textarea,
select {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 18px;
  padding: 14px 16px;
  font: inherit;
  color: #0f172a;
  background: rgba(255, 255, 255, 0.95);
  box-sizing: border-box;
}

textarea:focus,
select:focus {
  outline: none;
  border-color: rgba(37, 99, 235, 0.38);
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.12);
}

.error-banner {
  margin: 0;
  padding: 14px 16px;
  border-radius: 18px;
  background: rgba(254, 226, 226, 0.82);
  color: #b91c1c;
  border: 1px solid rgba(239, 68, 68, 0.18);
  line-height: 1.6;
}

.workspace-grid {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.grid-column {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}

.timeline-list {
  display: grid;
  gap: 12px;
}

.timeline-list li {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.timeline-dot {
  width: 12px;
  height: 12px;
  margin-top: 6px;
  border-radius: 999px;
  background: linear-gradient(135deg, #60a5fa, #7c3aed);
  box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.16);
}

.timeline-list p,
.bullet-list li,
.source-list li,
.tool-list li {
  margin: 0;
  line-height: 1.6;
}

.task-list {
  display: grid;
  gap: 12px;
}

.task-card-head {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.task-card p {
  font-size: 14px;
}

.report-card {
  min-height: 220px;
}

.detail-stack {
  display: grid;
  gap: 18px;
}

.detail-block {
  padding: 18px;
  border-radius: 18px;
  background: rgba(248, 250, 252, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.16);
}

.detail-block h4 {
  margin-bottom: 10px;
}

.pre-block {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 18px;
  padding: 16px;
  background: rgba(15, 23, 42, 0.035);
  border: 1px solid rgba(148, 163, 184, 0.12);
  color: #0f172a;
  line-height: 1.65;
  overflow-x: auto;
  box-sizing: border-box;
}

.compact-pre {
  font-size: 13px;
}

.report-pre {
  max-height: 560px;
}

.source-list {
  display: grid;
  gap: 12px;
}

.source-link {
  color: #1d4ed8;
  font-weight: 700;
  text-decoration: none;
}

.source-link:hover {
  text-decoration: underline;
}

.tool-list {
  display: grid;
  gap: 14px;
}

.tool-list li {
  padding: 16px;
  border-radius: 18px;
  background: rgba(241, 245, 249, 0.92);
}

.tool-call-head {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.meta-chip {
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
}

.path-chip {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.center-card {
  min-height: calc(100vh - 56px);
  padding: 40px;
  display: grid;
  place-content: center;
  gap: 18px;
  text-align: center;
}

.center-card p {
  margin: 0;
  color: #64748b;
  line-height: 1.7;
}

@media (max-width: 1180px) {
  .app-shell {
    grid-template-columns: 320px minmax(0, 1fr);
  }

  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    border-right: none;
    border-bottom: 1px solid rgba(96, 165, 250, 0.18);
  }

  .workspace {
    padding: 20px;
  }

  .workspace-header {
    flex-direction: column;
  }
}
</style>
