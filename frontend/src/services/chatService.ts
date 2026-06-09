import { ChatResponse, SessionInfo } from "@/types/chat";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function sendMessage(
  query: string,
  sessionId?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, session_id: sessionId }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  return res.json();
}

export async function getHistory(
  sessionId?: string
): Promise<{ role: string; content: string }[] | SessionInfo[]> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  const res = await fetch(`${API_BASE}/api/chat/history${params}`);

  if (!res.ok) {
    throw new Error(`Failed to fetch history: ${res.status}`);
  }

  return res.json();
}

export async function clearHistory(sessionId?: string): Promise<{ message: string }> {
  const params = sessionId ? `?session_id=${sessionId}` : "";
  const res = await fetch(`${API_BASE}/api/chat/history${params}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    throw new Error(`Failed to clear history: ${res.status}`);
  }

  return res.json();
}
