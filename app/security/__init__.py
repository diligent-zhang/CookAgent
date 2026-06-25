"""
安全模块统一导出。

这段代码是 security 包的入口文件。
作用是为外部模块提供统一、干净的 import 路径。

用法：
  from app.security import guard_manager, schedule_audit
  # 而不是：
  from app.security.guard import guard_manager
  from app.security.audit import schedule_audit

技术要点：__all__ 明确声明公开接口，IDE 和 linter 不会报"未使用的导入"。
"""

from app.security.guard import guard_manager
from app.security.audit import schedule_audit

__all__ = ["guard_manager", "schedule_audit"]
