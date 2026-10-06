"""课程概览（list-courses）公开行为的回归测试。

通过子进程调用 ``python -m course_progress``，验证 README 已公开的概览约定：
空库自动建库返回空列表、概览字段与编号顺序、同标题课程不合并、
``--db`` 写在子命令前后等价、跨进程再次读取结果一致、不同数据库相互独立、
缺少 ``--db`` 时失败退出；以及 ``--title-contains`` 的大小写敏感子串筛选
与空筛选词报错。每个用例使用独立临时目录，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from course_progress import add_course, connect, list_courses
from course_progress.core import ValidationError

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

# 筛选验收样例：标题大小写与特殊字符刻意混合，章节数各不相同。
FILTER_SAMPLE_COURSES = [
    ("Python入门", ["上", "下"]),
    ("python复习", ["章"]),
    ("Python入门", ["章"]),
    ("100%_训练", ["一", "二", "三"]),
]


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


def register_filter_sample_courses(db_path):
    """依次登记筛选验收样例的四门课程（Python入门/python复习/Python入门/100%_训练）。"""
    for index, (title, chapters) in enumerate(FILTER_SAMPLE_COURSES, start=1):
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


class TestListCoursesTitleFilter(unittest.TestCase):
    """--title-contains 的筛选、错误与只读行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "filter.db"

    def assert_overview_success(self, result):
        """成功概览查询：退出码 0、标准错误为空、输出一行可解析 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def test_filter_with_surrounding_spaces_matches_internal_space(self):
        """' Python ' 去首尾空白后匹配编号 1、3，原始标题与章节数不变。"""
        register_filter_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", " Python "
        )
        overview = self.assert_overview_success(result)

        self.assertEqual(
            overview,
            {
                "courses": [
                    {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                    {"course_id": 3, "title": "Python入门", "chapter_count": 1},
                ]
            },
        )

    def test_filter_percent_underscore_are_literal(self):
        """'%_' 作为普通连续子串只命中编号 4，不做通配匹配。"""
        register_filter_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "%_"
        )
        overview = self.assert_overview_success(result)

        self.assertEqual(
            overview,
            {
                "courses": [
                    {"course_id": 4, "title": "100%_训练", "chapter_count": 3},
                ]
            },
        )

    def test_filter_is_case_sensitive(self):
        """大小写敏感：小写 python 只命中编号 2，大写 Python 命中 1、3。"""
        register_filter_sample_courses(self.db_path)

        lower = self.assert_overview_success(
            run_cli(
                self.db_path, "list-courses", "--title-contains", "python"
            )
        )
        self.assertEqual([c["course_id"] for c in lower["courses"]], [2])

        upper = self.assert_overview_success(
            run_cli(
                self.db_path, "list-courses", "--title-contains", "Python"
            )
        )
        self.assertEqual([c["course_id"] for c in upper["courses"]], [1, 3])

    def test_filter_does_not_match_chapter_names(self):
        """章节名含筛选词但标题不含时不命中：如 '入门' 不出现在任何标题。"""
        register_filter_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "不存在"
        )
        self.assertEqual(self.assert_overview_success(result), {"courses": []})

    def test_filter_on_empty_database_returns_empty(self):
        """空库带筛选词同样返回空列表，退出码 0。"""
        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "Python"
        )
        self.assertEqual(self.assert_overview_success(result), {"courses": []})

    def test_blank_filter_exits_with_code_1(self):
        """空字符串或仅含空白的筛选词：退出码 1、空 stdout、固定中文错误。"""
        register_filter_sample_courses(self.db_path)
        for raw in ("", "   ", "\t\n "):
            with self.subTest(raw=raw):
                result = run_cli(
                    self.db_path, "list-courses", "--title-contains", raw
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "课程标题筛选词不能为空\n")

    def test_missing_db_reported_before_blank_filter(self):
        """缺少 --db 与空筛选词并存时优先报缺少 --db，退出码 2。"""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "course_progress",
                "list-courses",
                "--title-contains",
                "   ",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "必须通过 --db 指定数据库文件")

    def test_filter_is_read_only_and_repeatable(self):
        """筛选不改数据、不消耗编号；重复及跨进程查询结果一致。"""
        register_filter_sample_courses(self.db_path)
        expected = {
            "courses": [
                {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                {"course_id": 3, "title": "Python入门", "chapter_count": 1},
            ]
        }

        first = run_cli(
            self.db_path, "list-courses", "--title-contains", " Python "
        )
        second = run_cli(
            self.db_path,
            "list-courses",
            "--title-contains",
            " Python ",
            db_first=False,
        )
        self.assertEqual(self.assert_overview_success(first), expected)
        self.assertEqual(self.assert_overview_success(second), expected)

        # 全部课程仍为四条，且筛选后再登记的编号为 5（编号未被消耗）
        full = self.assert_overview_success(run_cli(self.db_path, "list-courses"))
        self.assertEqual(
            [c["course_id"] for c in full["courses"]], [1, 2, 3, 4]
        )
        added = run_cli(
            self.db_path, "add-course", "--title", "新", "--chapter", "x"
        )
        self.assertEqual(json.loads(added.stdout), {"course_id": 5})


class TestListCoursesTitleFilterFunction(unittest.TestCase):
    """公开函数 list_courses 的 title_contains 参数与 CLI 遵守相同规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(str(Path(self._tmp.name) / "fn.db"))
        self.addCleanup(self.conn.close)

    def test_none_and_omitted_return_all(self):
        """省略参数或传 None 都返回全部课程。"""
        for title, chapters in FILTER_SAMPLE_COURSES:
            add_course(self.conn, title, list(chapters))

        all_ids = [1, 2, 3, 4]
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn)], all_ids
        )
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn, None)], all_ids
        )

    def test_function_filter_matches_literal_and_case_sensitive(self):
        for title, chapters in FILTER_SAMPLE_COURSES:
            add_course(self.conn, title, list(chapters))

        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn, " Python ")],
            [1, 3],
        )
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn, "%_")], [4]
        )
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn, "python")], [2]
        )
        self.assertEqual(list_courses(self.conn, "无此标题"), [])

    def test_function_blank_filter_raises_validation_error(self):
        for raw in ("", "   ", "\t"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError) as ctx:
                    list_courses(self.conn, raw)
                self.assertEqual(str(ctx.exception), "课程标题筛选词不能为空")


if __name__ == "__main__":
    unittest.main()
