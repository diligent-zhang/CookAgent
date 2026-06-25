"""
HowToCook Markdown 菜谱解析器

实际 HowToCook 格式（每道菜一个独立文件）：
  # 咖喱炒蟹的做法                ← 一级标题 = 菜名
  这是一道融合泰式风味的家常海鲜菜...  ← 描述
  预估烹饪难度：★★★★              ← 难度
  ## 必备原料和工具                ← 二级标题 = 原料段落
  - 青蟹
  ...
  ## 计算                         ← 二级标题 = 用量
  ...
  ## 操作                         ← 二级标题 = 步骤
  ...
  ## 附加内容                     ← 二级标题 = 附加信息

目录结构：
  dishes/
    aquatic/          → 分类: 水产
      xxx.md
    vegetable_dish/   → 分类: 蔬菜
      xxx.md
    ...

解析目标：每个 .md 文件 → 一个 ParsedRecipe → 一个父文档 → N 个 chunk
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# 目录名 → 中文分类名
CATEGORY_CN = {
    "aquatic": "水产",
    "breakfast": "早餐",
    "condiment": "酱料",
    "dessert": "甜品",
    "drink": "饮品",
    "meat_dish": "荤菜",
    "semi-finished": "半成品",
    "soup": "汤类",
    "staple": "主食",
    "vegetable_dish": "蔬菜",
    "家常菜": "家常菜",  # 兼容自定义数据
}


@dataclass
class ParsedRecipe:
    """解析后的一道菜谱。"""
    dish_name: str
    content: str
    category: str
    source: str
    metadata: Dict = field(default_factory=dict)


class HowToCookParser:
    """
    HowToCook 单文件菜谱解析器。

    用法：
        parser = HowToCookParser()
        recipe = parser.parse_file("dishes/aquatic/咖喱炒蟹.md")
        print(recipe.dish_name)   # "咖喱炒蟹"
        print(recipe.category)    # "水产"
    """

    DIFFICULTY_KEYWORDS = {
        "简单": ["简单", "简易", "快手", "懒人", "新手"],
        "中等": ["中等", "普通", "家常"],
        "困难": ["困难", "复杂", "费时", "功夫", "宴客"],
    }

    def parse_file(self, file_path: str) -> Optional[ParsedRecipe]:
        """解析一个菜谱文件，返回单条 ParsedRecipe，解析失败返回 None。"""
        if not os.path.exists(file_path):
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return None

        return self._parse_recipe_content(content, source=file_path)

    def parse_directory(
        self, root_path: str, pattern: str = "*.md"
    ) -> List[ParsedRecipe]:
        """递归解析目录下所有菜谱文件。"""
        import glob
        recipes = []
        search_path = os.path.join(root_path, "**", pattern)
        for file_path in glob.glob(search_path, recursive=True):
            recipe = self.parse_file(file_path)
            if recipe:
                recipes.append(recipe)
        return recipes

    def _parse_recipe_content(
        self, content: str, source: str = ""
    ) -> Optional[ParsedRecipe]:
        """
        解析单道菜的 Markdown 内容。

        逻辑：
        1. 第一行 `# xxx` → 菜名
        2. 其余内容 → 完整正文
        3. 从文件路径提取分类
        """
        lines = content.split("\n")

        # 找一级标题作为菜名
        dish_name = ""
        content_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                dish_name = stripped[2:].strip()
                # 去掉后缀如"的做法"
                dish_name = re.sub(r"的做法$", "", dish_name)
                content_start = i
                break

        if not dish_name:
            return None

        # 完整内容（包含标题行，保持 Markdown 结构）
        full_content = "\n".join(lines[content_start:])

        # 从文件路径提取分类
        category = self._extract_category(source)

        # 从内容中提取难度
        difficulty = self._extract_difficulty(full_content)

        return ParsedRecipe(
            dish_name=dish_name,
            content=full_content,
            category=category,
            source=source,
            metadata={
                "dish_name": dish_name,
                "category": category,
                "difficulty": difficulty,
                "source": source,
                "data_source": "howtocook",
                "is_dish_index": False,
            },
        )

    def _extract_category(self, file_path: str) -> str:
        """从文件路径中提取分类。例如 dishes/aquatic/xxx.md → '水产'。"""
        parts = file_path.replace("\\", "/").split("/")
        # 找 dishes 后面的那级目录名
        for i, part in enumerate(parts):
            if part == "dishes" and i + 1 < len(parts):
                dir_name = parts[i + 1]
                return CATEGORY_CN.get(dir_name, dir_name)
        return "其他"

    def _extract_difficulty(self, content: str) -> str:
        """
        从内容中提取难度。

        HowToCook 的难度格式：
          - 预估烹饪难度：★（简单，1-2星）
          - 预估烹饪难度：★★★★（困难，4-5星）
        """
        # 先尝试匹配星标格式
        match = re.search(r"预估烹饪难度[:：]\s*([★☆★☆]+)", content)
        if match:
            stars = match.group(1)
            star_count = len(stars)
            if star_count <= 2:
                return "简单"
            elif star_count <= 3:
                return "中等"
            else:
                return "困难"

        # 回退到关键词匹配
        for level, keywords in self.DIFFICULTY_KEYWORDS.items():
            for kw in keywords:
                if kw in content:
                    return level
        return "中等"
