"""课程概览（list-courses）公开行为的回归测试。

通过子进程调用 ``python -m course_progress``，验证 README 已公开的概览约定：
空库自动建库返回空列表、概览字段与编号顺序、同标题课程不合并、
``--db`` 写在子命令前后等价、跨进程再次读取结果一致、不同数据库相互独立、
缺少 ``--db`` 时失败退出。每个用例使用独立临时目录，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定合成课程：标题顺序刻意按 Z、A、A 登记，章节数分别为 2、1、3，
# 用于证明概览按编号排列而非按标题重排，且同标题课程不被合并。
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

OVERVIEW_FIELDS = {"course_id", "title", "chapter_count"}


def run_cli(db_path, *args, db_first=True):
    """以独立进程运行 CLI，返回 CompletedProcess。

    db_first 为 True 时把 --db 写在子命令前，否则写在子命令后。
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


def register_sample_courses(db_path):
    """在同一数据库中依次登记三门固定合成课程。"""
    for index, (title, chapters) in enumerate(SAMPLE_COURSES, start=1):
        chapter_args = [arg for name in chapters for arg in ("--chapter", name)]
        result = run_cli(
            db_path, "add-course", "--title", title, *chapter_args
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"course_id": index}


class TestListCoursesOverview(unittest.TestCase):
    """每个用例一个独立临时目录，互不影响，也不依赖仓库中已有数据库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "overview.db"

    def assert_overview_success(self, result):
        """成功概览查询：退出码 0、标准错误为空、输出一行可解析 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出为单行 JSON：去掉行尾换行后不应再含换行
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def test_missing_database_file_returns_empty_overview(self):
        """父目录已存在而数据库文件尚不存在时，概览返回空列表。"""
        self.assertFalse(self.db_path.exists())
        result = run_cli(self.db_path, "list-courses")
        overview = self.assert_overview_success(result)
        self.assertEqual(overview, {"courses": []})

    def test_overview_lists_courses_by_id_without_merging_titles(self):
        """三项按编号 1/2/3 排列，标题顺序与登记一致，同标题不重排不合并。"""
        register_sample_courses(self.db_path)

        result = run_cli(self.db_path, "list-courses")
        overview = self.assert_overview_success(result)

        # 按解析后的完整内容比较，不限定键顺序或空格格式
        self.assertEqual(overview, EXPECTED_OVERVIEW)

        courses = overview["courses"]
        self.assertEqual([c["course_id"] for c in courses], [1, 2, 3])
        self.assertEqual([c["title"] for c in courses], ["Z培训", "A培训", "A培训"])
        self.assertEqual(
            [c["chapter_count"] for c in courses], [2, 1, 3]
        )
        # 每项只含 README 公开的概览字段，不带出章节名称
        for course in courses:
            self.assertEqual(set(course.keys()), OVERVIEW_FIELDS)
            self.assertNotIn("chapters", course)

    def test_db_option_before_and_after_subcommand_give_same_overview(self):
        """--db 写在 list-courses 前后结果相同，且跨进程再次读取仍一致。"""
        register_sample_courses(self.db_path)

        before = run_cli(self.db_path, "list-courses", db_first=True)
        after = run_cli(self.db_path, "list-courses", db_first=False)

        self.assertEqual(self.assert_overview_success(before), EXPECTED_OVERVIEW)
        self.assertEqual(self.assert_overview_success(after), EXPECTED_OVERVIEW)
        # 两种写法的解析结果彼此一致
        self.assertEqual(
            json.loads(before.stdout), json.loads(after.stdout)
        )

        # 前一次调用（进程）结束后再次查询，概览仍保持一致
        reread = run_cli(self.db_path, "list-courses")
        self.assertEqual(self.assert_overview_success(reread), EXPECTED_OVERVIEW)

    def test_separate_empty_database_stays_empty(self):
        """另一份独立空库返回空列表，不会混入原库课程。"""
        register_sample_courses(self.db_path)
        other_db_path = Path(self._tmp.name) / "other.db"
        self.assertFalse(other_db_path.exists())

        other = run_cli(other_db_path, "list-courses")
        self.assertEqual(
            self.assert_overview_success(other), {"courses": []}
        )

        # 查询空库不影响原库，原库概览仍为三门课程
        original = run_cli(self.db_path, "list-courses")
        self.assertEqual(
            self.assert_overview_success(original), EXPECTED_OVERVIEW
        )

    def test_missing_db_option_exits_with_code_2(self):
        """缺少 --db 的概览调用：退出码 2，标准输出为空，标准错误给出提示。"""
        result = subprocess.run(
            [sys.executable, "-m", "course_progress", "list-courses"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "必须通过 --db 指定数据库文件")


if __name__ == "__main__":
    unittest.main()
