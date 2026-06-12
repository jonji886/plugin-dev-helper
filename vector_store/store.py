"""
向量存储模块

使用 Chroma + sentence-transformers 构建向量索引和检索
"""

import json
import os
import traceback
from pathlib import Path
from typing import Optional

# 强制使用本地缓存的 HuggingFace 模型，避免网络超时
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import chromadb
from chromadb.config import Settings


class VectorStore:
    """SDK 知识库向量存储"""

    def __init__(self, persist_dir: str = "data/chroma", collection_name: str = "sdk_knowledge"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        # 初始化 Chroma client
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

        # 获取或创建集合
        try:
            self.collection = self.client.get_collection(collection_name)
        except Exception:
            self.collection = self.client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )

        # Embedding 模型（延迟加载）
        self._embedding_model = None

    @property
    def embedding_model(self):
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as e:
                print(f"[vector] embedding model load failed: {e}")
                print(traceback.format_exc())
                raise
        return self._embedding_model

    def build_index(self, knowledge_index: list[dict], knowledge_dir: str = "data/knowledge"):
        """从知识库索引构建向量索引"""
        knowledge_dir = Path(knowledge_dir)

        # 先删除旧集合重新构建
        self.delete_collection()
        self.collection = self.client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # 去重（同一ID在多个 chunk 中可能出现）
        seen_ids = set()
        unique_entries = []
        for entry in knowledge_index:
            if entry["id"] not in seen_ids:
                seen_ids.add(entry["id"])
                unique_entries.append(entry)

        documents = []
        metadatas = []
        ids = []

        for entry in unique_entries:
            # 读取 Markdown 内容
            md_file = knowledge_dir / entry["mdFile"]
            if not md_file.exists():
                continue

            content = md_file.read_text(encoding="utf-8")

            # 构建文档文本 (Markdown + 别名 + 描述用于检索)
            search_text = content
            aliases = entry.get("aliases", [])
            if aliases:
                search_text += "\n\n别名: " + ", ".join(aliases)
            desc = entry.get("description", "")
            if desc:
                search_text += "\n\n描述: " + desc

            documents.append(search_text)
            metadatas.append({
                "id": entry["id"],
                "name": entry["name"],
                "type": entry["type"],
                "namespace": entry["namespace"],
                "source": entry.get("source", ""),
                "sdkVersion": entry.get("sdkVersion", ""),
                "aliases": ",".join(aliases[:5]),
                "is_overview": entry.get("is_overview", False),
            })
            ids.append(entry["id"])

        if not documents:
            print("没有知识单元需要索引")
            return

        # 批量添加（避免内存溢出）
        batch_size = 100
        total_batches = (len(documents) + batch_size - 1) // batch_size

        print(f"开始构建向量索引...")
        print(f"  总文档数: {len(documents)}")
        print(f"  分批处理: {total_batches} 批, 每批 {batch_size} 个")

        for i in range(0, len(documents), batch_size):
            batch_end = min(i + batch_size, len(documents))
            batch_docs = documents[i:batch_end]
            batch_metas = metadatas[i:batch_end]
            batch_ids = ids[i:batch_end]

            # 生成 batch Embedding
            embeddings = self.embedding_model.encode(batch_docs, show_progress_bar=True).tolist()

            # 添加到 Chroma
            self.collection.add(
                embeddings=embeddings,
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids,
            )

            print(f"  批次 {i // batch_size + 1}/{total_batches} 完成")

        print(f"\n向量索引构建完成!")
        print(f"  集合: {self.collection_name}")
        print(f"  文档数: {self.collection.count()}")

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """搜索最相关的知识单元"""
        try:
            # 生成查询 embedding
            query_embedding = self.embedding_model.encode([query]).tolist()[0]

            # 检索
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self.collection.count()),
            )
        except Exception as e:
            print(f"[vector] search failed: {e}")
            print(traceback.format_exc())
            raise

        # 格式化结果
        formatted = []
        for i in range(len(results["ids"][0])):
            formatted.append({
                "id": results["ids"][0][i],
                "metadata": results["metadatas"][0][i],
                "document": results["documents"][0][i][:500],  # 截断显示
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })

        return formatted

    def search_with_boost(self, query: str, top_k: int = 5, boost_overview: bool = False) -> list[dict]:
        """
        搜索最相关的知识单元，支持对 overview 文档加权。

        Args:
            query: 查询文本
            top_k: 返回结果数量
            boost_overview: 是否对总览型文档加权

        Returns:
            排序后的检索结果列表
        """
        # 多取候选用于重排（扩大到 50 个以确保 overview 文档有机会进入候选）
        candidate_count = min(max(top_k * 10, 50), self.collection.count())
        results = self.search(query, top_k=max(candidate_count, top_k))

        if not boost_overview or not results:
            return results[:top_k]

        # 对 overview 文档加权（距离越小越相似，乘以 0.8 提升排名）
        for r in results:
            if r.get("metadata", {}).get("is_overview"):
                r["distance"] = r.get("distance", 1.0) * 0.8

        # 按距离重新排序
        results.sort(key=lambda x: x.get("distance", 1.0))
        return results[:top_k]

    def count(self) -> int:
        """返回文档数量"""
        return self.collection.count()

    def delete_collection(self):
        """删除集合"""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
