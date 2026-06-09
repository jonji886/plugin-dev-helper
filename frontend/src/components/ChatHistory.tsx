"use client";

import { useEffect, useState } from "react";
import { SessionInfo } from "@/types/chat";
import { getHistory, clearHistory } from "@/services/chatService";

interface Props {
  currentSessionId?: string;
  onSelectSession: (sessionId: string) => void;
  onNewChat: () => void;
  refreshTrigger: number;
}

export default function ChatHistory({
  currentSessionId,
  onSelectSession,
  onNewChat,
  refreshTrigger,
}: Props) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    getHistory()
      .then((data) => setSessions(data as SessionInfo[]))
      .catch(() => {});
  }, [refreshTrigger]);

  const handleClear = async () => {
    if (!confirm("确定清除所有会话历史？")) return;
    await clearHistory();
    setSessions([]);
    onNewChat();
  };

  // Mobile: hamburger menu; Desktop: sidebar toggle
  return (
    <>
      {/* Mobile toggle */}
      <button
        onClick={() => setOpen(!open)}
        className="md:hidden fixed top-3 left-3 z-50 p-2 rounded-lg bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
        aria-label="Toggle history"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Overlay for mobile */}
      {open && (
        <div className="md:hidden fixed inset-0 bg-black/30 z-40" onClick={() => setOpen(false)} />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed md:relative z-40 h-full bg-zinc-50 dark:bg-zinc-900 border-r border-zinc-200 dark:border-zinc-700 flex flex-col transition-transform duration-200 ${
          open ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        }`}
        style={{ width: "260px" }}
      >
        <div className="p-3 border-b border-zinc-200 dark:border-zinc-700">
          <button
            onClick={() => {
              onNewChat();
              setOpen(false);
            }}
            className="w-full py-2 px-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
          >
            + 新建对话
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {sessions.length === 0 && (
            <p className="text-xs text-zinc-400 text-center py-4">暂无历史记录</p>
          )}
          {sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => {
                onSelectSession(s.id);
                setOpen(false);
              }}
              className={`w-full text-left p-2 rounded-lg text-xs transition-colors ${
                s.id === currentSessionId
                  ? "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                  : "hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
              }`}
            >
              <div className="truncate font-medium">{s.last_message || "新对话"}</div>
              <div className="text-zinc-400 mt-0.5">{s.message_count} 条消息</div>
            </button>
          ))}
        </div>

        <div className="p-3 border-t border-zinc-200 dark:border-zinc-700">
          <button
            onClick={handleClear}
            className="w-full py-1.5 text-xs text-zinc-500 hover:text-red-500 transition-colors"
          >
            清除所有历史
          </button>
        </div>
      </aside>
    </>
  );
}
