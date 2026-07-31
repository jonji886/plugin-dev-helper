"""
自动评测脚本

评测指标:
1. 检索召回率: Recall@1/3/5
2. 答案正确性: Answer Correctness (基于关键词匹配)
3. 来源有效率: Citation Validity（引用必须存在于知识库索引）
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
REQUIRED_CITATION_VALIDITY = 0.90


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
        retrieved = vs.search_hybrid(question, top_k=max(TOP_K_VALUES))
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
    citation_valid_count = 0
    reference_cited_count = 0
    total_tokens_estimate = 0
    total_time = 0

    index_path = Path(__file__).parent.parent / "data" / "knowledge" / "_index.json"
    try:
        knowledge_index = json.loads(index_path.read_text(encoding="utf-8"))
        index_by_id = {entry.get("id"): entry for entry in knowledge_index}
    except (FileNotFoundError, json.JSONDecodeError):
        index_by_id = {}

    results = []

    for i, item in enumerate(test_data):
        qid = item["id"]
        question = item["question"]
        expected = item["expected_answer"]
        reference_docs = item["reference_docs"]

        print(f"\n  [{i+1}/{total}] {qid}: {question[:50]}...")

        # 调用 Agent 生成答案
        start = time.time()
        result = {}
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

        # 2. 来源有效率：引用必须是 Agent 根据实际检索结果从知识库索引装配的。
        citations = result.get("citations", [])
        citation_valid = bool(citations) and all(
            (entry := index_by_id.get(citation.get("id")))
            and citation.get("source") == entry.get("source", "")
            and citation.get("sdk_version", "") == entry.get("sdkVersion", "")
            for citation in citations
        )
        if citation_valid:
            citation_valid_count += 1

        # 3. 参考文档命中率：评测样本期望的知识单元至少被引用一次。
        citation_ids = [citation.get("id", "") for citation in citations]
        reference_cited = any(
            any(reference in citation_id for citation_id in citation_ids)
            for reference in reference_docs
        )
        if reference_cited:
            reference_cited_count += 1

        results.append({
            "qid": qid,
            "correct": is_correct,
            "citation_valid": citation_valid,
            "reference_cited": reference_cited,
            "keyword_ratio": round(keyword_ratio, 2),
            "answer_length": len(answer),
            "time": round(elapsed, 1),
        })

        # 小批量暂停避免限流
        if (i + 1) % 5 == 0 and i + 1 < total:
            time.sleep(2)

    # 汇总
    correctness = correct_count / total
    citation_validity = citation_valid_count / total
    reference_cited_rate = reference_cited_count / total
    avg_time = total_time / total

    metrics = {
        "Answer_Correctness": round(correctness, 4),
        "Citation_Validity": round(citation_validity, 4),
        "Reference_Cited_Rate": round(reference_cited_rate, 4),
        "Avg_Response_Time": round(avg_time, 1),
    }

    print(f"\n  答案正确率: {correct_count}/{total} = {correctness:.2%}")
    print(f"  来源有效率: {citation_valid_count}/{total} = {citation_validity:.2%}")
    print(f"  参考文档命中率: {reference_cited_count}/{total} = {reference_cited_rate:.2%}")
    print(f"  平均响应时间: {avg_time:.1f}s")

    correctness_ok = correctness >= REQUIRED_CORRECTNESS
    citation_validity_ok = citation_validity >= REQUIRED_CITATION_VALIDITY

    print(f"\n  正确性目标: >= {REQUIRED_CORRECTNESS:.0%}  {'✓ 通过' if correctness_ok else '✗ 未通过'}")
    print(f"  来源有效率目标: >= {REQUIRED_CITATION_VALIDITY:.0%}  {'✓ 通过' if citation_validity_ok else '✗ 未通过'}")

    metrics["pass_correctness"] = correctness_ok
    metrics["pass_citation_validity"] = citation_validity_ok

    # 保存详细结果
    output_path = Path(__file__).parent / "eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "retrieval": {"recall_at_5": metrics.get("pass", False)},
            "answer": {
                "correctness": correctness,
                "citation_validity": citation_validity,
                "reference_cited_rate": reference_cited_rate,
            },
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
    print(f"    Citation Validity: {answer_metrics['Citation_Validity']:.2%}")
    print(f"    Reference Cited Rate: {answer_metrics['Reference_Cited_Rate']:.2%}")
    print(f"    Avg Response Time: {answer_metrics['Avg_Response_Time']:.1f}s")

    all_pass = (
        retrieval_metrics.get("pass", False)
        and answer_metrics.get("pass_correctness", False)
        and answer_metrics.get("pass_citation_validity", False)
    )

    print(f"\n  整体结果: {'✓ 全部通过' if all_pass else '✗ 部分未通过'}")
    print("=" * 60)

    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
