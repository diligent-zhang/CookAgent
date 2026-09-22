"""
LLM用量日志仓库
"""
import uuid
from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import func, select
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

    # ==== 查询统计 ====

    async def list_logs(
        self,
        user_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[LlmUsageLog]:
        """列出用量日志（按时间倒序）。"""
        stmt = select(LlmUsageLog).order_by(LlmUsageLog.created_at.desc())
        if user_id:
            stmt = stmt.where(LlmUsageLog.user_id == user_id)
        result = await self.session.execute(stmt.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def get_summary(
        self,
        user_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """
        汇总用量：总调用次数、总 token、平均延迟、按模型分组。
        """
        # 收集过滤条件，显式应用到各条查询（避免依赖 SQLAlchemy 内部属性）
        filters = []
        if user_id:
            filters.append(LlmUsageLog.user_id == user_id)
        if start_date:
            filters.append(
                LlmUsageLog.created_at >= datetime.combine(start_date, datetime.min.time())
            )
        if end_date:
            filters.append(
                LlmUsageLog.created_at <= datetime.combine(end_date, datetime.max.time())
            )

        base = select(LlmUsageLog).where(*filters)

        count_v = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        in_v = (
            await self.session.execute(
                select(func.coalesce(func.sum(LlmUsageLog.input_tokens), 0)).where(*filters)
            )
        ).scalar_one()
        out_v = (
            await self.session.execute(
                select(func.coalesce(func.sum(LlmUsageLog.output_tokens), 0)).where(*filters)
            )
        ).scalar_one()
        avg = (
            await self.session.execute(
                select(func.coalesce(func.avg(LlmUsageLog.duration_ms), 0)).where(*filters)
            )
        ).scalar_one()

        # 按模型分组
        stmt = (
            select(
                LlmUsageLog.model_name,
                func.count().label("calls"),
                func.coalesce(func.sum(LlmUsageLog.total_tokens), 0).label("tokens"),
                func.coalesce(func.avg(LlmUsageLog.duration_ms), 0).label("avg_duration_ms"),
            )
            .where(*filters)
            .group_by(LlmUsageLog.model_name)
            .order_by(func.count().desc())
        )
        rows = await self.session.execute(stmt)
        by_model = [
            {
                "model_name": r.model_name or "unknown",
                "calls": r.calls,
                "tokens": r.tokens,
                "avg_duration_ms": round(r.avg_duration_ms, 1) if r.avg_duration_ms else None,
            }
            for r in rows.all()
        ]

        return {
            "total_calls": count_v,
            "total_input_tokens": in_v,
            "total_output_tokens": out_v,
            "total_tokens": in_v + out_v,
            "avg_duration_ms": round(avg, 1) if avg else None,
            "by_model": by_model,
        }