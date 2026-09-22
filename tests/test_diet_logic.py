"""
diet 模块纯函数单元测试（不依赖 PostgreSQL/docker 数据库连接）。

通过真实包导入 app.diet（依赖 sqlalchemy 等重依赖），但只调用纯逻辑函数，
不触发数据库连接。若环境缺失依赖，测试会 SKIP 而不是失败。

运行：python -m unittest tests.test_diet_logic -v
"""
import sys
import unittest

try:
    from datetime import date

    from app.diet.repository import _sum_dishes, get_week_start
    from app.diet.service import _build_log_dict, _parse_ai_json
    from app.agent.tools.common_tools import calculator, get_current_datetime
    DEPS_OK = True
except Exception as e:  # pragma: no cover
    DEPS_OK = False
    _IMPORT_ERR = e


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestWeekStart(unittest.TestCase):
    def test_monday(self):
        self.assertEqual(get_week_start(date(2026, 9, 14)), date(2026, 9, 14))

    def test_midweek(self):
        self.assertEqual(get_week_start(date(2026, 9, 17)), date(2026, 9, 14))

    def test_sunday(self):
        self.assertEqual(get_week_start(date(2026, 9, 20)), date(2026, 9, 14))


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestParseAiJson(unittest.TestCase):
    def test_plain_json(self):
        r = _parse_ai_json('{"meal_type": "lunch", "items": []}')
        self.assertEqual(r["meal_type"], "lunch")

    def test_markdown_wrapped(self):
        r = _parse_ai_json('```json\n{"meal_type": "breakfast"}\n```')
        self.assertEqual(r["meal_type"], "breakfast")

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_ai_json("这不是json"))


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestBuildLogDict(unittest.TestCase):
    def test_aggregates_totals(self):
        class FakeItem:
            def __init__(self, log_id, log_date, meal_type, cal, pro):
                from datetime import datetime
                self.log_id = log_id
                self.log_date = log_date
                self.meal_type = meal_type
                self.calories = cal
                self.protein = pro
                self.fat = 0
                self.carbs = 0
                self.notes = None
                self.created_at = datetime(2026, 9, 14, 12, 0, 0)

            def to_dict(self):
                return {"food_name": "x", "calories": self.calories}

        items = [
            FakeItem("log1", date(2026, 9, 14), "lunch", 100, 10),
            FakeItem("log1", date(2026, 9, 14), "lunch", 200, 20),
        ]
        r = _build_log_dict([items[0], items[1]])
        self.assertEqual(r["log_id"], "log1")
        self.assertEqual(r["total_calories"], 300)
        self.assertEqual(r["total_protein"], 30)


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestSumDishes(unittest.TestCase):
    def test_sums_nutrition(self):
        dishes = [{"calories": 280, "protein": 35}, {"calories": 80, "protein": 5}]
        t = _sum_dishes(dishes)
        self.assertEqual(t["calories"], 360)
        self.assertEqual(t["protein"], 40)

    def test_empty(self):
        self.assertIsNone(_sum_dishes(None)["calories"])
        self.assertIsNone(_sum_dishes([])["calories"])


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestCalculator(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_run(calculator.ainvoke("3 * 4 + 5")), "17")

    def test_power(self):
        self.assertEqual(_run(calculator.ainvoke("2 ** 10")), "1024")

    def test_division(self):
        self.assertEqual(_run(calculator.ainvoke("15 / 2")), "7.5")

    def test_zero_division(self):
        self.assertEqual(_run(calculator.ainvoke("1 / 0")), "错误：除数不能为零。")

    def test_injection_blocked(self):
        r = _run(calculator.ainvoke("__import__('os').system('dir')"))
        self.assertIn("不支持", r)


@unittest.skipUnless(DEPS_OK, "缺少 Python 依赖，跳过纯逻辑测试")
class TestDatetimeTool(unittest.TestCase):
    def test_returns_str(self):
        r = _run(get_current_datetime.ainvoke({}))
        self.assertIsInstance(r, str)
        self.assertIn("当前时间", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)