"""按姓名片段筛选课程报名名册（list-course-learners --name-contains）的回归测试。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接
调用公开函数 ``list_course_learners`` 验证函数层面的匹配与校验规则。
每个用例使用独立临时目录与固定合成数据，结束后自动清理，不依赖外网。
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
    list_course_learners,
)
from course_progress.core import (
    ERR_BAD_ID,
    ERR_EMPTY_LEARNER_NAME_FILTER,
    ERR_NOT_FOUND,
    ValidationError,
    rename_learner,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 超出 SQLite 64 位有符号整数范围的课程编号，按不存在处理。
OVERFLOW_COURSE_ID = 9223372036854775808

# 验收样例：两门课程；三位学员按编号依次命名为 Ann、ann、Ann；
# 前两位报名课程一，第三位只报名课程二。
SAMPLE_COURSES = [("课程一", ["第一章"]), ("课程二", ["导论"])]
SAMPLE_LEARNERS = ["Ann", "ann", "Ann"]
SAMPLE_ENROLLMENTS = [(1, 1), (2, 1), (3, 2)]


def run_cli(db_path, *args):
    """以独立进程运行 CLI，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, "-m", "course_progress", "--db", str(db_path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def register_sample(db_path):
    """按验收顺序通过 CLI 登记两门课程、三位学员与三条报名记录。"""
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


class TestListCourseLearnersNameFilterCli(unittest.TestCase):
    """CLI 层面：每个用例一个独立临时目录，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "roster.db"
        register_sample(self.db_path)

    def assert_success(self, result, expected):
        """成功筛选：退出码 0、标准错误为空、单行 JSON 且内容符合预期。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_filter_with_surrounding_spaces_matches_only_first_learner(self):
        """筛选词 " Ann " 去首尾空白后大小写敏感，课程一只命中编号一。"""
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", " Ann ",
        )

        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 1, "name": "Ann"}],
            },
        )

    def test_filter_is_case_sensitive(self):
        """小写 "ann" 在课程一只命中编号二。"""
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "ann",
        )

        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 2, "name": "ann"}],
            },
        )

    def test_omitted_filter_returns_all_enrolled_sorted_by_id(self):
        """省略筛选词返回课程一前两位学员，按编号升序，字段不多不少。"""
        result = run_cli(self.db_path, "list-course-learners", "1")

        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "Ann"},
                    {"learner_id": 2, "name": "ann"},
                ],
            },
        )
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload.keys()), {"course_id", "learners"})
        for learner in payload["learners"]:
            self.assertEqual(set(learner.keys()), {"learner_id", "name"})

    def test_filter_only_applies_to_target_course_enrollments(self):
        """筛选只作用于目标课程的已报名学员，不匹配其他课程学员。"""
        # 课程二只有编号三（Ann）：筛 "Ann" 只命中编号三，不含编号一
        result = run_cli(
            self.db_path, "list-course-learners", "2",
            "--name-contains", "Ann",
        )
        self.assert_success(
            result,
            {
                "course_id": 2,
                "learners": [{"learner_id": 3, "name": "Ann"}],
            },
        )

        # 编号二（ann）未报名课程二，小写 "ann" 在课程二无匹配
        result = run_cli(
            self.db_path, "list-course-learners", "2",
            "--name-contains", "ann",
        )
        self.assert_success(result, {"course_id": 2, "learners": []})

    def test_percent_underscore_quote_are_literal_characters(self):
        """百分号、下划线、引号按普通字符处理，不作为通配模式。"""
        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", 'A%_"n'
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        # "%" 只字面命中含百分号的编号一，不匹配其他学员
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "%",
        )
        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 1, "name": 'A%_"n'}],
            },
        )

        # 下划线与引号同样字面匹配
        for fragment in ("_", '"', '%_"'):
            result = run_cli(
                self.db_path, "list-course-learners", "1",
                "--name-contains", fragment,
            )
            self.assert_success(
                result,
                {
                    "course_id": 1,
                    "learners": [{"learner_id": 1, "name": 'A%_"n'}],
                },
            )

    def test_internal_whitespace_is_preserved_in_filter(self):
        """内部空白参与匹配：只去首尾空白，姓名中无该片段时无结果。"""
        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", "An n"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        # 首尾空白被去掉，内部空格保留并命中 "An n"
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "  An n  ",
        )
        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 1, "name": "An n"}],
            },
        )

        # 连续两个内部空格在姓名中不存在，无匹配
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "An  n",
        )
        self.assert_success(result, {"course_id": 1, "learners": []})

    def test_rename_then_filter_matches_new_name(self):
        """改名后按当前姓名匹配：新姓名命中，旧姓名不再命中。"""
        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", "Beth"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "Beth",
        )
        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 1, "name": "Beth"}],
            },
        )

        # 旧姓名 "Ann" 在课程一已无匹配（编号二是小写 ann）
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "Ann",
        )
        self.assert_success(result, {"course_id": 1, "learners": []})

    def test_no_match_returns_empty_roster(self):
        """无匹配学员时返回空名册，退出码 0。"""
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "不存在的姓名",
        )
        self.assert_success(result, {"course_id": 1, "learners": []})

    def test_course_without_enrollments_returns_empty_roster(self):
        """课程存在但无人报名时返回空名册，退出码 0。"""
        added = run_cli(
            self.db_path, "add-course", "--title", "课程三",
            "--chapter", "绪论",
        )
        self.assertEqual(json.loads(added.stdout), {"course_id": 3})

        result = run_cli(self.db_path, "list-course-learners", "3")
        self.assert_success(result, {"course_id": 3, "learners": []})

        filtered = run_cli(
            self.db_path, "list-course-learners", "3",
            "--name-contains", "Ann",
        )
        self.assert_success(filtered, {"course_id": 3, "learners": []})

    def test_empty_filter_exits_1_with_message_and_no_traceback(self):
        """课程存在但筛选词为空字符串：退出码 1，提示到标准错误。"""
        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", ""
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_LEARNER_NAME_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_whitespace_only_filter_exits_1_with_message(self):
        """仅含空白的筛选词同样失败：退出码 1，标准输出为空。"""
        result = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "   \t ",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_LEARNER_NAME_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_course_reports_not_found_even_with_empty_filter(self):
        """未知正整数课程编号：优先报告课程不存在，即使筛选词为空。"""
        for args in (
            ("list-course-learners", "999"),
            ("list-course-learners", "999", "--name-contains", ""),
            ("list-course-learners", "999", "--name-contains", "Ann"),
        ):
            result = run_cli(self.db_path, *args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), ERR_NOT_FOUND)
            self.assertNotIn("Traceback", result.stderr)

    def test_overflow_course_id_reports_not_found(self):
        """超出 SQLite 整数范围的编号按课程不存在处理，退出码 1。"""
        for args in (
            ("list-course-learners", str(OVERFLOW_COURSE_ID)),
            ("list-course-learners", str(OVERFLOW_COURSE_ID),
             "--name-contains", ""),
        ):
            result = run_cli(self.db_path, *args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), ERR_NOT_FOUND)
            self.assertNotIn("Traceback", result.stderr)

    def test_non_positive_or_non_numeric_id_exits_2(self):
        """编号为 0、负数或非数字：报告编号必须为正整数，退出码 2。"""
        for bad_id in ("0", "-1", "abc"):
            result = run_cli(
                self.db_path, "list-course-learners", bad_id
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), ERR_BAD_ID)
            self.assertNotIn("Traceback", result.stderr)

    def test_queries_do_not_change_business_data(self):
        """成功与失败筛选均为只读：课程、学员与报名记录前后一致。"""
        before = snapshot_business_data(self.db_path)

        ok = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "Ann",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        failed_filter = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", ""
        )
        self.assertEqual(failed_filter.returncode, 1)
        failed_course = run_cli(
            self.db_path, "list-course-learners", "999",
            "--name-contains", "Ann",
        )
        self.assertEqual(failed_course.returncode, 1)

        self.assertEqual(snapshot_business_data(self.db_path), before)


class TestListCourseLearnersFunctionFilter(unittest.TestCase):
    """直接调用公开函数 list_course_learners，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "func.db"
        self.conn = connect(str(self.db_path))
        self.addCleanup(self.conn.close)
        seed_sample(self.conn)

    def test_none_and_omitted_return_all_enrolled_sorted(self):
        """省略参数或显式 None 返回课程一全部已报名学员，按编号升序。"""
        expected = [
            {"learner_id": 1, "name": "Ann"},
            {"learner_id": 2, "name": "ann"},
        ]
        self.assertEqual(list_course_learners(self.conn, 1), expected)
        self.assertEqual(list_course_learners(self.conn, 1, None), expected)

    def test_function_filter_matches_substring_case_sensitively(self):
        """" Ann " 去首尾空白后只命中编号一，小写 "ann" 只命中编号二。"""
        result = list_course_learners(self.conn, 1, " Ann ")
        self.assertEqual(result, [{"learner_id": 1, "name": "Ann"}])

        result = list_course_learners(self.conn, 1, "ann")
        self.assertEqual(result, [{"learner_id": 2, "name": "ann"}])

    def test_function_filter_scoped_to_target_course(self):
        """只在该课程已报名学员中筛选，不匹配其他课程学员。"""
        result = list_course_learners(self.conn, 2, "Ann")
        self.assertEqual(result, [{"learner_id": 3, "name": "Ann"}])
        self.assertEqual(list_course_learners(self.conn, 2, "ann"), [])

    def test_function_special_characters_are_literal(self):
        """百分号、下划线、引号按普通字符做连续子串匹配。"""
        rename_learner(self.conn, 1, 'A%_"n')

        for fragment in ("%", "_", '"', '%_"'):
            self.assertEqual(
                list_course_learners(self.conn, 1, fragment),
                [{"learner_id": 1, "name": 'A%_"n'}],
            )
        # "%" 不是通配符：不匹配不含百分号的编号二
        self.assertEqual(
            [learner["learner_id"] for learner in
             list_course_learners(self.conn, 1, "%")],
            [1],
        )

    def test_function_internal_whitespace_preserved(self):
        """筛选词去首尾空白但保留内部空白参与匹配。"""
        rename_learner(self.conn, 1, "An n")
        self.assertEqual(
            list_course_learners(self.conn, 1, "  An n  "),
            [{"learner_id": 1, "name": "An n"}],
        )
        self.assertEqual(list_course_learners(self.conn, 1, "An  n"), [])

    def test_function_matches_current_name_after_rename(self):
        """改名后使用新姓名匹配，旧姓名不再命中。"""
        rename_learner(self.conn, 1, "Beth")
        self.assertEqual(
            list_course_learners(self.conn, 1, "Beth"),
            [{"learner_id": 1, "name": "Beth"}],
        )
        self.assertEqual(list_course_learners(self.conn, 1, "Ann"), [])

    def test_function_no_match_and_no_enrollment_return_empty_list(self):
        """无匹配与无人报名的课程均返回空列表。"""
        self.assertEqual(list_course_learners(self.conn, 1, "不存在"), [])
        empty_course_id = add_course(self.conn, "课程三", ["绪论"])
        self.assertEqual(list_course_learners(self.conn, empty_course_id), [])
        self.assertEqual(
            list_course_learners(self.conn, empty_course_id, "Ann"), []
        )

    def test_function_empty_or_whitespace_filter_raises_validation_error(self):
        """课程存在但筛选词为空或仅空白：抛出 ValidationError。"""
        for bad in ("", "   ", "\t\n  "):
            with self.assertRaises(ValidationError) as ctx:
                list_course_learners(self.conn, 1, bad)
            self.assertEqual(
                str(ctx.exception), ERR_EMPTY_LEARNER_NAME_FILTER
            )

    def test_function_unknown_course_returns_none_even_with_empty_filter(self):
        """未知正整数编号与超范围编号返回 None，且优先于空筛选词校验。"""
        for course_id in (999, OVERFLOW_COURSE_ID):
            self.assertIsNone(list_course_learners(self.conn, course_id))
            self.assertIsNone(
                list_course_learners(self.conn, course_id, "Ann")
            )
            # 即使筛选词为空也返回 None，不抛出 ValidationError
            self.assertIsNone(list_course_learners(self.conn, course_id, ""))

    def test_function_queries_do_not_change_business_data(self):
        """成功与失败筛选均不修改课程、学员与报名记录。"""
        before = snapshot_business_data(self.db_path)

        list_course_learners(self.conn, 1, "Ann")
        list_course_learners(self.conn, 1)
        with self.assertRaises(ValidationError):
            list_course_learners(self.conn, 1, "")
        self.assertIsNone(list_course_learners(self.conn, 999, "Ann"))

        self.assertEqual(snapshot_business_data(self.db_path), before)

    def test_reopen_same_file_keeps_filter_results_consistent(self):
        """关闭后重新打开同一文件，筛选结果保持一致。"""
        expected = list_course_learners(self.conn, 1, " Ann ")
        expected_all = list_course_learners(self.conn, 1)
        self.conn.close()

        reopened = connect(str(self.db_path))
        self.addCleanup(reopened.close)
        self.assertEqual(list_course_learners(reopened, 1, " Ann "), expected)
        self.assertEqual(list_course_learners(reopened, 1), expected_all)


if __name__ == "__main__":
    unittest.main()
