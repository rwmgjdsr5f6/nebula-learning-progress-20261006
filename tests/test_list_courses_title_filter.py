"""按标题片段筛选课程概览（list-courses --title-contains）的回归测试。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接
调用公开函数 ``list_courses`` 验证函数层面的匹配与校验规则。每个用例
使用独立临时目录，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from course_progress import connect, list_courses
from course_progress.core import ERR_EMPTY_TITLE_FILTER, ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent

# 验收样例：标题依次为 Python入门、python复习、Python入门、100%_训练，
# 章节数分别为 2、1、1、3。
SAMPLE_COURSES = [
    ("Python入门", ["基础", "练习"]),
    ("python复习", ["复习"]),
    ("Python入门", ["导学"]),
    ("100%_训练", ["热身", "正式", "收尾"]),
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
    """按验收顺序登记四门课程。"""
    for index, (title, chapters) in enumerate(SAMPLE_COURSES, start=1):
        chapter_args = [arg for name in chapters for arg in ("--chapter", name)]
        result = run_cli(
            db_path, "add-course", "--title", title, *chapter_args
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"course_id": index}


class TestListCoursesTitleFilter(unittest.TestCase):
    """每个用例一个独立临时目录，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "filter.db"

    def assert_success(self, result, expected):
        """成功筛选：退出码 0、标准错误为空、单行 JSON 且内容符合预期。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_filter_with_surrounding_spaces_matches_continuous_substring(self):
        """筛选词 " Python " 去首尾空白后只匹配编号一和三，标题原样返回。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", " Python "
        )

        self.assert_success(
            result,
            {
                "courses": [
                    {
                        "course_id": 1,
                        "title": "Python入门",
                        "chapter_count": 2,
                    },
                    {
                        "course_id": 3,
                        "title": "Python入门",
                        "chapter_count": 1,
                    },
                ]
            },
        )

    def test_filter_percent_underscore_are_literal_characters(self):
        """"%_" 不作为通配模式，只字面匹配编号四。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "%_"
        )

        self.assert_success(
            result,
            {
                "courses": [
                    {
                        "course_id": 4,
                        "title": "100%_训练",
                        "chapter_count": 3,
                    }
                ]
            },
        )

    def test_matching_is_case_sensitive(self):
        """小写 python 只匹配编号二，大小写敏感。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "python"
        )

        self.assert_success(
            result,
            {
                "courses": [
                    {
                        "course_id": 2,
                        "title": "python复习",
                        "chapter_count": 1,
                    }
                ]
            },
        )

    def test_internal_whitespace_is_preserved_in_filter(self):
        """内部空白参与匹配：标题中无 "Python 入门"（中间带空格）时无结果。"""
        register_sample_courses(self.db_path)

        with_space = run_cli(
            self.db_path, "list-courses", "--title-contains", "Python 入门"
        )
        self.assert_success(with_space, {"courses": []})

        without_space = run_cli(
            self.db_path, "list-courses", "--title-contains", "Python入门"
        )
        self.assertEqual(
            len(json.loads(without_space.stdout)["courses"]), 2
        )

    def test_does_not_match_chapter_names(self):
        """只匹配课程标题：仅出现在章节名中的片段不产生匹配。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "复习"
        )
        # “复习”是编号二的章节名，课程标题为 python复习 才匹配
        courses = json.loads(result.stdout)["courses"]
        self.assertEqual([c["course_id"] for c in courses], [2])

        chapter_only = run_cli(
            self.db_path, "list-courses", "--title-contains", "热身"
        )
        self.assert_success(chapter_only, {"courses": []})

    def test_no_match_and_empty_database_return_empty_list(self):
        """无匹配项与空库均返回 {"courses": []}，退出码 0。"""
        register_sample_courses(self.db_path)

        no_match = run_cli(
            self.db_path, "list-courses", "--title-contains", "不存在的标题"
        )
        self.assert_success(no_match, {"courses": []})

        empty_db = Path(self._tmp.name) / "empty.db"
        on_empty = run_cli(empty_db, "list-courses", "--title-contains", "x")
        self.assert_success(on_empty, {"courses": []})

    def test_repeated_and_cross_process_queries_are_consistent(self):
        """同进程外重复查询、--db 前后位置不同，结果均一致。"""
        register_sample_courses(self.db_path)

        first = run_cli(
            self.db_path, "list-courses", "--title-contains", " Python "
        )
        second = run_cli(
            self.db_path, "list-courses", "--title-contains", " Python "
        )
        db_after = run_cli(
            self.db_path,
            "list-courses",
            "--title-contains",
            " Python ",
            db_first=False,
        )
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout, db_after.stdout)

    def test_filter_without_argument_keeps_full_overview(self):
        """省略筛选参数时行为与原先完全一致，返回全部四门课程。"""
        register_sample_courses(self.db_path)

        result = run_cli(self.db_path, "list-courses")
        overview = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            [c["course_id"] for c in overview["courses"]], [1, 2, 3, 4]
        )
        for course in overview["courses"]:
            self.assertEqual(
                set(course.keys()),
                {"course_id", "title", "chapter_count"},
            )

    def test_empty_filter_exits_1_with_message_and_no_traceback(self):
        """显式空字符串：退出码 1，标准输出为空，标准错误仅有中文提示。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", ""
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_TITLE_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_whitespace_only_filter_exits_1_with_message(self):
        """仅含空白的筛选词同样失败：退出码 1，输出为空，提示到标准错误。"""
        register_sample_courses(self.db_path)

        result = run_cli(
            self.db_path, "list-courses", "--title-contains", "   \t "
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_TITLE_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_takes_priority_over_empty_filter(self):
        """缺少 --db 时仍优先返回原提示，退出码 2。"""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "course_progress",
                "list-courses",
                "--title-contains",
                "",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "必须通过 --db 指定数据库文件")

    def test_filter_is_read_only_and_does_not_consume_ids(self):
        """筛选不修改记录、不消耗编号：筛选后登记的新课程编号仍是 5。"""
        register_sample_courses(self.db_path)

        for _ in range(3):
            filtered = run_cli(
                self.db_path, "list-courses", "--title-contains", "Python"
            )
            self.assertEqual(filtered.returncode, 0)

        added = run_cli(
            self.db_path, "add-course", "--title", "Python进阶", "--chapter", "x"
        )
        self.assertEqual(json.loads(added.stdout), {"course_id": 5})

        # 原四门课程的标题与章节数未被筛选改变
        overview = run_cli(self.db_path, "list-courses")
        self.assertEqual(
            json.loads(overview.stdout),
            {
                "courses": [
                    {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                    {"course_id": 2, "title": "python复习", "chapter_count": 1},
                    {"course_id": 3, "title": "Python入门", "chapter_count": 1},
                    {"course_id": 4, "title": "100%_训练", "chapter_count": 3},
                    {"course_id": 5, "title": "Python进阶", "chapter_count": 1},
                ]
            },
        )


class TestListCoursesFunctionFilter(unittest.TestCase):
    """直接调用公开函数 list_courses，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(str(Path(self._tmp.name) / "func.db"))
        self.addCleanup(self.conn.close)
        from course_progress import add_course

        for title, chapters in SAMPLE_COURSES:
            add_course(self.conn, title, chapters)

    def test_none_and_omitted_return_all_courses(self):
        """省略参数或显式 None 返回全部课程，按编号升序。"""
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn)], [1, 2, 3, 4]
        )
        self.assertEqual(
            [c["course_id"] for c in list_courses(self.conn, None)],
            [1, 2, 3, 4],
        )

    def test_function_filter_matches_substring(self):
        """函数筛选与命令行同规则：去首尾空白、大小写敏感、字面字符。"""
        result = list_courses(self.conn, " Python ")
        self.assertEqual([c["course_id"] for c in result], [1, 3])
        self.assertEqual(result[0]["title"], "Python入门")

        literal = list_courses(self.conn, "%_")
        self.assertEqual([c["course_id"] for c in literal], [4])

        case_sensitive = list_courses(self.conn, "python")
        self.assertEqual([c["course_id"] for c in case_sensitive], [2])

        self.assertEqual(list_courses(self.conn, "没有这样的标题"), [])

    def test_empty_or_whitespace_filter_raises_validation_error(self):
        """空字符串或仅空白抛出 ValidationError，消息与命令行提示相同。"""
        for bad in ("", "   ", "\t\n  "):
            with self.assertRaises(ValidationError) as ctx:
                list_courses(self.conn, bad)
            self.assertEqual(str(ctx.exception), ERR_EMPTY_TITLE_FILTER)


if __name__ == "__main__":
    unittest.main()
