"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { ChatMessage as ChatMessageType } from "@/types/chat";
import { sendMessage, getHistory } from "@/services/chatService";
import ChatMessage from "@/components/ChatMessage";
import ChatInput from "@/components/ChatInput";
import ChatHistory from "@/components/ChatHistory";

function getFriendlyErrorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);

  if (message.includes("无法连接到后端服务")) {
    return message;
  }

  if (message.includes("回答生成失败")) {
    return message;
  }

  if (message.includes("未配置模型密钥")) {
    return message;
  }

  if (message.includes("API error")) {
    return `接口请求失败：${message}`;
  }

  return `请求失败: ${message || "请检查网络连接或后端服务是否运行"}`;
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(async (query: string) => {
    // Add user message immediately
    const userMsg: ChatMessageType = { role: "user", content: query };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const res = await sendMessage(query, sessionId);
      const assistantMsg: ChatMessageType = {
        role: "assistant",
        content: res.answer,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setSessionId(res.session_id);
      setRefreshTrigger((t) => t + 1);
    } catch (err: any) {
      const errorMsg: ChatMessageType = {
        role: "assistant",
        content: getFriendlyErrorMessage(err),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  const handleNewChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
  }, []);

  const handleSelectSession = useCallback(async (sid: string) => {
    setSessionId(sid);
    setMessages([]);
    setLoading(true);
    try {
      const history = await getHistory(sid);
      setMessages(history as ChatMessageType[]);
    } catch (err) {
      setMessages([
        {
          role: "assistant",
          content: getFriendlyErrorMessage(err),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="flex h-screen bg-white dark:bg-zinc-950">
      {/* Sidebar */}
      <ChatHistory
        currentSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        refreshTrigger={refreshTrigger}
      />

      {/* Main chat area */}
      <div className="flex-1 flex flex-col h-full">
        {/* Header */}
        <header className="border-b border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 px-4 md:px-6 py-3">
          <div className="max-w-3xl mx-auto flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white text-sm font-bold">
              P
            </div>
            <div>
              <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                插件开发 AI 助手
              </h1>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                SDK 智能问答 · 代码示例 · API 查询
              </p>
            </div>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="max-w-3xl mx-auto">
            {messages.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-full text-center py-20">
                <div className="w-16 h-16 rounded-2xl bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center mb-4">
                  <svg className="w-8 h-8 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
                  </svg>
                </div>
                <h2 className="text-lg font-semibold text-zinc-800 dark:text-zinc-200 mb-2">
                  有什么可以帮助您的？
                </h2>
                <p className="text-sm text-zinc-500 dark:text-zinc-400 max-w-md">
                  您可以询问 SDK 函数用法、API 参数说明、或获取 TypeScript 代码示例
                </p>

                {/* Suggested questions */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-6 w-full max-w-lg">
                  {[
                    "IDP.Miniapp.exit 怎么使用？",
                    "如何获取当前应用模式？",
                    "上传临时存储数据的方法",
                  ].map((q) => (
                    <button
                      key={q}
                      onClick={() => handleSend(q)}
                      className="text-left px-3 py-2 text-xs rounded-lg border border-zinc-200 dark:border-zinc-700 hover:bg-zinc-50 dark:hover:bg-zinc-800 text-zinc-600 dark:text-zinc-400 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <ChatMessage key={i} message={msg} />
            ))}

            {loading && messages[messages.length - 1]?.role === "user" && (
              <div className="flex justify-start mb-4">
                <div className="bg-zinc-100 dark:bg-zinc-800 rounded-2xl rounded-bl-sm px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 bg-zinc-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-2 h-2 bg-zinc-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                    <span className="w-2 h-2 bg-zinc-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    <span className="text-xs text-zinc-400 ml-1">正在检索知识库...</span>
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <ChatInput onSend={handleSend} disabled={loading} />
      </div>
    </div>
  );
}
