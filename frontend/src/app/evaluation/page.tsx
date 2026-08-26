"use client";

import { useEffect, useState } from "react";
import { getBadcases, updateBadcase } from "@/services/chatService";
import { Badcase } from "@/types/chat";

export default function EvaluationPage() {
  const [cases, setCases] = useState<Badcase[]>([]);
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      setCases(await getBadcases());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    let active = true;
    getBadcases().then((data) => {
      if (active) setCases(data);
    }).catch((err: unknown) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    });
    return () => { active = false; };
  }, []);

  const changeStatus = async (id: number, status: Badcase["status"]) => {
    await updateBadcase(id, status);
    await refresh();
  };

  return (
    <main className="min-h-dvh bg-zinc-50 p-6 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div className="mx-auto max-w-6xl">
        <h1 className="mb-2 text-2xl font-semibold">Evaluation / Badcases</h1>
        <p className="mb-6 text-sm text-zinc-500">负反馈候选需要人工复核后再晋升到回归评测集。</p>
        {error && <p className="mb-4 rounded bg-rose-100 p-3 text-sm text-rose-700">{error}</p>}
        <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-zinc-100 text-xs dark:bg-zinc-800"><tr>
              <th className="px-4 py-3">Query / Answer</th><th className="px-4 py-3">Reason</th>
              <th className="px-4 py-3">Model / Prompt</th><th className="px-4 py-3">Created</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Action</th>
            </tr></thead>
            <tbody>{cases.map((item) => <tr key={item.id} className="border-t border-zinc-200 align-top dark:border-zinc-800">
              <td className="max-w-md px-4 py-3"><p className="font-medium">{item.query}</p><p className="mt-1 text-xs text-zinc-500">{item.answer}</p><p className="mt-2 text-[11px] text-zinc-400">trace: {item.request_id}</p></td>
              <td className="px-4 py-3">{item.feedback_reason || "未分类"}<p className="mt-1 text-xs text-zinc-500">{item.feedback_comment}</p></td>
              <td className="whitespace-nowrap px-4 py-3 text-xs">{item.model || "-"}<br />{item.prompt_version || "-"}</td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-zinc-500">{item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "-"}</td>
              <td className="px-4 py-3"><span className="rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-800">{item.status}</span></td>
              <td className="whitespace-nowrap px-4 py-3"><button onClick={() => void changeStatus(item.id, "PROMOTED")} disabled={item.status === "PROMOTED"} className="mr-2 rounded bg-blue-600 px-2 py-1 text-xs text-white disabled:opacity-40">Promote</button><button onClick={() => void changeStatus(item.id, "IGNORED")} className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700">Ignore</button></td>
            </tr>)}</tbody>
          </table>
          {cases.length === 0 && <p className="p-8 text-center text-sm text-zinc-500">暂无 badcase</p>}
        </div>
      </div>
    </main>
  );
}
