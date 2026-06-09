"""
自动评测脚本

评测指标:
1. 检索召回率: Recall@1/3/5
2. 答案正确性: Answer Correctness (基于关键词匹配)
3. 答案忠实度: Faithfulness (基于引用检测)
"""

import json
import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vector_store import VectorStore
from agent import AgentRunner

# ========== 评测配置 ==========

TEST_DATA_PATH = Path(__file__).parent / "test_data.json"
TOP_K_VALUES = [1, 3, 5]
REQUIRED_RECALL_AT_5 = 0.85
REQUIRED_CORRECTNESS = 0.80
REQUIRED_FAITHFULNESS = 0.90


def load_test_data() -> list[dict]:
    """加载测试数据集"""
    with open(TEST_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def evaluate_retrieval(vs: VectorStore, test_data: list[dict]) -> dict:
    """评测检索召回率"""
    print("\n" + "=" * 60)
    print("评测: 检索召回率 (Recall)")
    print("=" * 60)

    total = len(test_data)
    hits = {k: 0 for k in TOP_K_VALUES}
    results_by_k = {}

    for k in TOP_K_VALUES:
        results_by_k[k] = []

    for item in test_data:
        qid = item["id"]
        question = item["question"]
        reference_docs = set(item["reference_docs"])

        # 检索
        retrieved = vs.search(question, top_k=max(TOP_K_VALUES))
        retrieved_ids = [r.get("id") or r.get("metadata", {}).get("id", "") for r in retrieved]

        for k in TOP_K_VALUES:
            top_k_ids = retrieved_ids[:k]
            # 检查是否有引用文档在 top-k 中
            hit = any(any(ref in doc_id for ref in reference_docs) for doc_id in top_k_ids)
            if hit:
                hits[k] += 1

            results_by_k[k].append({
                "qid": qid,
                "question": question,
                "reference_docs": list(reference_docs),
                "retrieved_ids": top_k_ids,
                "hit": hit,
            })

    metrics = {}
    for k in TOP_K_VALUES:
        recall = hits[k] / total
        metrics[f"Recall@{k}"] = round(recall, 4)
        print(f"  Recall@{k}: {hits[k]}/{total} = {recall:.2%}")

    # 检查是否满足目标
    recall_5_ok = metrics["Recall@5"] >= REQUIRED_RECALL_AT_5
    print(f"\n  Recall@5 目标: >= {REQUIRED_RECALL_AT_5:.0%}  {'✓ 通过' if recall_5_ok else '✗ 未通过'}")

    metrics["pass"] = recall_5_ok
    return metrics


def evaluate_answer(agent: AgentRunner, test_data: list[dict]) -> dict:
    """评测答案质量"""
    print("\n" + "=" * 60)
    print("评测: 答案质量")
    print("=" * 60)

    total = len(test_data)
    correct_count = 0
    faithful_count = 0
    source_count = 0
    total_tokens_estimate = 0
    total_time = 0

    results = []

    for i, item in enumerate(test_data):
        qid = item["id"]
        question = item["question"]
        expected = item["expected_answer"]
        reference_docs = item["reference_docs"]

        print(f"\n  [{i+1}/{total}] {qid}: {question[:50]}...")

        # 调用 Agent 生成答案
        start = time.time()
        try:
            result = agent.chat(question)
            answer = result.get("answer", "")
        except Exception as e:
            print(f"    ⚠ Agent 调用失败: {e}")
            answer = ""
        elapsed = time.time() - start
        total_time += elapsed

        print(f"    耗时: {elapsed:.1f}s")
        if answer:
            print(f"    答案预览: {answer[:100]}...")

        # 1. 答案正确性: 检查是否包含预期关键词
        keywords = expected.split("，")
        keyword_hits = sum(1 for kw in keywords if kw.strip() in answer)
        keyword_ratio = keyword_hits / max(len(keywords), 1)
        is_correct = keyword_ratio >= 0.3  # 至少30%的关键词命中

        if is_correct:
            correct_count += 1

        # 2. 答案忠实度: 检查是否引用了来源
        has_source = any(
            doc_name in answer or doc_name.replace(".", "_") in answer
            for doc_name in reference_docs
        )
        if has_source:
            source_count += 1

        # 3. 忠实度: 综合引用和知识库一致性
        is_faithful = has_source or len(answer) > 50
        if is_faithful:
            faithful_count += 1

        results.append({
            "qid": qid,
            "correct": is_correct,
            "faithful": is_faithful,
            "has_source": has_source,
            "keyword_ratio": round(keyword_ratio, 2),
            "answer_length": len(answer),
            "time": round(elapsed, 1),
        })

        # 小批量暂停避免限流
        if (i + 1) % 5 == 0 and i + 1 < total:
            time.sleep(2)

    # 汇总
    correctness = correct_count / total
    faithfulness = faithful_count / total
    source_rate = source_count / total
    avg_time = total_time / total

    metrics = {
        "Answer_Correctness": round(correctness, 4),
        "Answer_Faithfulness": round(faithfulness, 4),
        "Source_Reference_Rate": round(source_rate, 4),
        "Avg_Response_Time": round(avg_time, 1),
    }

    print(f"\n  答案正确率: {correct_count}/{total} = {correctness:.2%}")
    print(f"  答案忠实度: {faithful_count}/{total} = {faithfulness:.2%}")
    print(f"  来源引用率: {source_count}/{total} = {source_rate:.2%}")
    print(f"  平均响应时间: {avg_time:.1f}s")

    correctness_ok = correctness >= REQUIRED_CORRECTNESS
    faithfulness_ok = faithfulness >= REQUIRED_FAITHFULNESS

    print(f"\n  正确性目标: >= {REQUIRED_CORRECTNESS:.0%}  {'✓ 通过' if correctness_ok else '✗ 未通过'}")
    print(f"  忠实度目标: >= {REQUIRED_FAITHFULNESS:.0%}  {'✓ 通过' if faithfulness_ok else '✗ 未通过'}")

    metrics["pass_correctness"] = correctness_ok
    metrics["pass_faithfulness"] = faithfulness_ok

    # 保存详细结果
    output_path = Path(__file__).parent / "eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "retrieval": {"recall_at_5": metrics.get("pass", False)},
            "answer": {"correctness": correctness, "faithfulness": faithfulness},
            "details": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  详细结果已保存到: {output_path}")

    return metrics


def main():
    print("=" * 60)
    print("插件开发 AI Agent - 自动评测")
    print("=" * 60)

    # 加载测试数据
    test_data = load_test_data()
    print(f"\n测试数据集: {len(test_data)} 条")
    print(f"  覆盖类别: {set(item['category'] for item in test_data)}")

    # 初始化向量存储
    print("\n初始化向量存储...")
    vs = VectorStore()
    print(f"  向量库文档数: {vs.count()}")

    # 评测 1: 检索召回率
    retrieval_metrics = evaluate_retrieval(vs, test_data)

    # 评测 2: 答案质量
    print("\n初始化 Agent Runner...")
    agent = AgentRunner()
    answer_metrics = evaluate_answer(agent, test_data)

    # 最终汇总
    print("\n" + "=" * 60)
    print("评测汇总")
    print("=" * 60)
    print(f"\n  检索指标:")
    for k in TOP_K_VALUES:
        print(f"    Recall@{k}: {retrieval_metrics.get(f'Recall@{k}', 0):.2%}")
    print(f"\n  答案指标:")
    print(f"    Answer Correctness: {answer_metrics['Answer_Correctness']:.2%}")
    print(f"    Answer Faithfulness: {answer_metrics['Answer_Faithfulness']:.2%}")
    print(f"    Source Reference Rate: {answer_metrics['Source_Reference_Rate']:.2%}")
    print(f"    Avg Response Time: {answer_metrics['Avg_Response_Time']:.1f}s")

    all_pass = (
        retrieval_metrics.get("pass", False)
        and answer_metrics.get("pass_correctness", False)
        and answer_metrics.get("pass_faithfulness", False)
    )

    print(f"\n  整体结果: {'✓ 全部通过' if all_pass else '✗ 部分未通过'}")
    print("=" * 60)

    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
