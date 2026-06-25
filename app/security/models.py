"""
审计日志 ORM 模型。

这段代码定义了 audit_logs 表的数据库结构。
作用是记录每一次安全相关事件（请求放行、请求拦截、登录失败等），
为后续的安全分析和问题排查提供数据基础。

技术要点：
  - UUID 主键：分布式友好，应用层生成不依赖数据库自增
  - 不设外键约束：审计表是"只写不删"的日志表，关联的 user 可能被删除，
    但审计记录应该保留。外键会阻止这种删除或导致级联删除日志。
  - input_summary 截断到 500 字符：防止恶意超长输入撑爆存储
  - indexed on user_id + created_at：最常见的查询是"某用户最近的操作"
"""

import uuid as uuid_module
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database.session import Base


def generate_uuid():
    """生成 UUID 字符串作为主键。"""
    return str(uuid_module.uuid4())


class AuditLog(Base):
    """
    审计日志表。

    字段说明：
      user_id     — 触发操作的用户（可为空，比如未登录的请求）
      action      — 操作类型：chat / agent / login / register
      endpoint    — 请求路径
      status      — allowed（放行）/ blocked（拦截）/ error（异常）
      block_reason— 拦截原因：rate_limited / sql_injection / jailbreak ...
      input_summary — 用户输入摘要（截断到 500 字符，存前缀便于排查）
      ip_address  — 客户端 IP
      user_agent  — 客户端浏览器/设备信息
      duration_ms — 整个安全检测耗时（毫秒）
      token_count — 本次请求消耗的 token（如果有）
      created_at  — 记录时间
    """
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), nullable=True, index=True)
    action = Column(String(50), nullable=False)
    endpoint = Column(String(200), nullable=True)
    status = Column(String(20), nullable=False, default="allowed")
    block_reason = Column(String(200), nullable=True)
    input_summary = Column(String(500), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
