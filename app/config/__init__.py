# 配置模块
# 全局单例 settings 在 config.py 中定义，
# 其他模块通过 from app.config import settings 统一访问配置
from app.config.config import settings
__all__ = ["settings"]