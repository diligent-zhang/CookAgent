"""
LLM用量日志仓库
"""
import uuid
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import LlmUsageLog

class UsageRepository:
    """LLM调用日志的数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_log(self, data: Dict[str, Any]) -> LlmUsageLog:
        """创建一条用量日志。"""
        log = LlmUsageLog(
            id=str(uuid.uuid4()),
            **data,
        )
        self.session.add(log)
        await self.session.commit()
        return log