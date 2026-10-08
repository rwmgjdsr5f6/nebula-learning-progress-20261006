"""学员已报名课程按标题片段筛选（list-learner-courses --title-contains）的回归测试。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接
调用公开函数 ``list_learner_courses`` 验证函数层面的匹配与校验规则。
主样例固定为四门课程（编号 1 至 4，标题依次为 Python入门、python复习、
Python入门、Python旁听，章节数依次为 2、1、1、1，其中编号 2 的章节名为
Python练习）与两名学员：编号 1 的学员甲先报名 3、2、1 再重复报名 1，
编号 2 的学员乙只报名 4。每个用例使用独立临时目录与合成数据，结束后
自动清理；本文件不新增产品功能，只固化已有行为。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from course_progress import (
    add_course,
    add_learner,
    connect,
    enroll_learner,
    list_learner_courses,
    rename_course,
)
from course_progress.core import (
    ERR_BAD_LEARNER_ID,
    ERR_EMPTY_TITLE_FILTER,
    ERR_LEARNER_NOT_FOUND,
    ValidationError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 主样例：标题依次为 Python入门、python复习、Python入门、Python旁听，
# 章节数依次为 2、1、1、1；编号 2 的章节名为 Python练习。
SAMPLE_COURSES = [
    ("Python入门", ["基础", "进阶"]),
    ("python复习", ["Python练习"]),
    ("Python入门", ["导学"]),
    ("Python旁听", ["旁听须知"]),
]


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


class ListLearnerCoursesFilterTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "sample.db"

    # ---- 数据准备 ----

    def add_course(self, title, *chapters):
        args = ["add-course", "--title", title]
        for chapter in chapters:
            args.extend(["--chapter", chapter])
        result = run_cli(self.db_path, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id):
        result = run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def register_main_sample(self):
        """登记主样例：四门课程、两名学员与各自报名记录。"""
        for index, (title, chapters) in enumerate(SAMPLE_COURSES, start=1):
            self.assertEqual(self.add_course(title, *chapters), index)
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员乙"), 2)
        # 学员甲先报名 3、2、1，再重复报名 1；学员乙只报名 4
        self.enroll(1, 3)
        self.enroll(1, 2)
        self.enroll(1, 1)
        self.enroll(1, 1)
        self.enroll(2, 4)

    # ---- 命令与断言辅助 ----

    def list_courses_cli(self, learner_id, *extra, db_first=True):
        return run_cli(
            self.db_path,
            "list-learner-courses",
            str(learner_id),
            *extra,
            db_first=db_first,
        )

    def assert_success(self, result, expected):
        """成功筛选：退出码 0、标准错误为空、单行 JSON 且内容符合预期。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, exit_code, message):
        """失败：标准输出为空、无异常堆栈、标准错误仅有中文提示。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def snapshot_data(self):
        """读取课程、学员与报名记录的当前状态，用于只读性比对。"""
        courses = run_cli(self.db_path, "list-courses")
        self.assertEqual(courses.returncode, 0, courses.stderr)
        learners = run_cli(self.db_path, "list-learners")
        self.assertEqual(learners.returncode, 0, learners.stderr)
        enrollments = {}
        for learner in json.loads(learners.stdout)["learners"]:
            listed = self.list_courses_cli(learner["learner_id"])
            self.assertEqual(listed.returncode, 0, listed.stderr)
            enrollments[learner["learner_id"]] = json.loads(listed.stdout)[
                "courses"
            ]
        return (
            json.loads(courses.stdout),
            json.loads(learners.stdout),
            enrollments,
        )


class TestListLearnerCoursesTitleFilter(ListLearnerCoursesFilterTestCase):
    """主样例上的命令行筛选行为。"""

    def test_acceptance_filter_with_surrounding_spaces(self):
        """" Python " 去首尾空白后只命中编号 1 和 3，按编号升序输出。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", " Python ")

        self.assert_success(
            result,
            {
                "learner_id": 1,
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
                ],
            },
        )

    def test_same_title_courses_not_merged(self):
        """同标题的编号 1 与 3 分别列出，不合并；章节数各自为 2 和 1。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "Python入门")

        courses = json.loads(result.stdout)["courses"]
        self.assertEqual(
            courses,
            [
                {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                {"course_id": 3, "title": "Python入门", "chapter_count": 1},
            ],
        )

    def test_matching_is_case_sensitive(self):
        """小写 python 只命中编号 2（python复习）。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "python")

        self.assert_success(
            result,
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 2,
                        "title": "python复习",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_does_not_match_chapter_names(self):
        """只匹配课程标题：编号 2 的章节名 Python练习 不产生匹配。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "练习")

        self.assert_success(result, {"learner_id": 1, "courses": []})

    def test_other_learners_enrollments_do_not_mix_in(self):
        """学员乙报名的编号 4 同样含 Python，但不混入学员甲的结果。"""
        self.register_main_sample()

        first = self.list_courses_cli(1, "--title-contains", "Python")
        # 大写 Python 命中编号 1、3；编号 2 的标题是小写 python，不命中
        self.assertEqual(
            [c["course_id"] for c in json.loads(first.stdout)["courses"]],
            [1, 3],
        )

        second = self.list_courses_cli(2, "--title-contains", "Python")
        self.assert_success(
            second,
            {
                "learner_id": 2,
                "courses": [
                    {
                        "course_id": 4,
                        "title": "Python旁听",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_no_match_returns_empty_array(self):
        """有报名但无标题匹配时返回空数组，退出码 0。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "不存在的标题")

        self.assert_success(result, {"learner_id": 1, "courses": []})

    def test_enrolled_learner_without_enrollments_returns_empty_array(self):
        """存在但未报名的学员无论是否筛选都返回空数组。"""
        self.register_main_sample()
        self.assertEqual(self.add_learner("学员丙"), 3)

        unfiltered = self.list_courses_cli(3)
        self.assert_success(unfiltered, {"learner_id": 3, "courses": []})

        filtered = self.list_courses_cli(3, "--title-contains", "Python")
        self.assert_success(filtered, {"learner_id": 3, "courses": []})

    def test_omit_filter_returns_all_enrolled_courses(self):
        """省略 --title-contains 时行为不变，返回全部已报名课程。"""
        self.register_main_sample()

        result = self.list_courses_cli(1)

        self.assert_success(
            result,
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "Python入门",
                        "chapter_count": 2,
                    },
                    {
                        "course_id": 2,
                        "title": "python复习",
                        "chapter_count": 1,
                    },
                    {
                        "course_id": 3,
                        "title": "Python入门",
                        "chapter_count": 1,
                    },
                ],
            },
        )

    def test_filter_uses_current_title_after_rename(self):
        """课程改名后按当前标题筛选：旧片段不再命中，新片段命中。"""
        self.register_main_sample()
        renamed = run_cli(
            self.db_path, "rename-course", "3", "--title", "Rust入门"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        old = self.list_courses_cli(1, "--title-contains", "Python入门")
        self.assertEqual(
            [c["course_id"] for c in json.loads(old.stdout)["courses"]], [1]
        )

        new = self.list_courses_cli(1, "--title-contains", "Rust")
        self.assert_success(
            new,
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 3,
                        "title": "Rust入门",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_db_option_position_and_reopen_are_consistent(self):
        """--db 在子命令前后、重新打开同一文件，筛选结果一致。"""
        self.register_main_sample()

        first = self.list_courses_cli(1, "--title-contains", " Python ")
        reopened = self.list_courses_cli(1, "--title-contains", " Python ")
        db_after = self.list_courses_cli(
            1, "--title-contains", " Python ", db_first=False
        )

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, reopened.stdout)
        self.assertEqual(first.stdout, db_after.stdout)
        self.assertEqual(reopened.stderr, "")
        self.assertEqual(db_after.stderr, "")

    def test_queries_do_not_modify_data(self):
        """成功与失败的筛选都不改变课程、学员和报名记录。"""
        self.register_main_sample()
        before = self.snapshot_data()

        self.assertEqual(
            self.list_courses_cli(1, "--title-contains", "Python").returncode,
            0,
        )
        self.assertEqual(
            self.list_courses_cli(1, "--title-contains", "").returncode, 1
        )
        self.assertEqual(
            self.list_courses_cli(99, "--title-contains", "").returncode, 1
        )
        self.assertEqual(
            self.list_courses_cli(0, "--title-contains", "").returncode, 2
        )

        self.assertEqual(self.snapshot_data(), before)


class TestListLearnerCoursesFilterLiteralCharacters(
    ListLearnerCoursesFilterTestCase
):
    """小样例：百分号、下划线、引号按普通字符匹配，内部空白参与匹配。"""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.add_course("100%_训练", "热身"), 1)
        self.assertEqual(self.add_course('他说"你好"', "开场"), 2)
        self.assertEqual(self.add_course("内部 空白", "唯一章"), 3)
        self.assertEqual(self.add_learner("学员甲"), 1)
        for course_id in (1, 2, 3):
            self.enroll(1, course_id)

    def course_ids(self, result):
        return [c["course_id"] for c in json.loads(result.stdout)["courses"]]

    def test_percent_and_underscore_are_literal(self):
        """% 与 _ 不作为通配符，只字面命中编号 1。"""
        result = self.list_courses_cli(1, "--title-contains", "%_")
        self.assert_success(
            result,
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "100%_训练",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_quote_is_literal(self):
        """引号按普通字符匹配，只字面命中编号 2。"""
        result = self.list_courses_cli(1, "--title-contains", '"')
        self.assertEqual(self.course_ids(result), [2])

        quoted = self.list_courses_cli(1, "--title-contains", '"你好"')
        self.assert_success(
            quoted,
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 2,
                        "title": '他说"你好"',
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_internal_whitespace_participates_in_matching(self):
        """内部空白参与匹配：含空格的片段命中编号 3，去掉空格则不命中。"""
        with_space = self.list_courses_cli(1, "--title-contains", "内部 空白")
        self.assertEqual(self.course_ids(with_space), [3])

        without_space = self.list_courses_cli(
            1, "--title-contains", "内部空白"
        )
        self.assert_success(without_space, {"learner_id": 1, "courses": []})


class TestListLearnerCoursesFilterFailures(ListLearnerCoursesFilterTestCase):
    """筛选词与学员编号的失败路径。"""

    def test_empty_filter_for_existing_learner_exits_1(self):
        """存在学员配空字符串筛选词：退出码 1，提示筛选词不能为空。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "")

        self.assert_failure(result, 1, ERR_EMPTY_TITLE_FILTER)

    def test_whitespace_only_filter_for_existing_learner_exits_1(self):
        """存在学员配纯空白筛选词：退出码 1，提示筛选词不能为空。"""
        self.register_main_sample()

        result = self.list_courses_cli(1, "--title-contains", "  \t ")

        self.assert_failure(result, 1, ERR_EMPTY_TITLE_FILTER)

    def test_unknown_learner_with_empty_filter_reports_not_found(self):
        """未知正整数学员即使筛选词为空，也报告学员不存在并退出 1。"""
        self.register_main_sample()

        empty_filter = self.list_courses_cli(99, "--title-contains", "")
        self.assert_failure(empty_filter, 1, ERR_LEARNER_NOT_FOUND)

        whitespace_filter = self.list_courses_cli(
            99, "--title-contains", "   "
        )
        self.assert_failure(whitespace_filter, 1, ERR_LEARNER_NOT_FOUND)

    def test_zero_learner_id_with_empty_filter_reports_bad_id(self):
        """编号 0 配空筛选词：先报告学员编号必须为正整数，退出码 2。"""
        self.register_main_sample()

        result = self.list_courses_cli(0, "--title-contains", "")

        self.assert_failure(result, 2, ERR_BAD_LEARNER_ID)


class TestListLearnerCoursesFunctionFilter(unittest.TestCase):
    """直接调用公开函数 list_learner_courses，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(Path(self._tmp.name) / "func.db")
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        for title, chapters in SAMPLE_COURSES:
            add_course(self.conn, title, chapters)
        add_learner(self.conn, "学员甲")
        add_learner(self.conn, "学员乙")
        for course_id in (3, 2, 1, 1):
            enroll_learner(self.conn, 1, course_id)
        enroll_learner(self.conn, 2, 4)

    def test_none_and_omitted_return_all_enrolled_courses(self):
        """省略参数或显式 None 返回该学员全部已报名课程，按编号升序。"""
        expected = [
            {"course_id": 1, "title": "Python入门", "chapter_count": 2},
            {"course_id": 2, "title": "python复习", "chapter_count": 1},
            {"course_id": 3, "title": "Python入门", "chapter_count": 1},
        ]
        self.assertEqual(list_learner_courses(self.conn, 1), expected)
        self.assertEqual(list_learner_courses(self.conn, 1, None), expected)

    def test_function_filter_matches_substring(self):
        """函数筛选与命令行同规则：去首尾空白、大小写敏感、不匹配章节名。"""
        result = list_learner_courses(self.conn, 1, " Python ")
        self.assertEqual(
            result,
            [
                {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                {"course_id": 3, "title": "Python入门", "chapter_count": 1},
            ],
        )

        case_sensitive = list_learner_courses(self.conn, 1, "python")
        self.assertEqual(
            [c["course_id"] for c in case_sensitive], [2]
        )

        chapter_name = list_learner_courses(self.conn, 1, "练习")
        self.assertEqual(chapter_name, [])

        other_learner = list_learner_courses(self.conn, 2, "Python")
        self.assertEqual([c["course_id"] for c in other_learner], [4])

    def test_empty_or_whitespace_filter_raises_validation_error(self):
        """存在学员传入空字符串或纯空白筛选词时抛出 ValidationError。"""
        for bad in ("", "   ", "\t\n  "):
            with self.subTest(title_contains=bad):
                with self.assertRaises(ValidationError) as ctx:
                    list_learner_courses(self.conn, 1, bad)
                self.assertEqual(str(ctx.exception), ERR_EMPTY_TITLE_FILTER)

    def test_unknown_learner_with_empty_filter_returns_none(self):
        """未知正整数学员即使筛选词为空也返回 None，不抛出异常。"""
        self.assertIsNone(list_learner_courses(self.conn, 99, ""))
        self.assertIsNone(list_learner_courses(self.conn, 99, "   "))
        self.assertIsNone(list_learner_courses(self.conn, 99, "Python"))
        self.assertIsNone(list_learner_courses(self.conn, 99))

    def test_enrolled_learner_without_enrollments_returns_empty_list(self):
        """存在但未报名的学员返回空列表，筛选后仍为空列表。"""
        add_learner(self.conn, "学员丙")
        self.assertEqual(list_learner_courses(self.conn, 3), [])
        self.assertEqual(list_learner_courses(self.conn, 3, "Python"), [])

    def test_filter_uses_current_title_after_rename(self):
        """课程改名后按当前标题筛选。"""
        rename_course(self.conn, 3, "Rust入门")
        # 大写 Python 只命中编号 1（编号 2 标题为小写 python，编号 3 已改名）
        self.assertEqual(
            [c["course_id"] for c in list_learner_courses(self.conn, 1, "Python")],
            [1],
        )
        self.assertEqual(
            [c["course_id"] for c in list_learner_courses(self.conn, 1, "Rust")],
            [3],
        )

    def test_reopen_same_file_returns_consistent_results(self):
        """关闭后重新打开同一数据库文件，筛选结果一致。"""
        before = list_learner_courses(self.conn, 1, " Python ")
        self.conn.close()
        reopened = connect(self.db_path)
        self.addCleanup(reopened.close)
        self.assertEqual(list_learner_courses(reopened, 1, " Python "), before)

    def test_function_queries_do_not_modify_data(self):
        """成功与失败的函数调用都不改变课程、学员和报名记录。"""
        from course_progress import list_courses, list_learners

        before = (
            list_courses(self.conn),
            list_learners(self.conn),
            list_learner_courses(self.conn, 1),
            list_learner_courses(self.conn, 2),
        )

        list_learner_courses(self.conn, 1, "Python")
        with self.assertRaises(ValidationError):
            list_learner_courses(self.conn, 1, "")
        self.assertIsNone(list_learner_courses(self.conn, 99, ""))

        after = (
            list_courses(self.conn),
            list_learners(self.conn),
            list_learner_courses(self.conn, 1),
            list_learner_courses(self.conn, 2),
        )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
