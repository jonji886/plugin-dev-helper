"use client";

import { ChatMessage as ChatMessageType } from "@/types/chat";
import { useMemo } from "react";

interface Props {
  message: ChatMessageType;
  onCodeClick?: (code: string) => void;
}

export default function ChatMessage({ message, onCodeClick }: Props) {
  const isUser = message.role === "user";

  // Parse code blocks from content
  const parts = useMemo(() => {
    const blocks: { type: "text" | "code"; content: string; lang?: string }[] = [];
    const regex = /```(\w*)\n?([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = regex.exec(message.content)) !== null) {
      // Text before this code block
      if (match.index > lastIndex) {
        blocks.push({ type: "text", content: message.content.slice(lastIndex, match.index) });
      }
      blocks.push({ type: "code", content: match[2].trim(), lang: match[1] || "typescript" });
      lastIndex = match.index + match[0].length;
    }

    // Remaining text
    if (lastIndex < message.content.length) {
      blocks.push({ type: "text", content: message.content.slice(lastIndex) });
    }

    return blocks;
  }, [message.content]);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-blue-600 text-white rounded-br-sm"
            : "bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 rounded-bl-sm"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{message.content}</p>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none space-y-2">
            {parts.map((part, i) =>
              part.type === "code" ? (
                <div key={i} className="relative group">
                  <div className="flex items-center justify-between bg-zinc-800 dark:bg-zinc-900 text-zinc-300 text-xs px-3 py-1 rounded-t-md">
                    <span>{part.lang || "code"}</span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(part.content);
                      }}
                      className="hover:text-white transition-colors"
                    >
                      复制
                    </button>
                  </div>
                  <pre className="bg-zinc-900 dark:bg-black text-zinc-200 text-sm p-3 rounded-b-md overflow-x-auto">
                    <code>{part.content}</code>
                  </pre>
                </div>
              ) : (
                <p key={i} className="whitespace-pre-wrap text-sm leading-relaxed">
                  {part.content}
                </p>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}
