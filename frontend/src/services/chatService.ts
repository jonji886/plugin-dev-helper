import { ChatResponse, HistoryMessage, SessionInfo } from "@/types/chat";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function formatFetchError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);

  if (message.includes("Failed to fetch")) {
    return `无法连接到后端服务，请检查 NEXT_PUBLIC_API_URL 是否正确，以及后端是否已启动（当前地址：${API_BASE}）`;
  }

  return message;
}

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(input, init);

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`API error ${res.status}: ${err}`);
    }

    // 反馈接口按 HTTP 语义返回 204 No Content，不能再尝试解析 JSON。
    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return undefined as T;
    }

    return res.json() as Promise<T>;
  } catch (error) {
    throw new Error(formatFetchError(error));
  }
}

export async function sendMessage(
  query: string,
  sessionId?: string
): Promise<ChatResponse> {
  return requestJson<ChatResponse>(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, session_id: sessionId }),
  });
}

export async function getHistory(
  sessionId?: string
): Promise<HistoryMessage[] | SessionInfo[]> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  return requestJson(`${API_BASE}/api/chat/history${params}`);
}

export async function clearHistory(sessionId?: string): Promise<{ message: string }> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  return requestJson(`${API_BASE}/api/chat/history${params}`, {
    method: "DELETE",
  });
}

export async function submitFeedback(
  requestId: string,
  helpful: boolean,
  comment = ""
): Promise<void> {
  await requestJson(`${API_BASE}/api/chat/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, helpful, comment }),
  });
}
