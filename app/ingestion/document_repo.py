"""
菜谱父文档的 PostgreSQL 仓库

存入和读取完整菜谱文档。检索阶段通过 parent_id 找回它。
"""
import uuid
import logging
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RecipeDocument

logger = logging.getLogger(__name__)


class RecipeDocumentRepository:
    """菜谱父文档的 CRUD 仓库。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, doc_data: Dict[str, Any]) -> RecipeDocument:
        """插入一条父文档记录。"""
        doc = RecipeDocument(
            id=str(uuid.uuid4()),
            **doc_data,
        )
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def get_by_ids(self, doc_ids: List[str]) -> List[RecipeDocument]:
        """批量获取父文档。用于 Small-to-Large 的父文档还原。"""
        if not doc_ids:
            return []
        result = await self.session.execute(
            select(RecipeDocument).where(RecipeDocument.id.in_(doc_ids))
        )
        return list(result.scalars().all())

    async def get_by_id(self, doc_id: str) -> Optional[RecipeDocument]:
        """获取单个父文档。"""
        result = await self.session.execute(
            select(RecipeDocument).where(RecipeDocument.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def count_by_source(self, source: str) -> int:
        """统计某来源的文档数。"""
        result = await self.session.execute(
            select(func.count()).where(RecipeDocument.source == source)
        )
        return result.scalar() or 0

    async def delete_by_source(self, source: str) -> int:
        """删除某来源的所有文档（用于重新导入）。"""
        result = await self.session.execute(
            delete(RecipeDocument).where(RecipeDocument.source == source)
        )
        await self.session.commit()
        return result.rowcount
