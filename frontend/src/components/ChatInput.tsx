"use client";

import { useState, useRef, useEffect } from "react";

interface Props {
  onSend: (message: string, images: string[]) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [input, setInput] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }
  }, [input]);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if ((!trimmed && images.length === 0) || disabled) return;
    onSend(trimmed, images);
    setInput("");
    setImages([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleFiles = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []).slice(0, 3);
    files.forEach((file) => {
      if (!file.type.startsWith("image/") || file.size > 8 * 1024 * 1024) return;
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result !== "string") return;
        setImages((current) => current.length >= 3 ? current : [...current, reader.result as string]);
      };
      reader.readAsDataURL(file);
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="border-t border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-4">
      {images.length > 0 && (
        <div className="mx-auto mb-3 flex w-full max-w-6xl gap-2">
          {images.map((image, index) => (
            <div key={`${image.slice(0, 30)}-${index}`} className="relative">
              <img src={image} alt={`待分析图片 ${index + 1}`} className="h-16 w-16 rounded-lg object-cover" />
              <button
                type="button"
                onClick={() => setImages((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                className="absolute -right-1 -top-1 h-5 w-5 rounded-full bg-zinc-800 text-xs text-white"
                aria-label="移除图片"
              >×</button>
            </div>
          ))}
        </div>
      )}
      <div className="mx-auto flex w-full max-w-6xl items-end gap-2">
        <input ref={fileInputRef} type="file" accept="image/*" multiple hidden onChange={handleFiles} />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || images.length >= 3}
          className="rounded-xl border border-zinc-300 px-3 py-3 text-sm text-zinc-600 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
          aria-label="添加图片"
        >
          图片
        </button>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入您的问题，例如：IDP.Miniapp.exit 怎么使用？"
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-xl border border-zinc-300 dark:border-zinc-600 bg-zinc-50 dark:bg-zinc-800 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          onClick={handleSubmit}
          disabled={disabled || (!input.trim() && images.length === 0)}
          className="px-5 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-300 dark:disabled:bg-zinc-700 text-white rounded-xl text-sm font-medium transition-colors disabled:cursor-not-allowed"
        >
          {disabled ? (
            <span className="flex items-center gap-1">
              <span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full" />
              思考中
            </span>
          ) : (
            "发送"
          )}
        </button>
      </div>
    </div>
  );
}
