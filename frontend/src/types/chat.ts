export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
}

export interface ChatResponse {
  answer: string;
  session_id: string;
  intent: string;
  retrieved_count: number;
}

export interface SessionInfo {
  id: string;
  message_count: number;
  last_message: string;
}

export interface ChatRequest {
  query: string;
  session_id?: string;
}
