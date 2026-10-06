"""课程登记后按原顺序读取的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
成功登记与跨进程读取、各类登记失败、查询失败。
每个用例使用独立的临时数据库，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_TITLE = " Python 入门 "
VALID_CHAPTERS = [" 安装环境 ", "第一段程序"]
STRIPPED_TITLE = "Python 入门"
STRIPPED_CHAPTERS = ["安装环境", "第一段程序"]


def run_cli(db_path, *args, db_first=True):
    """以独立进程运行 CLI，返回 CompletedProcess。

    db_first 为 True 时把 --db 写在子命令前，否则写在子命令后，
    两种写法都应被支持。
    """
    if db_first:
        argv = ["--db", str(db_path), *args]
    else:
        argv = [*args, "--db", str(db_path)]
    return subprocess.run(
        [sys.executable, "-m", "course_progress", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def add_valid_course(db_path, db_first=True):
    return run_cli(
        db_path,
        "add-course",
        "--title",
        VALID_TITLE,
        "--chapter",
        VALID_CHAPTERS[0],
        "--chapter",
        VALID_CHAPTERS[1],
        db_first=db_first,
    )


class CliTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"

    def assert_failure(self, result, exit_code, message):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)

    def assert_empty_db_then_register(self):
        """确认数据库仍为空，随后登记有效课程应得到编号 1。"""
        listed = run_cli(self.db_path, "list-courses")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(listed.stderr, "")
        self.assertEqual(json.loads(listed.stdout), {"courses": []})

        added = add_valid_course(self.db_path)
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        got = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout),
            {
                "course_id": 1,
                "title": STRIPPED_TITLE,
                "chapters": STRIPPED_CHAPTERS,
            },
        )


class TestAddThenGet(CliTestCase):
    def test_add_then_get_preserves_order_across_processes(self):
        added = add_valid_course(self.db_path)
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        # 标准输出是可解析的 JSON，按内容比较，不限定字段顺序或空格
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        # 另一次独立调用读取同一数据库，证明前一次进程退出后数据仍在
        got = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        course = json.loads(got.stdout)
        self.assertEqual(course["title"], STRIPPED_TITLE)
        # 章节数量与顺序一致，且首尾空白已去除
        self.assertEqual(course["chapters"], STRIPPED_CHAPTERS)

    def test_db_option_after_subcommand(self):
        added = add_valid_course(self.db_path, db_first=False)
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        got = run_cli(self.db_path, "get-course", "1", db_first=False)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout),
            {
                "course_id": 1,
                "title": STRIPPED_TITLE,
                "chapters": STRIPPED_CHAPTERS,
            },
        )


class TestAddValidationFailures(CliTestCase):
    def test_blank_title(self):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "   ",
            "--chapter",
            "安装环境",
        )
        self.assert_failure(result, 1, "课程标题不能为空")
        self.assert_empty_db_then_register()

    def test_no_chapters(self):
        result = run_cli(self.db_path, "add-course", "--title", "Python 入门")
        self.assert_failure(result, 1, "章节不能为空")
        self.assert_empty_db_then_register()

    def test_blank_chapter_name(self):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "Python 入门",
            "--chapter",
            "安装环境",
            "--chapter",
            "  ",
        )
        self.assert_failure(result, 1, "章节不能为空")
        self.assert_empty_db_then_register()

    def test_duplicate_chapter_after_strip(self):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "Python 入门",
            "--chapter",
            " 安装环境 ",
            "--chapter",
            "安装环境",
        )
        self.assert_failure(result, 1, "章节名重复")
        self.assert_empty_db_then_register()

    def test_title_error_reported_before_chapter_error(self):
        result = run_cli(self.db_path, "add-course", "--title", "  ")
        self.assert_failure(result, 1, "课程标题不能为空")
        self.assert_empty_db_then_register()

    def test_blank_chapter_reported_before_duplicate(self):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "Python 入门",
            "--chapter",
            " ",
            "--chapter",
            "安装环境",
            "--chapter",
            " 安装环境 ",
        )
        self.assert_failure(result, 1, "章节不能为空")
        self.assert_empty_db_then_register()


class TestGetFailures(CliTestCase):
    def test_unknown_positive_id(self):
        result = run_cli(self.db_path, "get-course", "42")
        self.assert_failure(result, 1, "课程不存在")

    def test_zero_id(self):
        result = run_cli(self.db_path, "get-course", "0")
        self.assert_failure(result, 2, "课程编号必须为正整数")

    def test_negative_id(self):
        result = run_cli(self.db_path, "get-course", "-3")
        self.assert_failure(result, 2, "课程编号必须为正整数")

    def test_non_numeric_id(self):
        result = run_cli(self.db_path, "get-course", "abc")
        self.assert_failure(result, 2, "课程编号必须为正整数")

    def test_decimal_id(self):
        result = run_cli(self.db_path, "get-course", "1.5")
        self.assert_failure(result, 2, "课程编号必须为正整数")


# SQLite 整数上限为 2**63 - 1；恰好等于上限的编号按普通正整数查询。
SQLITE_INT64_MAX = 2**63 - 1
OVERFLOW_IDS = [str(SQLITE_INT64_MAX + 1), "9" * 26]

SAMPLE_TITLE = "入门培训"
SAMPLE_CHAPTERS = ["准备", "学习", "回顾"]
SAMPLE_COURSE = {
    "course_id": 1,
    "title": SAMPLE_TITLE,
    "chapters": SAMPLE_CHAPTERS,
}


class TestOverflowCourseId(CliTestCase):
    """超出 SQLite 整数范围的编号按未知课程处理，且不改变数据库内容。"""

    def setUp(self):
        super().setUp()
        added = run_cli(
            self.db_path,
            "add-course",
            "--title",
            SAMPLE_TITLE,
            *[arg for name in SAMPLE_CHAPTERS for arg in ("--chapter", name)],
        )
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

    def assert_sample_course_intact(self):
        """课程详情与概览保持登记时的内容，编号 1 仍可按原顺序读取。"""
        got = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(json.loads(got.stdout), SAMPLE_COURSE)

        listed = run_cli(self.db_path, "list-courses")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(listed.stderr, "")
        self.assertEqual(
            json.loads(listed.stdout),
            {"courses": [{"course_id": 1, "title": SAMPLE_TITLE, "chapter_count": 3}]},
        )

    def test_overflow_id_treated_as_unknown_course(self):
        for raw in OVERFLOW_IDS:
            with self.subTest(course_id=raw):
                result = run_cli(self.db_path, "get-course", raw)
                self.assert_failure(result, 1, "课程不存在")
                self.assertNotIn("Traceback", result.stderr)
        self.assert_sample_course_intact()

    def test_int64_max_id_is_an_ordinary_unknown_id(self):
        result = run_cli(self.db_path, "get-course", str(SQLITE_INT64_MAX))
        self.assert_failure(result, 1, "课程不存在")
        self.assert_sample_course_intact()

    def test_overflow_id_with_affixes_is_not_a_format_error(self):
        for raw in [
            " +9223372036854775808 ",
            "009223372036854775808",
            "\t" + "9" * 26 + " ",
        ]:
            with self.subTest(course_id=raw):
                result = run_cli(self.db_path, "get-course", raw)
                self.assert_failure(result, 1, "课程不存在")
        self.assert_sample_course_intact()

    def test_failed_overflow_query_does_not_consume_ids(self):
        for raw in OVERFLOW_IDS:
            run_cli(self.db_path, "get-course", raw)

        added = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "进阶培训",
            "--chapter",
            "实战",
        )
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 2})

        # 重新打开数据库后，两门课程仍按登记顺序可读
        listed = run_cli(self.db_path, "list-courses")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(
            [c["course_id"] for c in json.loads(listed.stdout)["courses"]],
            [1, 2],
        )
        got = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(json.loads(got.stdout), SAMPLE_COURSE)


class TestCoreGetCourseOverflow(CliTestCase):
    """core.get_course 收到超界的 Python 正整数时返回 None。"""

    def test_overflow_int_returns_none(self):
        from course_progress.core import add_course, connect, get_course

        conn = connect(str(self.db_path))
        with conn:
            course_id = add_course(conn, SAMPLE_TITLE, SAMPLE_CHAPTERS)
            self.assertEqual(get_course(conn, course_id)["chapters"], SAMPLE_CHAPTERS)
            self.assertIsNone(get_course(conn, 2**63))
            self.assertIsNone(get_course(conn, int("9" * 26)))
            self.assertIsNone(get_course(conn, -(2**63) - 1))
            self.assertIsNone(get_course(conn, 2**63 - 1))
            self.assertIsNone(get_course(conn, course_id + 1))
        conn.close()


if __name__ == "__main__":
    unittest.main()
