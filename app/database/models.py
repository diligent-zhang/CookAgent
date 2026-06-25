"""
ORM 数据表模型
定义 User、Conversation、Message 三张基础表。
所有模型都继承自 session.py 中的 Base。
"""

import uuid as uuid_module
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.session import Base


def generate_uuid():
    """生成 UUID 字符串作为主键。"""
    return str(uuid_module.uuid4())


class User(Base):
    """
    用户表。
    存储注册用户的基本信息
    """
    __tablename__ = "users"

    # 主键：UUID 字符串（比自增 ID 更适合分布式场景）
    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    # 用户名，必须唯一
    username = Column(String(64), unique=True, nullable=False, index=True)
    # 密码哈希（bcrypt），不存明文
    password_hash = Column(String(255), nullable=False)
    # 昵称（可选）
    nickname = Column(String(128), default="")
    # 注册时间
    created_at = Column(DateTime, default=datetime.utcnow)
    # 最后登录时间
    last_login_at = Column(DateTime, nullable=True)

    # 关系：一个用户有多条对话
    conversations = relationship(
        "Conversation", back_populates="owner", lazy="dynamic"
    )
    agent_sessions = relationship(
        "AgentSession", back_populates="owner", lazy="dynamic"
    )


class Conversation(Base):
    """
    对话表
    每次用户发起一次对话（多轮），创建一条记录
    注意：Agent 模块有自己独立的会话表（agent_sessions），这张表服务于旧版 RAG 对话。
    """
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    # 所属用户
    user_id = Column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 对话标题（从第一条消息自动生成或用户自定义）
    title = Column(String(255), default="新对话")
    # 创建时间
    created_at = Column(DateTime, default=datetime.utcnow)
    # 更新时间
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # 已压缩的消息数（用于上下文压缩）
    compressed_count = Column(Integer, default=0)
    # 压缩后的摘要文本
    compressed_summary = Column(Text, nullable=True)

    # 关系
    owner = relationship("User", back_populates="conversations")
    messages = relationship(
        "Message", back_populates="conversation", lazy="dynamic"
    )


class Message(Base):
    """
    消息表。
    记录对话中的每一条消息。
    """
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    # 所属对话
    conversation_id = Column(
        UUID(as_uuid=False),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 消息角色：user 或 assistant
    role = Column(String(16), nullable=False)
    # 消息内容
    content = Column(Text, nullable=False)
    # 发送时间
    created_at = Column(DateTime, default=datetime.utcnow)
    # Token 使用量（可选，用于统计）
    token_count = Column(Integer, nullable=True)

    # 关系
    conversation = relationship("Conversation", back_populates="messages")


class LlmUsageLog(Base):
    """
    LLM 调用用量日志表。

    每次 LLM 调用完成后，由 callbacks.py 中的 LLMUsageCallbackHandler
    异步写入此表。用于成本分析、性能监控和异常排查。

    不设外键约束原因：
      - 回调写入在后台线程执行，避免外键检查导致的阻塞
      - 即使关联的 user/conversation 被删除，日志仍保留（便于审计）
    """
    __tablename__ = "llm_usage_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)

    # 调用上下文
    request_id = Column(String(36), nullable=False, index=True)
    module_name = Column(String(64), nullable=False)
    user_id = Column(String(36), nullable=True, index=True)
    conversation_id = Column(String(36), nullable=True)

    # 模型信息
    model_name = Column(String(128), nullable=True)
    tool_name = Column(String(128), nullable=True)

    # Token 用量
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)

    # 性能
    duration_ms = Column(Integer, nullable=True)

    # 记录时间
    created_at = Column(DateTime, default=datetime.utcnow)


class RecipeDocument(Base):
    """
    菜谱父文档表。

    这是 Small-to-Large 架构中的"Large"——存储完整的菜谱文档。
    Milvus 里存的是 chunk（小块），检索命中后通过 parent_id 回到这里取完整内容。

    示例：
      id: "doc_001"
      content: "# 家常菜\n## 番茄炒蛋\n番茄炒蛋的做法...\n## 麻婆豆腐\n..."
      dish_name: "家常菜"
      category: "家常菜"
      source: "data/howtocook/dishes/家常菜.md"
    """
    __tablename__ = "recipe_documents"

    id = Column(UUID(as_uuid=False), primary_key=True, default=generate_uuid)
    content = Column(Text, nullable=False, comment="完整文档内容（Markdown）")
    dish_name = Column(String(256), nullable=True, index=True, comment="菜品名称")
    category = Column(String(128), nullable=True, index=True, comment="菜系分类")
    difficulty = Column(String(64), nullable=True, comment="难度")
    source = Column(String(256), nullable=True, comment="来源文件路径")
    data_source = Column(String(64), default="howtocook", comment="数据来源")
    chunk_count = Column(Integer, default=0, comment="切分出的 chunk 数量")
    created_at = Column(DateTime, default=datetime.utcnow)


#   为什么需要这张表：
#   - 阶段3的 callbacks.py 已经收集了每次 LLM 调用的 token 用量和耗时，但
#     _write_to_db 只打日志不写库
#   - 有了这张表，你可以统计每个用户花了多少 token、哪个模型被调用最多、平均延迟是多少
#   - 后续可以接入成本监控（qwen-max 和 qwen-plus 价格不同）
#
#   模块解释
#
#   为什么主键用 UUID 字符串而不是自增 ID？
#   - 自增 ID 在分布式环境会冲突（多台服务器同时写入）
#   - UUID 可以在应用层生成，不需要等数据库分配
#   - 缺点：UUID 作为主键在 B+ 树索引中写入性能不如自增 ID，
#     但对于对话系统这种写少读多的场景完全够用
#
#   lazy="dynamic"：当访问 user.conversations 时，不立即加载所有对话，
#     而是返回一个可继续过滤的 Query 对象。比如
#     user.conversations.filter_by(title="xxx").first()。
#
#   ondelete="CASCADE"：删除用户时，自动级联删除其所有对话；
#     删除对话时，自动级联删除其所有消息。
