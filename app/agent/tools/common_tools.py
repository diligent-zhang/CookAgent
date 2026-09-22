"""
常用工具：日期时间、计算器

纯本地实现，无需外部 API，任何 Agent 都可以直接使用。
"""
import ast
import operator
from datetime import datetime

from langchain_core.tools import tool


@tool
async def get_current_datetime() -> str:
    """
    获取当前的日期和时间（北京时间）。当用户询问"今天星期几"、"现在几点"、
    "当前日期"时需要调用，也可用于判断当前季节、安排任务的日期推理。

    返回：当前日期、星期、时间。
    """
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} "
        f"{weekdays[now.weekday()]}"
    )


# 计算器支持的操作符
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _safe_eval(node: ast.AST, depth: int = 0) -> float:
    """在 AST 层面安全求值，只允许四则运算、幂、取模、括号和负数。"""
    if depth > 10:
        raise ValueError("表达式过深")
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, depth + 1)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只支持数字常量")
    if isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return op(_safe_eval(node.left, depth + 1), _safe_eval(node.right, depth + 1))
    if isinstance(node, ast.UnaryOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_safe_eval(node.operand, depth + 1))
    if isinstance(node, tuple(getattr(ast, name) for name in ("BoolOp", "IfExp"))):
        raise ValueError("不支持布尔运算和条件表达式")
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


@tool
async def calculator(expression: str) -> str:
    """
    计算数学表达式。当用户请求做数学计算时调用，支持加减乘除、幂、取模、
    括号和负数（如 "3*4+5"、"2**10"、"15/2+(3*4)"）。

    参数：
        expression: 数学表达式字符串，如 "3 * 4 + 12 / 3"
    """
    if not expression or len(expression) > 100:
        return "错误：表达式为空或过长。"
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree)
        # 处理浮点显示：整数结果不带小数
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return str(result)
    except SyntaxError:
        return "错误：表达式语法不正确，请检查后重试。"
    except ZeroDivisionError:
        return "错误：除数不能为零。"
    except (ValueError, TypeError) as e:
        return f"错误：{e}"