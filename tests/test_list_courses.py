"""课程概览查询（list-courses）的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
空库概览、多门课程（含同标题）的概览顺序与字段、--db 位置与重复查询的
一致性、不同数据库彼此独立、缺少 --db 的报错。
每个用例使用独立的临时数据库，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定合成样例：三门课程依次登记，标题不按字典序、后两门同标题，
# 用于确认概览按课程编号排序且不合并同标题课程。
SAMPLE_COURSES = [
    ("Z培训", ["准备", "学习"]),
    ("A培训", ["入门"]),
    ("A培训", ["入门", "练习", "回顾"]),
]
EXPECTED_OVERVIEW = {
    "courses": [
        {"course_id": 1, "title": "Z培训", "chapter_count": 2},
        {"course_id": 2, "title": "A培训", "chapter_count": 1},
        {"course_id": 3, "title": "A培训", "chapter_count": 3},
    ]
}


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


def run_cli_without_db(*args):
    """以独立进程运行 CLI，但不提供 --db。"""
    return subprocess.run(
        [sys.executable, "-m", "course_progress", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def add_course(db_path, title, chapters):
    return run_cli(
        db_path,
        "add-course",
        "--title",
        title,
        *[arg for name in chapters for arg in ("--chapter", name)],
    )


class ListCoursesTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，数据库文件由 CLI 首次调用时创建。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"

    def assert_overview_ok(self, result, expected):
        """成功概览：退出码 0、标准错误为空、标准输出是一行可解析 JSON。

        按解析后的内容比较，不限定对象键顺序或空格格式。
        """
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出恰好一行（以换行结尾，无多余行）
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stdout.count("\n"), 1)
        return self.assertEqual(json.loads(result.stdout), expected)

    def register_sample_courses(self):
        """在同一个新库中依次登记三门固定样例课程，编号应为 1、2、3。"""
        for expected_id, (title, chapters) in enumerate(SAMPLE_COURSES, start=1):
            added = add_course(self.db_path, title, chapters)
            self.assertEqual(added.returncode, 0)
            self.assertEqual(added.stderr, "")
            self.assertEqual(json.loads(added.stdout), {"course_id": expected_id})


class TestEmptyOverview(ListCoursesTestCase):
    def test_missing_db_file_lists_no_courses(self):
        # 父目录已存在而数据库文件尚不存在时直接查询概览
        self.assertFalse(self.db_path.exists())
        result = run_cli(self.db_path, "list-courses")
        self.assert_overview_ok(result, {"courses": []})


class TestSampleOverview(ListCoursesTestCase):
    def setUp(self):
        super().setUp()
        self.register_sample_courses()

    def test_overview_lists_courses_by_id_without_chapter_names(self):
        result = run_cli(self.db_path, "list-courses")
        self.assert_overview_ok(result, EXPECTED_OVERVIEW)

        courses = json.loads(result.stdout)["courses"]
        # 按课程编号升序，不按标题重排，也不合并同标题课程
        self.assertEqual([c["course_id"] for c in courses], [1, 2, 3])
        self.assertEqual([c["title"] for c in courses], ["Z培训", "A培训", "A培训"])
        self.assertEqual([c["chapter_count"] for c in courses], [2, 1, 3])
        # 每项只包含 README 公开的概览字段，不带出章节名称
        for course in courses:
            self.assertEqual(set(course), {"course_id", "title", "chapter_count"})

    def test_db_option_position_and_repeat_query_give_same_overview(self):
        before = run_cli(self.db_path, "list-courses")
        self.assert_overview_ok(before, EXPECTED_OVERVIEW)

        # --db 写在子命令后，结果与写在子命令前一致
        after = run_cli(self.db_path, "list-courses", db_first=False)
        self.assert_overview_ok(after, EXPECTED_OVERVIEW)
        self.assertEqual(json.loads(after.stdout), json.loads(before.stdout))

        # 前一次调用结束后再次查询，仍获得相同概览
        again = run_cli(self.db_path, "list-courses")
        self.assert_overview_ok(again, EXPECTED_OVERVIEW)
        self.assertEqual(json.loads(again.stdout), json.loads(before.stdout))

    def test_separate_empty_db_is_not_affected(self):
        # 先确认样例库概览非空
        result = run_cli(self.db_path, "list-courses")
        self.assert_overview_ok(result, EXPECTED_OVERVIEW)

        # 另一份独立空库应得到空列表，不混入原库课程
        other_db = Path(self._tmp.name) / "other" / "other.db"
        other_db.parent.mkdir()
        other = run_cli(other_db, "list-courses")
        self.assert_overview_ok(other, {"courses": []})


class TestMissingDbOption(unittest.TestCase):
    def test_list_courses_without_db_option(self):
        result = run_cli_without_db("list-courses")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "必须通过 --db 指定数据库文件")


if __name__ == "__main__":
    unittest.main()
