"""
meal_plan_tools 纯函数单元测试（不依赖 Docker / 数据库 / LLM）。

直接按文件路径加载模块，绕过 app.agent.tools.__init__ 的重依赖。
若环境未安装 langchain_core，自动注入最小桩（@tool 直通装饰器）。

运行：python -m unittest tests.test_meal_plan_tools -v
  或：python tests/test_meal_plan_tools.py
"""
import importlib.util
import os
import sys
import types
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_module():
    try:
        import langchain_core.tools  # noqa: F401
    except ModuleNotFoundError:
        pkg = types.ModuleType("langchain_core")
        tools_mod = types.ModuleType("langchain_core.tools")

        def _tool_stub(func=None, **kwargs):
            if func is None:
                return lambda f: f
            return func

        tools_mod.tool = _tool_stub
        pkg.tools = tools_mod
        sys.modules["langchain_core"] = pkg
        sys.modules["langchain_core.tools"] = tools_mod

    path = os.path.join(REPO_ROOT, "app", "agent", "tools", "meal_plan_tools.py")
    spec = importlib.util.spec_from_file_location("meal_plan_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpt = _load_module()

SAMPLE_PLAN = """# 3天饮食计划

> 每日目标：每日约 1500 千卡 | 饮食限制：鸡蛋过敏 | 偏好：家常菜

## 第 1 天（2026-09-09）
- **早餐**：小米粥（约150千卡）
  清淡养胃
- **午餐**：番茄炒蛋, 紫菜蛋花汤（约450千卡）
  荤素搭配
- **晚餐**：清蒸鲈鱼; 凉拌黄瓜（约400千卡）
  优质蛋白

## 第 2 天（2026-09-10）
- **早餐**：豆浆配油条（约300千卡）
  经典早餐
- **午餐**：宫保鸡丁（约550千卡）
  高蛋白
- **晚餐**：冬瓜排骨汤（约380千卡）
  易消化

## 第 3 天（2026-09-11）
- **早餐**：牛奶麦片（约250千卡）
- **午餐**：清炒时蔬（约200千卡）
- **晚餐**：红烧带鱼（约500千卡）

---

### 健康小贴士
多喝水，少油<少盐。
"""


class TestFilenameSafety(unittest.TestCase):
    def test_accepts_generated_names(self):
        for ext in ("md", "ics", "html"):
            name = mpt._make_export_filename(ext)
            self.assertTrue(
                mpt.is_safe_plan_filename(name),
                f"生成的文件名未通过白名单: {name}",
            )

    def test_rejects_traversal_and_garbage(self):
        bad = [
            "..\\..\\..\\app\\main.py",
            "../../../etc/passwd",
            "meal_plan_20260909.md",          # 旧格式（无 uuid）不再接受
            "meal_plan_20260909_deadbeef.exe",
            "meal_plan_20260909_deadbeef.md.html",
            "meal_plan_ABCD1234_12345678.md",  # 日期非数字
            "",
            ".md",
        ]
        for name in bad:
            self.assertFalse(
                mpt.is_safe_plan_filename(name),
                f"应被拒绝: {name}",
            )

    def test_storage_dir_is_absolute(self):
        self.assertTrue(os.path.isabs(mpt.get_plan_storage_dir()))


class TestParsePlanMarkdown(unittest.TestCase):
    def test_extracts_three_days(self):
        days = mpt._parse_plan_markdown(SAMPLE_PLAN)
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0]["day"], 1)
        self.assertEqual(days[0]["date"], "2026-09-09")

    def test_extracts_meals_and_calories(self):
        days = mpt._parse_plan_markdown(SAMPLE_PLAN)
        meals = days[0]["meals"]
        self.assertEqual([m["type"] for m in meals], ["早餐", "午餐", "晚餐"])
        self.assertEqual(meals[0]["dish"], "小米粥")
        self.assertEqual(meals[0]["calories"], 150)

    def test_dish_names_keep_comma_and_semicolon(self):
        days = mpt._parse_plan_markdown(SAMPLE_PLAN)
        lunch = days[0]["meals"][1]["dish"]
        dinner = days[0]["meals"][2]["dish"]
        self.assertIn(",", lunch)
        self.assertIn(";", dinner)

    def test_empty_on_garbage(self):
        self.assertEqual(mpt._parse_plan_markdown("随便一段文字"), [])


class TestIcsExport(unittest.TestCase):
    def setUp(self):
        days = mpt._parse_plan_markdown(SAMPLE_PLAN)
        self.ics = mpt._plan_to_ics(days)

    def test_crlf_line_endings(self):
        # 每一行都应以 CRLF 结束，且文本中不应有裸 \n
        self.assertTrue(self.ics.endswith("\r\n"))
        lines = self.ics.split("\r\n")
        for line in lines:
            self.assertNotIn("\n", line)

    def test_text_values_escaped(self):
        self.assertIn("番茄炒蛋\\, 紫菜蛋花汤", self.ics)
        self.assertIn("清蒸鲈鱼\\; 凉拌黄瓜", self.ics)

    def test_lines_within_75_octets(self):
        for line in self.ics.split("\r\n"):
            if line.startswith(" "):  # 折叠续行
                pass
            self.assertLessEqual(
                len(line.encode("utf-8")), 75,
                f"超长未折叠: {line!r}",
            )

    def test_event_count(self):
        self.assertEqual(self.ics.count("BEGIN:VEVENT"), 9)

    def test_folding_keeps_valid_utf8_per_line(self):
        # 折叠后每个物理行都必须能独立 decode（不切断多字节字符）
        for line in self.ics.split("\r\n"):
            line.encode("utf-8").decode("utf-8")  # 不应抛异常


class TestHtmlExport(unittest.TestCase):
    def test_escapes_dynamic_content(self):
        html = mpt._plan_to_html(SAMPLE_PLAN)
        self.assertIn("少油&lt;少盐", html)
        self.assertIn("小米粥", html)
        self.assertNotIn("<script>", html)

    def test_injected_script_is_escaped(self):
        evil = SAMPLE_PLAN.replace("小米粥", "<script>alert(1)</script>")
        html = mpt._plan_to_html(evil)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_contains_all_meal_cards(self):
        html = mpt._plan_to_html(SAMPLE_PLAN)
        self.assertEqual(html.count('class="meal-card'), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
