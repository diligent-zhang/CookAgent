"""
菜谱相关 Tool：搜索菜谱、获取菜谱详情

 每个 Tool 函数需要：
   1. 类型标注（LangChain 用它生成 tool schema）
   2. 文档字符串（LangChain 用它生成 tool description）
   3. 返回字符串（Agent 的 observation）
"""
from typing import Optional

from langchain_core.documents import Document
from langchain_core.tools import tool

# RAGService 实例引用，由 service.py 在运行时注入
_rag_service = None


def set_rag_service(rag_service):
    """运行时注入 RAG 服务（避免循环导入）。"""
    global _rag_service
    _rag_service = rag_service


@tool
async def search_recipes(
    query: str,
    category: Optional[str] = None,
    difficulty: Optional[str] = None,
    max_results: int = 3,
) -> str:
    """
    搜索菜谱。根据用户的查询（菜名、食材、口味等）从菜谱库中检索相关菜谱。

    适用于：
    - 用户想做某道菜但不知道怎么做
    - 用户想根据食材找菜谱（如"土豆能做什么"）
    - 用户想根据场景找菜谱（如"夏天清淡的菜"）

    参数：
        query: 搜索关键词，如"番茄炒蛋"、"土豆的做法"、"夏天清淡"
        category: 菜系分类，可选值：家常菜、川菜、粤菜、鲁菜、苏菜、浙菜、闽菜、湘菜
        difficulty: 难度，可选值：简单、中等、困难
        max_results: 最多返回几条结果，默认 3
    """
    if _rag_service is None:
        return "错误: 菜谱检索服务未初始化"

    # 构建过滤表达式
    expr_parts = []
    if category:
        expr_parts.append(f'category == "{category}"')
    if difficulty:
        expr_parts.append(f'difficulty == "{difficulty}"')

    expr = " and ".join(expr_parts) if expr_parts else None

    try:
        docs = await _rag_service.retrieve(
            query=query,
            top_k=max_results,
            expr=expr,
        )
    except Exception as e:
        return f"检索菜谱时出错: {e}"

    if not docs:
        return f"没有找到与「{query}」相关的菜谱。建议换一个关键词试试。"

    # 格式化返回结果
    parts = [f"找到 {len(docs)} 个相关菜谱：\n"]
    for i, doc in enumerate(docs, 1):
        name = doc.metadata.get("dish_name", "未知菜品")
        cat = doc.metadata.get("category", "")
        diff = doc.metadata.get("difficulty", "")
        score = doc.metadata.get("retrieval_score", 0.0)
        # 格式化，以 JSON 格式输出
        parts.append(f"--- {i}. {name} (分类: {cat}, 难度: {diff}, 相关度: {score:.2f}) ---")
        # 只取前 500 字作为摘要
        parts.append(doc.page_content[:500])
        parts.append("")
    return "\n".join(parts)


@tool
async def get_recipe_detail(dish_name: str) -> str:
    """
     获取某道菜的完整做法。当用户对某道菜感兴趣，想了解详细步骤时调用。

      参数：
          dish_name: 菜名，如"番茄炒蛋"

    """
    if _rag_service is None:
        return "错误: 菜谱检索服务未初始化"
    try:
        docs = await _rag_service.retrieve(
            query = dish_name,
            top_k = 1

        )

    except Exception as e:
        return f"获取菜谱详情时出错:{e}"
    if not docs:
        return f"没有找到「{dish_name}」的完整菜谱。请确认菜名是否正确。"
    doc = docs[0]
    return (
        f"# {dish_name}\n"
        f"分类: {doc.metadata.get('category', '未知')}\n"  #未知作为兜底操作
        f"难度: {doc.metadata.get('difficulty', '未知')}\n\n"
        f"{doc.page_content}"
    )
