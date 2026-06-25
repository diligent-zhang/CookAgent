"""
数据导入管道

完整流程：
  HowToCook Markdown 文件（每个文件=一道菜）
    → HowToCookParser 解析
    → 批量写入 RecipeDocument 表（PostgreSQL）
    → DocumentProcessor 切分成 chunk
    → 批量写入 Milvus（自动生成 Dense + Sparse 向量）

设计原则：
  - 幂等性：重复导入同一批数据不会产生重复记录
  - 批量写入 PG，每 50 条提交一次
  - 批量写入 Milvus，每 200 个 chunk 写一次
  - 进度反馈：实时打印进度
"""
import asyncio
import glob
import logging
import time
from typing import List

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import app.agent.models  # noqa: F401 确保 Agent 模型在 mapper 初始化前注册
from app.config import settings
from app.database.session import AsyncSessionLocal
from app.ingestion.document_repo import RecipeDocumentRepository
from app.ingestion.parser import HowToCookParser
from app.rag.embeddings.embedding_factory import get_embedding_model
from app.rag.pipeline.document_processor import DocumentProcessor
from app.rag.vector_stores.vector_store_factory import get_vector_store

logger = logging.getLogger(__name__)

PG_BATCH_SIZE = 50       # PostgreSQL 每批写入数量
MILVUS_BATCH_SIZE = 200  # Milvus 每批写入 chunk 数


class IngestionPipeline:
    """
    数据导入管道。

    用法：
        pipeline = IngestionPipeline()
        await pipeline.run("data/howtocook/dishes/**/*.md")
    """

    def __init__(self):
        self.parser = HowToCookParser()
        self.processor = DocumentProcessor()
        self.embeddings: Embeddings | None = None
        self.stats = {
            "files": 0,
            "recipes": 0,
            "chunks": 0,
            "skipped": 0,
            "errors": 0,
            "start_time": 0.0,
            "elapsed": 0.0,
        }

    async def run(
        self,
        data_pattern: str = "data/howtocook/dishes/**/*.md",
        clear_existing: bool = False,
    ) -> dict:
        """
        执行数据导入。

        参数：
            data_pattern: 文件匹配模式
            clear_existing: 是否清除已有数据后重新导入
        """
        self.stats["start_time"] = time.time()

        # 1. 加载 Embedding 模型
        print("[INGESTION] Loading embedding model...")
        self.embeddings = get_embedding_model(settings.embedding.model_name)
        print(f"[INGESTION] Embedding model loaded: {settings.embedding.model_name}")

        # 2. 查找所有菜谱文件
        files = sorted(glob.glob(data_pattern, recursive=True))
        if not files:
            print(f"[INGESTION] No files found matching: {data_pattern}")
            return self.stats

        total = len(files)
        print(f"[INGESTION] Found {total} recipe files\n")
        self.stats["files"] = total

        # 3. 逐文件解析，分批写 PG，收集所有 chunk
        pg_batch: List = []
        all_chunks: List[Document] = []  # 收集所有 chunk，最后一次性写 Milvus
        last_print = time.time()

        async with AsyncSessionLocal() as db:
            doc_repo = RecipeDocumentRepository(db)

            if clear_existing:
                print("[INGESTION] Clearing existing recipe data...")
                for file_path in files:
                    await doc_repo.delete_by_source(file_path)
                print("[INGESTION] Existing data cleared")

            for i, file_path in enumerate(files, 1):
                try:
                    recipe = self.parser.parse_file(file_path)
                except Exception as e:
                    logger.error(f"Parse error {file_path}: {e}")
                    self.stats["errors"] += 1
                    continue

                if recipe is None:
                    self.stats["skipped"] += 1
                    continue

                # 积累 PG 批量数据
                pg_batch.append({
                    "content": recipe.content,
                    "dish_name": recipe.dish_name,
                    "category": recipe.category,
                    "difficulty": recipe.metadata.get("difficulty", ""),
                    "source": recipe.source,
                    "data_source": "howtocook",
                })

                # 切 chunk，暂存所有 metadata
                try:
                    chunks = self.processor.create_chunks(
                        doc_id="",
                        content=recipe.content,
                        metadata=recipe.metadata,
                    )
                    for chunk in chunks:
                        chunk.metadata["_recipe_source"] = recipe.source
                        chunk.metadata["_recipe_name"] = recipe.dish_name
                    all_chunks.extend(chunks)
                except Exception as e:
                    logger.error(f"Chunk error {recipe.dish_name}: {e}")

                self.stats["recipes"] += 1

                # ---- 每 PG_BATCH_SIZE 条写一次 PG ----
                if len(pg_batch) >= PG_BATCH_SIZE:
                    await self._flush_pg_batch(db, doc_repo, pg_batch, all_chunks)
                    pg_batch.clear()

                # ---- 进度打印 ----
                now = time.time()
                if now - last_print > 3 or i % 20 == 0 or i == total:
                    elapsed = now - self.stats["start_time"]
                    rate = i / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{i}/{total}] {rate:.0f} files/s | "
                        f"{self.stats['recipes']} recipes | "
                        f"{len(all_chunks)} chunks | "
                        f"{self.stats['errors']} errors",
                        end="\r",
                    )
                    last_print = now

            # ---- 处理剩余 PG 批次 ----
            if pg_batch:
                await self._flush_pg_batch(db, doc_repo, pg_batch, all_chunks)
                pg_batch.clear()

            # 修正所有 chunk 的 parent_id
            self._fix_parent_ids(all_chunks)

        # 4. 一次性将所有 chunk 写入 Milvus
        print(f"\n[INGESTION] Writing {len(all_chunks)} chunks to Milvus...")
        await self._write_all_to_milvus(all_chunks)

        self.stats["elapsed"] = time.time() - self.stats["start_time"]

        # 打印统计
        print(f"\n\n{'='*50}")
        print(f"[INGESTION] Import completed!")
        print(f"  Files scanned:    {self.stats['files']}")
        print(f"  Recipes created:  {self.stats['recipes']}")
        print(f"  Chunks generated: {self.stats['chunks']}")
        print(f"  Skipped:          {self.stats['skipped']}")
        print(f"  Errors:           {self.stats['errors']}")
        print(f"  Time elapsed:     {self.stats['elapsed']:.1f}s")
        print(f"{'='*50}")

        return self.stats

    async def _flush_pg_batch(self, db, doc_repo, pg_batch, all_chunks):
        """批量写入 PostgreSQL 父文档。"""
        source_to_id = {}  # source → parent_doc_id 映射

        for data in pg_batch:
            try:
                parent_doc = await doc_repo.insert(data)
                source_to_id[data["source"]] = parent_doc.id
                source_chunks = [
                    c for c in all_chunks
                    if c.metadata.get("_recipe_source") == data["source"]
                ]
                parent_doc.chunk_count = len(source_chunks)
            except Exception as e:
                logger.error(f"PG insert error {data['dish_name']}: {e}")
                self.stats["errors"] += 1

        await db.commit()

        # 修正 chunk 的 parent_id
        for chunk in all_chunks:
            source = chunk.metadata.get("_recipe_source", "")
            if source in source_to_id:
                chunk.metadata["parent_id"] = source_to_id[source]
                chunk.metadata["_parent_id_set"] = True

    def _fix_parent_ids(self, all_chunks: List[Document]):
        """清理 chunk 内部标记字段。"""
        for chunk in all_chunks:
            chunk.metadata.pop("_recipe_source", None)
            chunk.metadata.pop("_recipe_name", None)
            chunk.metadata.pop("_parent_id_set", None)

    async def _write_all_to_milvus(self, chunks: List[Document]):
        """一次性将所有 chunk 写入 Milvus（通过 from_documents 创建 Collection 并插入）。"""
        if not chunks:
            return

        collection_name = settings.vector_store.collection_names["recipes"]

        await asyncio.to_thread(
            get_vector_store,
            collection_name=collection_name,
            embeddings=self.embeddings,
            chunks=chunks,
        )

        self.stats["chunks"] = len(chunks)
        print(f"[INGESTION] {len(chunks)} chunks written to Milvus collection '{collection_name}'")
