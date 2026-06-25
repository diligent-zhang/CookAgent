#!/usr/bin/env python3
"""
CookHero 菜谱数据导入脚本

用法：
    # 导入所有 HowToCook 菜谱
    python scripts/ingest_data.py

    # 预览模式：只解析不写入
    python scripts/ingest_data.py --dry-run

    # 清除已有数据后重新导入
    python scripts/ingest_data.py --clear

    # 只导入特定分类
    python scripts/ingest_data.py --data "data/howtocook/dishes/aquatic/**/*.md"

前提条件：
    1. PostgreSQL 已启动（cookhero 数据库已创建）
    2. Milvus 已启动（端口 19530）
    3. .env 中已配置好数据库密码和 LLM API Key
    4. 已安装所有依赖（pip install -r requirements.txt）
"""
import argparse
import asyncio
import glob
import os
import sys

# 把项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.ingestion import IngestionPipeline
from app.ingestion.parser import HowToCookParser


async def dry_run(data_pattern: str, limit: int = 10):
    """预览模式：只解析不写入，展示前 N 条菜谱结构。"""
    parser = HowToCookParser()
    files = sorted(glob.glob(data_pattern, recursive=True))

    if not files:
        print(f"No files found matching: {data_pattern}")
        return

    total = len(files)
    recipes = []
    for file_path in files:
        recipe = parser.parse_file(file_path)
        if recipe:
            recipes.append(recipe)

    print(f"Found {total} files, parsed {len(recipes)} recipes\n")

    for i, r in enumerate(recipes[:limit]):
        print(f"{'='*60}")
        print(f"  {i+1}. [{r.category}] {r.dish_name}")
        print(f"     难度: {r.metadata.get('difficulty', 'N/A')}")
        print(f"     文件: {r.source}")
        preview = r.content[:200].replace("\n", "\n     ")
        print(f"     内容预览:\n     {preview}...")

    if len(recipes) > limit:
        print(f"\n  ... 还有 {len(recipes) - limit} 道菜未展示")

    # 统计分类
    categories = {}
    for r in recipes:
        cat = r.category
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n{'='*60}")
    print("分类统计:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count} 道菜")
    print(f"  总计: {len(recipes)} 道菜")
    print("\nDry run completed. No data written.")


async def main():
    parser = argparse.ArgumentParser(description="CookHero 菜谱数据导入工具")
    parser.add_argument(
        "--data",
        type=str,
        default="data/howtocook/dishes/**/*.md",
        help="菜谱文件匹配模式（默认: data/howtocook/dishes/**/*.md）",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="清除已有数据后重新导入",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：只解析不写入",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=10,
        help="预览模式下展示的菜谱数量（默认 10）",
    )

    args = parser.parse_args()

    if args.dry_run:
        await dry_run(args.data, limit=args.show)
        return

    pipeline = IngestionPipeline()
    stats = await pipeline.run(
        data_pattern=args.data,
        clear_existing=args.clear,
    )

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
