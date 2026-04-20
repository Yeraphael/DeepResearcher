const baseURL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export interface ResearchSessionCreateRequest {
  topic?: string;
  search_api?: string;
}

export interface ResearchSessionRunRequest {
  topic: string;
  search_api?: string;
}

export interface ResearchStreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface ResearchSessionSummary {
  id: number;
  topic: string;
  display_topic: string;
  search_api: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  total_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  report_excerpt: string;
}

export interface ResearchTask {
  id: number;
  title: string;
  intent: string;
  query: string;
  status: string;
  summary: string;
  sources_summary: string;
  notices: string[];
  note_id: string | null;
  note_path: string | null;
  stream_token: string | null;
}

export interface ResearchStep {
  id: number;
  type: string;
  step: number | null;
  task_id: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ResearchToolCall {
  id: number;
  event_id: number | null;
  task_id: number | null;
  agent: string;
  tool: string;
  parameters: Record<string, unknown>;
  result: string;
  note_id: string | null;
  note_path: string | null;
  created_at: string;
}

export interface ResearchSessionDetail extends ResearchSessionSummary {
  report_markdown: string;
  report_note_id: string | null;
  report_note_path: string | null;
  tasks: ResearchTask[];
  steps: ResearchStep[];
  tool_calls: ResearchToolCall[];
  progress_logs: string[];
}

export interface StreamOptions {
  signal?: AbortSignal;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }
  return (await response.json()) as T;
}

async function extractErrorMessage(response: Response): Promise<string> {
  const fallback = `请求失败，状态码：${response.status}`;

  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
  } catch {
    // Ignore JSON parse failures and fall back to text response.
  }

  try {
    const text = await response.text();
    return text.trim() || fallback;
  } catch {
    return fallback;
  }
}

export async function listResearchSessions(): Promise<ResearchSessionSummary[]> {
  const response = await fetch(`${baseURL}/api/research/sessions`);
  return parseJsonResponse<ResearchSessionSummary[]>(response);
}

export async function getResearchSession(
  sessionId: number
): Promise<ResearchSessionDetail> {
  const response = await fetch(`${baseURL}/api/research/sessions/${sessionId}`);
  return parseJsonResponse<ResearchSessionDetail>(response);
}

export async function createResearchSession(
  payload: ResearchSessionCreateRequest = {}
): Promise<ResearchSessionDetail> {
  const response = await fetch(`${baseURL}/api/research/sessions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  return parseJsonResponse<ResearchSessionDetail>(response);
}

export async function runResearchSessionStream(
  sessionId: number,
  payload: ResearchSessionRunRequest,
  onEvent: (event: ResearchStreamEvent) => void,
  options: StreamOptions = {}
): Promise<void> {
  const response = await fetch(
    `${baseURL}/api/research/sessions/${sessionId}/run/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream"
      },
      body: JSON.stringify(payload),
      signal: options.signal
    }
  );

  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }

  const body = response.body;
  if (!body) {
    throw new Error("浏览器不支持流式响应，无法获取研究进度。");
  }

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);

      if (rawEvent.startsWith("data:")) {
        const dataPayload = rawEvent.slice(5).trim();
        if (dataPayload) {
          try {
            const event = JSON.parse(dataPayload) as ResearchStreamEvent;
            onEvent(event);
            if (event.type === "error" || event.type === "done") {
              return;
            }
          } catch (error) {
            console.error("解析流式事件失败", error, dataPayload);
          }
        }
      }

      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      if (buffer.trim()) {
        const rawEvent = buffer.trim();
        if (rawEvent.startsWith("data:")) {
          const dataPayload = rawEvent.slice(5).trim();
          if (dataPayload) {
            try {
              const event = JSON.parse(dataPayload) as ResearchStreamEvent;
              onEvent(event);
            } catch (error) {
              console.error("解析流式事件失败", error, dataPayload);
            }
          }
        }
      }
      break;
    }
  }
}
