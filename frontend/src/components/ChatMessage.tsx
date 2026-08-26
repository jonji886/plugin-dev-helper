"use client";

import { ChatMessage as ChatMessageType, FeedbackReason } from "@/types/chat";
import { Children, isValidElement } from "react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  message: ChatMessageType;
  onFeedback?: (requestId: string, helpful: boolean, reason?: FeedbackReason, comment?: string) => void;
}

function CopyButton({ text, tone = "dark" }: { text: string; tone?: "dark" | "light" }) {
  const colorClass = tone === "dark"
    ? "text-zinc-300 hover:bg-zinc-700 hover:text-white"
    : "text-zinc-500 hover:bg-zinc-200 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-700 dark:hover:text-white";

  return (
    <button
      onClick={() => navigator.clipboard.writeText(text)}
      className={`shrink-0 rounded px-2 py-0.5 text-xs transition-colors ${colorClass}`}
      aria-label="复制内容"
    >
      复制
    </button>
  );
}

export default function ChatMessage({ message, onFeedback }: Props) {
  const isUser = message.role === "user";
  const [feedbackState, setFeedbackState] = useState<"idle" | "positive" | "negative" | "submitted">("idle");
  const [reason, setReason] = useState<FeedbackReason>("wrong_answer");
  const [comment, setComment] = useState("");

  const submit = (helpful: boolean) => {
    if (!message.requestId || !onFeedback) return;
    if (!helpful && feedbackState !== "negative") {
      setFeedbackState("negative");
      return;
    }
    onFeedback(message.requestId, helpful, helpful ? undefined : reason, helpful ? "" : comment);
    setFeedbackState(helpful ? "positive" : "submitted");
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-5`}>
      <div
        className={`max-w-[88%] rounded-2xl px-5 py-4 md:max-w-3xl ${
          isUser
            ? "bg-blue-600 text-white rounded-br-sm"
            : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100 rounded-bl-sm"
        }`}
      >
        {isUser ? (
          <>
            {message.images && message.images.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {message.images.map((image, index) => (
                  <img key={`${image.slice(0, 30)}-${index}`} src={image} alt={`用户图片 ${index + 1}`} className="max-h-48 max-w-xs rounded-lg object-contain" />
                ))}
              </div>
            )}
            {message.content && <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>}
          </>
        ) : (
          <>
            <div className="markdown-content text-sm leading-7">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => <h1 className="mb-4 text-xl font-bold tracking-tight">{children}</h1>,
                  h2: ({ children }) => <h2 className="mb-3 mt-7 text-lg font-semibold">{children}</h2>,
                  h3: ({ children }) => <h3 className="mb-2 mt-5 text-base font-semibold">{children}</h3>,
                  p: ({ children }) => <p className="mb-4 last:mb-0">{children}</p>,
                  ul: ({ children }) => <ul className="mb-4 list-disc space-y-1 pl-5 marker:text-zinc-400">{children}</ul>,
                  ol: ({ children }) => <ol className="mb-4 list-decimal space-y-1 pl-5 marker:font-medium marker:text-zinc-500">{children}</ol>,
                  li: ({ children }) => <li className="pl-1">{children}</li>,
                  blockquote: ({ children }) => (
                    <blockquote className="mb-4 border-l-4 border-blue-400 bg-blue-50 px-4 py-2 text-zinc-700 dark:bg-blue-950/30 dark:text-zinc-200">
                      {children}
                    </blockquote>
                  ),
                  hr: () => <hr className="my-6 border-zinc-200 dark:border-zinc-700" />,
                  a: ({ children, href }) => (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-blue-600 underline underline-offset-2 hover:text-blue-700 dark:text-blue-400"
                    >
                      {children}
                    </a>
                  ),
                  pre: ({ children }) => {
                    const codeElement = Children.toArray(children)[0];
                    const props = isValidElement<{ className?: string; children?: unknown }>(codeElement)
                      ? codeElement.props
                      : {};
                    const code = String(props.children ?? "").replace(/\n$/, "");
                    const language = props.className?.replace("language-", "") || "code";

                    return (
                      <div className="my-4 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-950 text-zinc-100">
                        <div className="flex items-center justify-between border-b border-zinc-700 px-3 py-1.5 text-xs text-zinc-400">
                          <span>{language}</span>
                          <CopyButton text={code} />
                        </div>
                        <pre className="overflow-x-auto p-4 text-xs leading-6"><code>{code}</code></pre>
                      </div>
                    );
                  },
                  code: ({ children }) => (
                    <code className="rounded bg-zinc-200 px-1.5 py-0.5 font-mono text-[0.85em] dark:bg-zinc-700">
                      {children}
                    </code>
                  ),
                  table: ({ children }) => (
                    <div className="my-4 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
                      <table className="min-w-full border-collapse text-left text-xs">{children}</table>
                    </div>
                  ),
                  thead: ({ children }) => <thead className="bg-zinc-200/70 dark:bg-zinc-700">{children}</thead>,
                  th: ({ children }) => <th className="border-b border-zinc-200 px-3 py-2 font-semibold dark:border-zinc-700">{children}</th>,
                  td: ({ children }) => <td className="border-b border-zinc-200 px-3 py-2 align-top dark:border-zinc-700">{children}</td>,
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>

            {message.citations && message.citations.length > 0 && (
              <div className="mt-5 border-t border-zinc-200 pt-3 text-xs text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                <p className="mb-2 font-medium text-zinc-600 dark:text-zinc-300">来源</p>
                <ul className="space-y-1.5">
                  {message.citations.map((citation) => {
                    const version = citation.sdk_version ? ` v${citation.sdk_version}` : "";
                    const lines = citation.start_line > 0
                      ? `，第 ${citation.start_line}-${citation.end_line} 行`
                      : "";
                    const text = `${citation.source || citation.id}${version}${lines}`;

                    return (
                      <li key={citation.id} className="flex items-center justify-between gap-2 rounded bg-white/60 px-2 py-1.5 dark:bg-zinc-900/40">
                        <span className="truncate">{text}</span>
                        <CopyButton text={text} tone="light" />
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            {message.requestId && onFeedback && (
              <div className="mt-4 flex items-center justify-end gap-2 border-t border-zinc-200 pt-3 text-xs text-zinc-500 dark:border-zinc-700">
                <span className="mr-auto">这条回答有帮助吗？</span>
                <button
                  onClick={() => submit(true)}
                  className="rounded px-2 py-1 hover:bg-emerald-100 hover:text-emerald-700 dark:hover:bg-emerald-950/40 dark:hover:text-emerald-300"
                >
                  👍 有帮助
                </button>
                <button
                  onClick={() => submit(false)}
                  className="rounded px-2 py-1 hover:bg-rose-100 hover:text-rose-700 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
                >
                  👎 没帮助
                </button>
              </div>
            )}
            {message.requestId && feedbackState === "negative" && (
              <div className="mt-2 rounded-lg bg-white/70 p-3 text-xs dark:bg-zinc-900/50">
                <p className="mb-2 font-medium text-zinc-700 dark:text-zinc-200">可以告诉我们原因吗？</p>
                <select value={reason} onChange={(event) => setReason(event.target.value as FeedbackReason)} className="mb-2 w-full rounded border border-zinc-300 bg-transparent px-2 py-1.5 dark:border-zinc-600">
                  <option value="wrong_answer">回答错误</option>
                  <option value="retrieval_wrong">没有找到正确文档</option>
                  <option value="citation_wrong">引用错误</option>
                  <option value="code_wrong">代码示例错误</option>
                  <option value="incomplete">回答不完整</option>
                  <option value="other">其他</option>
                </select>
                <textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="补充说明（可选）" maxLength={1000} className="mb-2 w-full resize-none rounded border border-zinc-300 bg-transparent px-2 py-1.5 dark:border-zinc-600" rows={2} />
                <button onClick={() => submit(false)} className="rounded bg-zinc-900 px-3 py-1.5 text-white dark:bg-zinc-100 dark:text-zinc-900">提交反馈</button>
              </div>
            )}
            {feedbackState === "submitted" && <p className="mt-2 text-right text-xs text-zinc-500">感谢反馈，已记录为待复核案例。</p>}
          </>
        )}
      </div>
    </div>
  );
}
