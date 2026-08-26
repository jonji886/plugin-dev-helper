export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
  citations?: Citation[];
  requestId?: string;
  images?: string[];
}

export interface Citation {
  id: string;
  source: string;
  sdk_version: string;
  start_line: number;
  end_line: number;
}

export interface ChatResponse {
  answer: string;
  session_id: string;
  intent: string;
  retrieved_count: number;
  citations: Citation[];
  request_id: string;
  trace_id?: string;
  provider?: string;
  model?: string;
  model_role?: string;
  route_reason?: string;
  image_count?: number;
  prompt_version?: string;
  estimated_cost?: number;
}

export type FeedbackReason =
  | "wrong_answer"
  | "retrieval_wrong"
  | "citation_wrong"
  | "code_wrong"
  | "incomplete"
  | "other";

export interface Badcase {
  id: number;
  status: "NEW" | "REVIEWED" | "PROMOTED" | "IGNORED";
  request_id: string;
  query: string;
  answer: string;
  feedback_reason: string;
  feedback_comment: string;
  model: string;
  prompt_version: string;
  created_at: string;
}

export interface SessionInfo {
  id: string;
  message_count: number;
  last_message: string;
}

export interface ChatRequest {
  query: string;
  session_id?: string;
  images?: string[];
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timestamp?: string;
  request_id?: string;
}
