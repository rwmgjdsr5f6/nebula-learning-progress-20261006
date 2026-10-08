"""按标题片段筛选学员已报名课程（list-learner-courses --title-contains）的回归测试。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接调用
公开函数 ``list_learner_courses`` 验证函数层面的返回值与校验规则。每个用例
使用独立临时 SQLite 文件与固定合成数据，结束后自动清理，不依赖外网。
"""

import json
import sqlite3
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

# 超出 SQLite 64 位有符号整数范围的学员编号，按不存在处理。
OVERFLOW_LEARNER_ID = 2**63

# 验收样例：四门课程编号 1 至 4，标题依次为
# Python入门、python复习、Python入门、Python旁听，章节数依次为 2、1、1、1；
# 编号二的章节名为 Python练习。
SAMPLE_COURSES = [
    ("Python入门", ["第一章", "第二章"]),
    ("python复习", ["Python练习"]),
    ("Python入门", ["导学"]),
    ("Python旁听", ["绪论"]),
]
# 学员甲（编号 1）先报名 3、2、1，再重复报名 1；学员乙（编号 2）只报名 4。
SAMPLE_LEARNERS = ["学员甲", "学员乙"]
SAMPLE_ENROLLMENTS = [(1, 3), (1, 2), (1, 1), (1, 1), (2, 4)]


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


def register_sample(db_path):
    """按验收顺序通过 CLI 登记四门课程、两位学员与全部报名记录。"""
    for index, (title, chapters) in enumerate(SAMPLE_COURSES, start=1):
        chapter_args = [arg for name in chapters for arg in ("--chapter", name)]
        result = run_cli(
            db_path, "add-course", "--title", title, *chapter_args
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"course_id": index}
    for index, name in enumerate(SAMPLE_LEARNERS, start=1):
        result = run_cli(db_path, "add-learner", "--name", name)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"learner_id": index}
    for learner_id, course_id in SAMPLE_ENROLLMENTS:
        result = run_cli(
            db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "learner_id": learner_id,
            "course_id": course_id,
        }


def seed_sample(conn):
    """按验收顺序直接调用公开函数写入同样的样例数据。"""
    for title, chapters in SAMPLE_COURSES:
        add_course(conn, title, chapters)
    for name in SAMPLE_LEARNERS:
        add_learner(conn, name)
    for learner_id, course_id in SAMPLE_ENROLLMENTS:
        enroll_learner(conn, learner_id, course_id)


def snapshot_business_data(db_path):
    """读取课程、章节、学员与报名记录的全部内容，用于前后比对。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            table: conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("courses", "chapters", "learners", "enrollments")
        }
    finally:
        conn.close()


class ListLearnerCoursesFilterTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，setUp 写入固定样例数据。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "filter.db"
        register_sample(self.db_path)

    def assert_success(self, result, expected):
        """成功查询：退出码 0、标准错误为空、单行 JSON 且内容符合预期。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), expected)

    def assert_failure(self, result, exit_code, message):
        """失败查询：指定退出码与标准错误提示，标准输出为空且无异常堆栈。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def list_courses(self, learner_id, title_contains=None, db_first=True):
        args = ["list-learner-courses", str(learner_id)]
        if title_contains is not None:
            args.extend(["--title-contains", title_contains])
        return run_cli(self.db_path, *args, db_first=db_first)


class TestListLearnerCoursesTitleFilterCli(ListLearnerCoursesFilterTestCase):
    """命令行层面的标题片段筛选回归测试。"""

    def test_fixed_acceptance_sample(self):
        """筛选词 " Python " 只命中编号 1 和 3，按编号升序显示章节数 2 和 1。"""
        result = self.list_courses(1, " Python ")

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
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload.keys()), {"learner_id", "courses"})
        for course in payload["courses"]:
            self.assertEqual(
                set(course.keys()),
                {"course_id", "title", "chapter_count"},
            )

    def test_matching_is_case_sensitive(self):
        """小写 python 只命中编号二，大小写敏感。"""
        result = self.list_courses(1, "python")

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

    def test_chapter_names_are_not_matched(self):
        """只匹配课程标题：仅出现在章节名中的“练习”产生空数组。"""
        result = self.list_courses(1, "练习")
        self.assert_success(result, {"learner_id": 1, "courses": []})

    def test_same_titled_courses_are_not_merged(self):
        """同标题课程分别返回：编号 1 与 3 标题相同但仍是两项。"""
        result = self.list_courses(1, "Python入门")
        courses = json.loads(result.stdout)["courses"]
        self.assertEqual(
            [(c["course_id"], c["title"]) for c in courses],
            [(1, "Python入门"), (3, "Python入门")],
        )

    def test_other_learners_enrollments_do_not_mix_in(self):
        """筛选范围只限该学员报名：学员乙的课程四不混入学员甲的结果。"""
        result = self.list_courses(1, "Python旁听")
        self.assert_success(result, {"learner_id": 1, "courses": []})

        # 学员乙只报名编号四，筛 Python 只命中编号四
        result = self.list_courses(2, "Python")
        self.assert_success(
            result,
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

    def test_duplicate_enrollment_appears_once_under_filter(self):
        """重复报名的编号一在筛选结果中仍只出现一次。"""
        result = self.list_courses(1, "入门")
        self.assertEqual(
            [c["course_id"] for c in json.loads(result.stdout)["courses"]],
            [1, 3],
        )

    def test_percent_underscore_quote_are_literal_characters(self):
        """百分号、下划线、引号按普通字符做连续子串匹配。"""
        special_id = add_course_via_cli(
            self.db_path, '100%_"训练', "唯一章节"
        )
        run_cli(
            self.db_path,
            "enroll-learner",
            "1",
            "--course",
            str(special_id),
        )

        for fragment in ("%", "_", '"', '%_"'):
            with self.subTest(fragment=fragment):
                result = self.list_courses(1, fragment)
                courses = json.loads(result.stdout)["courses"]
                self.assertEqual(
                    [c["course_id"] for c in courses], [special_id]
                )
                self.assertEqual(courses[0]["title"], '100%_"训练')

        # 通配模式 "%_\"" 不应命中不含这些字面字符的其他已报名课程
        wildcard_like = self.list_courses(1, "%Python%")
        self.assert_success(
            wildcard_like, {"learner_id": 1, "courses": []}
        )

    def test_internal_whitespace_is_preserved_in_filter(self):
        """只去首尾空白，内部空白参与匹配：标题中无对应空格则无结果。"""
        with_space = self.list_courses(1, "  Python 入门  ")
        self.assert_success(with_space, {"learner_id": 1, "courses": []})

        without_space = self.list_courses(1, "  Python入门  ")
        self.assertEqual(
            [c["course_id"] for c in json.loads(without_space.stdout)["courses"]],
            [1, 3],
        )

    def test_filter_matches_current_title_after_rename(self):
        """课程改名后按当前标题筛选：新标题命中，旧标题不再命中。"""
        renamed = run_cli(
            self.db_path, "rename-course", "2", "--title", "Python串讲"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        result = self.list_courses(1, "串讲")
        self.assertEqual(
            [c["course_id"] for c in json.loads(result.stdout)["courses"]],
            [2],
        )

        old_title = self.list_courses(1, "复习")
        self.assert_success(old_title, {"learner_id": 1, "courses": []})

    def test_omitted_filter_returns_all_enrolled_courses(self):
        """省略筛选参数返回学员甲全部三门已报名课程，按编号升序。"""
        result = self.list_courses(1)

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

    def test_existing_learner_without_enrollments_returns_empty_list(self):
        """存在但未报名的学员，无筛选与有筛选均返回空数组。"""
        added = run_cli(self.db_path, "add-learner", "--name", "学员丙")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 3})

        self.assert_success(
            self.list_courses(3), {"learner_id": 3, "courses": []}
        )
        self.assert_success(
            self.list_courses(3, "Python"), {"learner_id": 3, "courses": []}
        )

    def test_no_match_returns_empty_list(self):
        """已报名课程中无标题匹配时返回空数组，退出码 0。"""
        result = self.list_courses(1, "不存在的标题片段")
        self.assert_success(result, {"learner_id": 1, "courses": []})

    def test_empty_filter_exits_1_with_message_and_no_traceback(self):
        """存在学员传入空字符串：退出码 1，标准输出为空，提示到标准错误。"""
        self.assert_failure(
            self.list_courses(1, ""),
            1,
            ERR_EMPTY_TITLE_FILTER,
        )

    def test_whitespace_only_filter_exits_1_with_message(self):
        """仅含空白的筛选词同样失败：退出码 1，标准输出为空。"""
        self.assert_failure(
            self.list_courses(1, "   \t "),
            1,
            ERR_EMPTY_TITLE_FILTER,
        )

    def test_unknown_learner_reports_not_found_even_with_empty_filter(self):
        """未知正整数编号优先报告学员不存在，即使筛选词为空。"""
        for args in (
            ["list-learner-courses", "999"],
            ["list-learner-courses", "999", "--title-contains", ""],
            ["list-learner-courses", "999", "--title-contains", "Python"],
        ):
            with self.subTest(args=args):
                self.assert_failure(
                    run_cli(self.db_path, *args),
                    1,
                    ERR_LEARNER_NOT_FOUND,
                )

    def test_overflow_learner_id_reports_not_found(self):
        """超出 SQLite 整数范围的编号按学员不存在处理，退出码 1。"""
        self.assert_failure(
            self.list_courses(OVERFLOW_LEARNER_ID, ""),
            1,
            ERR_LEARNER_NOT_FOUND,
        )

    def test_zero_id_with_empty_filter_reports_bad_id_first(self):
        """编号 0 配空筛选词时优先报告编号必须为正整数，退出码 2。"""
        self.assert_failure(
            self.list_courses(0, ""),
            2,
            ERR_BAD_LEARNER_ID,
        )

    def test_db_position_and_repeated_cross_process_queries_agree(self):
        """--db 在子命令前后、以及重新打开同一文件，结果均一致。"""
        before = self.list_courses(1, " Python ")
        again = self.list_courses(1, " Python ")
        db_after = self.list_courses(1, " Python ", db_first=False)

        self.assertEqual(before.returncode, 0)
        self.assertEqual(again.stdout, before.stdout)
        self.assertEqual(db_after.stdout, before.stdout)

        # 另起进程重新打开同一文件（CLI 每次调用本就是新进程）结果不变
        reopened = self.list_courses(1, " Python ")
        self.assertEqual(reopened.stdout, before.stdout)

        # 无筛选查询在两种 --db 位置下同样一致
        all_first = self.list_courses(1)
        all_after = self.list_courses(1, db_first=False)
        self.assertEqual(all_after.stdout, all_first.stdout)

    def test_queries_do_not_change_business_data(self):
        """成功与失败查询均为只读：课程、学员与报名记录前后一致。"""
        before = snapshot_business_data(self.db_path)

        ok = self.list_courses(1, " Python ")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        no_match = self.list_courses(1, "不存在的标题片段")
        self.assertEqual(no_match.returncode, 0)
        empty_filter = self.list_courses(1, "")
        self.assertEqual(empty_filter.returncode, 1)
        unknown = self.list_courses(999, "Python")
        self.assertEqual(unknown.returncode, 1)
        bad_id = self.list_courses(0, "")
        self.assertEqual(bad_id.returncode, 2)

        self.assertEqual(snapshot_business_data(self.db_path), before)


class TestListLearnerCoursesTitleFilterFunction(unittest.TestCase):
    """直接调用公开函数 list_learner_courses，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "func.db"
        self.conn = connect(str(self.db_path))
        self.addCleanup(self.conn.close)
        seed_sample(self.conn)

    def test_none_and_omitted_return_all_enrolled_sorted(self):
        """省略参数或显式 None 返回学员甲全部已报名课程，按编号升序。"""
        expected = [
            {"course_id": 1, "title": "Python入门", "chapter_count": 2},
            {"course_id": 2, "title": "python复习", "chapter_count": 1},
            {"course_id": 3, "title": "Python入门", "chapter_count": 1},
        ]
        self.assertEqual(list_learner_courses(self.conn, 1), expected)
        self.assertEqual(
            list_learner_courses(self.conn, 1, None), expected
        )

    def test_function_filter_matches_substring_case_sensitively(self):
        """函数筛选与命令行同规则：去首尾空白、大小写敏感、字面匹配。"""
        result = list_learner_courses(self.conn, 1, " Python ")
        self.assertEqual(
            [(c["course_id"], c["chapter_count"]) for c in result],
            [(1, 2), (3, 1)],
        )

        lower = list_learner_courses(self.conn, 1, "python")
        self.assertEqual([c["course_id"] for c in lower], [2])

        # 只匹配课程标题，不匹配章节名
        self.assertEqual(list_learner_courses(self.conn, 1, "练习"), [])
        self.assertEqual(
            list_learner_courses(self.conn, 1, "不存在的标题"), []
        )

    def test_function_filter_scoped_to_learner_enrollments(self):
        """筛选只在该学员已报名课程中进行，其他学员的报名不混入。"""
        self.assertEqual(
            list_learner_courses(self.conn, 1, "Python旁听"), []
        )
        result = list_learner_courses(self.conn, 2, "Python")
        self.assertEqual(
            result,
            [
                {
                    "course_id": 4,
                    "title": "Python旁听",
                    "chapter_count": 1,
                }
            ],
        )

    def test_function_same_titled_courses_are_not_merged(self):
        """同标题课程分别返回两项，不做合并。"""
        result = list_learner_courses(self.conn, 1, "入门")
        self.assertEqual(
            [(c["course_id"], c["title"]) for c in result],
            [(1, "Python入门"), (3, "Python入门")],
        )

    def test_function_special_characters_are_literal(self):
        """百分号、下划线、引号按普通字符做连续子串匹配。"""
        special_id = add_course(self.conn, '100%_"训练', ["唯一章节"])
        enroll_learner(self.conn, 1, special_id)

        for fragment in ("%", "_", '"', '%_"'):
            with self.subTest(fragment=fragment):
                result = list_learner_courses(self.conn, 1, fragment)
                self.assertEqual(
                    [c["course_id"] for c in result], [special_id]
                )
        # 类通配写法不产生通配效果
        self.assertEqual(
            list_learner_courses(self.conn, 1, "%Python%"), []
        )

    def test_function_internal_whitespace_preserved(self):
        """筛选词去首尾空白但保留内部空白参与匹配。"""
        self.assertEqual(
            list_learner_courses(self.conn, 1, "  Python入门  "),
            [
                {"course_id": 1, "title": "Python入门", "chapter_count": 2},
                {"course_id": 3, "title": "Python入门", "chapter_count": 1},
            ],
        )
        self.assertEqual(
            list_learner_courses(self.conn, 1, "Python 入门"), []
        )

    def test_function_matches_current_title_after_rename(self):
        """改名后使用新标题匹配，旧标题不再命中。"""
        rename_course(self.conn, 2, "Python串讲")
        result = list_learner_courses(self.conn, 1, "串讲")
        self.assertEqual([c["course_id"] for c in result], [2])
        self.assertEqual(list_learner_courses(self.conn, 1, "复习"), [])

    def test_function_existing_learner_without_enrollments_returns_empty(self):
        """存在但未报名的学员省略或给出筛选词均返回空列表。"""
        learner_id = add_learner(self.conn, "学员丙")
        self.assertEqual(list_learner_courses(self.conn, learner_id), [])
        self.assertEqual(
            list_learner_courses(self.conn, learner_id, "Python"), []
        )

    def test_function_empty_or_whitespace_filter_raises_validation_error(self):
        """学员存在但筛选词为空或仅空白：抛出 ValidationError。"""
        for bad in ("", "   ", "\t\n  "):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError) as ctx:
                    list_learner_courses(self.conn, 1, bad)
                self.assertEqual(str(ctx.exception), ERR_EMPTY_TITLE_FILTER)

    def test_function_unknown_learner_returns_none_even_with_empty_filter(self):
        """未知正整数编号与超范围编号返回 None，且优先于空筛选词校验。"""
        for learner_id in (999, OVERFLOW_LEARNER_ID):
            self.assertIsNone(list_learner_courses(self.conn, learner_id))
            self.assertIsNone(
                list_learner_courses(self.conn, learner_id, "Python")
            )
            # 即使筛选词为空也返回 None，不抛出 ValidationError
            self.assertIsNone(
                list_learner_courses(self.conn, learner_id, "")
            )

    def test_function_queries_do_not_change_business_data(self):
        """成功与失败查询均不修改课程、章节、学员与报名记录。"""
        before = snapshot_business_data(self.db_path)

        list_learner_courses(self.conn, 1, " Python ")
        list_learner_courses(self.conn, 1)
        list_learner_courses(self.conn, 1, "不存在的标题片段")
        with self.assertRaises(ValidationError):
            list_learner_courses(self.conn, 1, "")
        self.assertIsNone(list_learner_courses(self.conn, 999, "Python"))

        self.assertEqual(snapshot_business_data(self.db_path), before)

    def test_reopen_same_file_keeps_filter_results_consistent(self):
        """关闭后重新打开同一文件，筛选结果保持一致。"""
        expected = list_learner_courses(self.conn, 1, " Python ")
        expected_all = list_learner_courses(self.conn, 1)
        self.conn.close()

        reopened = connect(str(self.db_path))
        self.addCleanup(reopened.close)
        self.assertEqual(
            list_learner_courses(reopened, 1, " Python "), expected
        )
        self.assertEqual(
            list_learner_courses(reopened, 1), expected_all
        )


def add_course_via_cli(db_path, title, *chapters):
    """测试辅助：通过 CLI 登记课程并返回新课程编号。"""
    args = ["add-course", "--title", title]
    for chapter in chapters:
        args.extend(["--chapter", chapter])
    result = run_cli(db_path, *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["course_id"]


if __name__ == "__main__":
    unittest.main()
