"""按学员姓名片段筛选课程报名名册（list-course-learners --name-contains）
的回归测试。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接
调用公开函数 ``list_course_learners`` 验证函数层面的筛选与错误优先级。
基础样例固定为两门课程、三位学员（按编号依次命名 Ann、ann、Ann，前两位
报名课程一，第三位只报名课程二）。每个用例使用独立临时 SQLite 文件与
固定合成数据，不依赖外网，结束后自动清理。
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from course_progress import connect, list_course_learners
from course_progress.core import (
    ERR_BAD_ID,
    ERR_EMPTY_LEARNER_NAME_FILTER,
    ERR_NOT_FOUND,
    ValidationError,
    add_course,
    add_learner,
    enroll_learner,
    rename_learner,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SQLITE_INT64_MAX = 2**63 - 1

# 验收样例：两位学员报名课程一，第三位同名学员只报名课程二；
# 课程三在需要“无人报名”场景的用例中另行登记。
SAMPLE_LEARNER_NAMES = ["Ann", "ann", "Ann"]


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


def dump_business_data(db_path):
    """读取课程、学员、报名记录三张业务表的全部内容，用于前后比对。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return (
            conn.execute("SELECT id, title FROM courses ORDER BY id").fetchall(),
            conn.execute("SELECT id, name FROM learners ORDER BY id").fetchall(),
            conn.execute(
                "SELECT learner_id, course_id FROM enrollments"
                " ORDER BY learner_id, course_id"
            ).fetchall(),
        )
    finally:
        conn.close()


class CourseLearnersCliTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "roster.db"

    def register_sample(self):
        """登记两门课程、三位学员并按验收样例建立报名关系。"""
        for course_id, title in enumerate(("课程一", "课程二"), start=1):
            result = run_cli(
                self.db_path, "add-course", "--title", title, "--chapter", "导学"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"course_id": course_id})
        for learner_id, name in enumerate(SAMPLE_LEARNER_NAMES, start=1):
            result = run_cli(self.db_path, "add-learner", "--name", name)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout), {"learner_id": learner_id}
            )
        for learner_id, course_id in ((1, 1), (2, 1), (3, 2)):
            result = run_cli(
                self.db_path,
                "enroll-learner",
                str(learner_id),
                "--course",
                str(course_id),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

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

    def list_course_learners(self, course_id, name_contains=None, db_first=True):
        args = ["list-course-learners", str(course_id)]
        if name_contains is not None:
            args.extend(["--name-contains", name_contains])
        return run_cli(self.db_path, *args, db_first=db_first)

    def assert_success_roster(self, result, expected_learners, course_id):
        """成功查询：退出码 0、标准错误为空、单行 JSON 且只含规定字段。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload.keys()), {"course_id", "learners"})
        self.assertEqual(payload["course_id"], course_id)
        for learner in payload["learners"]:
            self.assertEqual(set(learner.keys()), {"learner_id", "name"})
        self.assertEqual(payload["learners"], expected_learners)

    def assert_failure(self, result, exit_code, message):
        """失败查询：规定退出码与标准错误消息，标准输出为空且无异常堆栈。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)


class TestListCourseLearnersNameFilter(CourseLearnersCliTestCase):
    def test_fixed_acceptance_sample(self):
        """课程一：省略筛选词返回前两位且按编号升序；" Ann " 只命中编号一，
        "ann" 只命中编号二。"""
        self.register_sample()

        full = self.list_course_learners(1)
        self.assert_success_roster(
            full,
            [
                {"learner_id": 1, "name": "Ann"},
                {"learner_id": 2, "name": "ann"},
            ],
            1,
        )

        capital = self.list_course_learners(1, " Ann ")
        self.assert_success_roster(
            capital, [{"learner_id": 1, "name": "Ann"}], 1
        )

        lower = self.list_course_learners(1, "ann")
        self.assert_success_roster(
            lower, [{"learner_id": 2, "name": "ann"}], 1
        )

    def test_filter_is_scoped_to_enrolled_learners_of_target_course(self):
        """筛选只作用于目标课程的已报名学员：编号三虽同名但只报名课程二，
        不出现在课程一名册中。"""
        self.register_sample()

        course_one = self.list_course_learners(1, "Ann")
        self.assert_success_roster(
            course_one, [{"learner_id": 1, "name": "Ann"}], 1
        )

        course_two = self.list_course_learners(2, "Ann")
        self.assert_success_roster(
            course_two, [{"learner_id": 3, "name": "Ann"}], 2
        )

        # 课程二没有任何姓名包含小写 ann 的已报名学员
        self.assert_success_roster(
            self.list_course_learners(2, "ann"), [], 2
        )

    def test_matching_is_case_sensitive(self):
        """大小写敏感：ANN 在课程一的 Ann/ann 中均无连续匹配。"""
        self.register_sample()

        self.assert_success_roster(
            self.list_course_learners(1, "ANN"), [], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, "aNN"), [], 1
        )

    def test_surrounding_whitespace_trimmed_but_internal_whitespace_kept(self):
        """筛选词只去首尾空白，内部空白参与匹配。"""
        self.register_sample()
        learner_id = self.add_learner("A nn")
        self.enroll(learner_id, 1)

        # 首尾空白被去除后等价于 "A nn"，只命中编号四
        self.assert_success_roster(
            self.list_course_learners(1, "  A nn  "),
            [{"learner_id": learner_id, "name": "A nn"}],
            1,
        )
        # "Ann" 不是 "A nn"（中间带空格）的连续子串，仍只命中编号一
        self.assert_success_roster(
            self.list_course_learners(1, "Ann"),
            [{"learner_id": 1, "name": "Ann"}],
            1,
        )

    def test_percent_underscore_and_quotes_are_literal_characters(self):
        """百分号、下划线、单双引号均按普通字符做连续子串匹配。"""
        self.register_sample()
        learner_id = self.add_learner('100%_ann"\'')
        self.enroll(learner_id, 1)
        special = {"learner_id": learner_id, "name": '100%_ann"\''}

        # 若按 SQL LIKE 解释，"%" 与 "_" 会匹配全部学员；这里只命中字面含
        # 这些字符的编号四
        self.assert_success_roster(
            self.list_course_learners(1, "%"), [special], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, "_"), [special], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, "%_"), [special], 1
        )
        # 通配语义下 "%ann" 会匹配以 ann 结尾的编号二；字面匹配时姓名中
        # 百分号后面是下划线，不存在该子串，名册为空
        self.assert_success_roster(
            self.list_course_learners(1, "%ann"), [], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, 'ann"\''), [special], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, '"'), [special], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, "'"), [special], 1
        )

    def test_matching_uses_current_name_after_rename(self):
        """学员改名后按新姓名匹配，旧姓名不再命中。"""
        self.register_sample()

        renamed = run_cli(
            self.db_path, "rename-learner", "2", "--name", "Ann"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        # 编号一改名为 Bob 后："Ann" 只剩编号二，"Bob" 命中编号一
        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", "Bob"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)

        self.assert_success_roster(
            self.list_course_learners(1, "Ann"),
            [{"learner_id": 2, "name": "Ann"}],
            1,
        )
        self.assert_success_roster(
            self.list_course_learners(1, "ann"), [], 1
        )
        self.assert_success_roster(
            self.list_course_learners(1, "Bob"),
            [{"learner_id": 1, "name": "Bob"}],
            1,
        )

    def test_no_match_returns_empty_roster(self):
        """有报名学员但无姓名匹配时返回空名册，退出码仍为 0。"""
        self.register_sample()

        self.assert_success_roster(
            self.list_course_learners(1, "不存在的片段"), [], 1
        )
        self.assert_success_roster(
            self.list_course_learners(2, "zzz"), [], 2
        )

    def test_course_without_enrollments_returns_empty_roster(self):
        """课程存在但无人报名时，无论是否筛选均返回空名册。"""
        self.register_sample()
        # 课程三存在但没有任何学员报名
        result = run_cli(
            self.db_path, "add-course", "--title", "课程三", "--chapter", "导学"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_success_roster(self.list_course_learners(3), [], 3)
        self.assert_success_roster(
            self.list_course_learners(3, "Ann"), [], 3
        )

    def test_db_option_after_subcommand(self):
        """--db 写在子命令后同样可用，结果一致。"""
        self.register_sample()

        result = self.list_course_learners(1, " Ann ", db_first=False)
        self.assert_success_roster(
            result, [{"learner_id": 1, "name": "Ann"}], 1
        )


class TestListCourseLearnersNameFilterFailures(CourseLearnersCliTestCase):
    def test_empty_or_whitespace_filter_on_existing_course(self):
        """课程存在但筛选词为空字符串或仅含空白：退出码 1，标准错误给出
        固定提示，标准输出为空且无异常堆栈。"""
        self.register_sample()

        for bad in ("", "   ", "\t\n  "):
            with self.subTest(name_contains=bad):
                self.assert_failure(
                    self.list_course_learners(1, bad),
                    1,
                    ERR_EMPTY_LEARNER_NAME_FILTER,
                )

        # 无人报名的课程同样先判定筛选词非空
        run_cli(self.db_path, "add-course", "--title", "课程三", "--chapter", "x")
        self.assert_failure(
            self.list_course_learners(3, "  "),
            1,
            ERR_EMPTY_LEARNER_NAME_FILTER,
        )

    def test_unknown_course_reported_before_empty_filter(self):
        """未知正整数课程编号：即使同时传入空筛选词，也先报告课程不存在。"""
        self.register_sample()

        self.assert_failure(
            self.list_course_learners(99), 1, ERR_NOT_FOUND
        )
        self.assert_failure(
            self.list_course_learners(99, ""), 1, ERR_NOT_FOUND
        )
        self.assert_failure(
            self.list_course_learners(99, "   "), 1, ERR_NOT_FOUND
        )

    def test_overflow_id_treated_as_unknown_course(self):
        """9223372036854775808 超出 SQLite 整数范围，按课程不存在处理，
        且优先于空筛选词报错。"""
        self.register_sample()
        overflow = str(SQLITE_INT64_MAX + 1)

        self.assert_failure(
            self.list_course_learners(overflow), 1, ERR_NOT_FOUND
        )
        self.assert_failure(
            self.list_course_learners(overflow, ""), 1, ERR_NOT_FOUND
        )

    def test_bad_course_id_format(self):
        """编号为 0、负数或非数字时先报告课程编号必须为正整数，退出码 2；
        即使筛选词同时为空也先报格式错误。"""
        self.register_sample()

        for raw in ("0", "-3", "abc", "1.5", ""):
            with self.subTest(course_id=raw):
                self.assert_failure(
                    self.list_course_learners(raw), 2, ERR_BAD_ID
                )
        self.assert_failure(
            self.list_course_learners("0", ""), 2, ERR_BAD_ID
        )

    def test_success_and_failure_filters_are_read_only(self):
        """成功与失败筛选都不改变课程、学员与报名记录；反复重开同一文件
        查询结果保持一致。"""
        self.register_sample()
        before = dump_business_data(self.db_path)

        expected_success = [
            self.list_course_learners(1),
            self.list_course_learners(1, " Ann "),
            self.list_course_learners(1, "ann"),
            self.list_course_learners(1, "无匹配"),
            self.list_course_learners(2, "Ann"),
            self.list_course_learners(3),
        ]
        # 全部失败路径：空筛选词、未知课程、超范围编号、非法格式
        failing = [
            self.list_course_learners(1, ""),
            self.list_course_learners(1, "  "),
            self.list_course_learners(99),
            self.list_course_learners(99, ""),
            self.list_course_learners(str(SQLITE_INT64_MAX + 1)),
            self.list_course_learners("0"),
            self.list_course_learners("abc", ""),
        ]

        self.assertEqual(dump_business_data(self.db_path), before)

        # 每次 CLI 调用都是独立进程（关闭后重新打开同一文件），结果一致
        repeated = [
            self.list_course_learners(1),
            self.list_course_learners(1, " Ann "),
            self.list_course_learners(1, "ann"),
            self.list_course_learners(1, "无匹配"),
            self.list_course_learners(2, "Ann"),
            self.list_course_learners(3),
        ]
        self.assertEqual(
            [r.stdout for r in repeated],
            [r.stdout for r in expected_success],
        )
        for result in failing:
            self.assertEqual(result.stdout, "")
        self.assertEqual(dump_business_data(self.db_path), before)


class TestListCourseLearnersFunctionFilter(unittest.TestCase):
    """直接调用公开函数 list_course_learners，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_file = Path(self._tmp.name) / "func.db"
        self.conn = connect(str(self.db_file))
        self.addCleanup(self.conn.close)

        add_course(self.conn, "课程一", ["导学"])
        add_course(self.conn, "课程二", ["导学"])
        for name in SAMPLE_LEARNER_NAMES:
            add_learner(self.conn, name)
        enroll_learner(self.conn, 1, 1)
        enroll_learner(self.conn, 2, 1)
        enroll_learner(self.conn, 3, 2)

    def test_none_and_omitted_return_enrolled_learners_sorted(self):
        """省略参数或显式 None 返回课程一的前两位学员，按编号升序。"""
        expected = [
            {"learner_id": 1, "name": "Ann"},
            {"learner_id": 2, "name": "ann"},
        ]
        self.assertEqual(list_course_learners(self.conn, 1), expected)
        self.assertEqual(
            list_course_learners(self.conn, 1, None), expected
        )
        for learner in expected:
            self.assertEqual(set(learner.keys()), {"learner_id", "name"})

    def test_fixed_sample_substring_matching(self):
        """函数层面锁定验收样例：" Ann " 只命中编号一，"ann" 只命中编号二。"""
        self.assertEqual(
            list_course_learners(self.conn, 1, " Ann "),
            [{"learner_id": 1, "name": "Ann"}],
        )
        self.assertEqual(
            list_course_learners(self.conn, 1, "ann"),
            [{"learner_id": 2, "name": "ann"}],
        )
        # 大小写敏感与课程范围
        self.assertEqual(list_course_learners(self.conn, 1, "ANN"), [])
        self.assertEqual(
            list_course_learners(self.conn, 2, "Ann"),
            [{"learner_id": 3, "name": "Ann"}],
        )

    def test_special_characters_are_literal(self):
        """百分号、下划线与引号在函数筛选中同样按普通字符处理。"""
        learner_id = add_learner(self.conn, '100%_ann"\'')
        enroll_learner(self.conn, learner_id, 1)
        special = [{"learner_id": learner_id, "name": '100%_ann"\''}]

        self.assertEqual(list_course_learners(self.conn, 1, "%"), special)
        self.assertEqual(list_course_learners(self.conn, 1, "_"), special)
        self.assertEqual(list_course_learners(self.conn, 1, "%_"), special)
        self.assertEqual(list_course_learners(self.conn, 1, "%ann"), [])
        self.assertEqual(list_course_learners(self.conn, 1, '"'), special)
        self.assertEqual(list_course_learners(self.conn, 1, "'"), special)

    def test_matching_uses_current_name_after_rename(self):
        """改名后使用新姓名匹配，旧姓名不再命中。"""
        self.assertEqual(
            rename_learner(self.conn, 2, "Ann"),
            {"learner_id": 2, "name": "Ann"},
        )
        self.assertEqual(
            list_course_learners(self.conn, 1, "Ann"),
            [
                {"learner_id": 1, "name": "Ann"},
                {"learner_id": 2, "name": "Ann"},
            ],
        )
        self.assertEqual(list_course_learners(self.conn, 1, "ann"), [])

    def test_no_match_and_course_without_enrollments_return_empty(self):
        """无匹配与无人报名的课程均返回空列表。"""
        self.assertEqual(list_course_learners(self.conn, 1, "zzz"), [])
        self.assertEqual(list_course_learners(self.conn, 2, "zzz"), [])

        add_course(self.conn, "课程三", ["导学"])
        self.assertEqual(list_course_learners(self.conn, 3), [])
        self.assertEqual(list_course_learners(self.conn, 3, None), [])
        self.assertEqual(list_course_learners(self.conn, 3, "Ann"), [])

    def test_unknown_course_returns_none_before_filter_validation(self):
        """未知正整数编号与超范围编号返回 None；课程不存在时即使筛选词
        为空也返回 None，而不是抛出 ValidationError。"""
        self.assertIsNone(list_course_learners(self.conn, 99))
        self.assertIsNone(list_course_learners(self.conn, 99, ""))
        self.assertIsNone(list_course_learners(self.conn, 99, "   "))
        self.assertIsNone(
            list_course_learners(self.conn, SQLITE_INT64_MAX + 1)
        )
        self.assertIsNone(
            list_course_learners(self.conn, SQLITE_INT64_MAX + 1, "")
        )

    def test_empty_or_whitespace_filter_raises_validation_error(self):
        """课程存在但筛选词为空字符串或仅含空白时抛出 ValidationError，
        消息与命令行提示相同。"""
        for course_id in (1, 2):
            for bad in ("", "   ", "\t\n  "):
                with self.subTest(course_id=course_id, name_contains=bad):
                    with self.assertRaises(ValidationError) as ctx:
                        list_course_learners(self.conn, course_id, bad)
                    self.assertEqual(
                        str(ctx.exception), ERR_EMPTY_LEARNER_NAME_FILTER
                    )

    def test_filters_are_read_only_and_survive_reopen(self):
        """成功与失败筛选不改变业务数据；关闭后重新打开同一文件结果一致。"""
        before = dump_business_data(self.db_file)
        expected = list_course_learners(self.conn, 1, " Ann ")

        list_course_learners(self.conn, 1)
        list_course_learners(self.conn, 1, "ann")
        list_course_learners(self.conn, 1, "无匹配")
        list_course_learners(self.conn, 2, "Ann")
        list_course_learners(self.conn, 3)
        for bad in ("", "  "):
            with self.assertRaises(ValidationError):
                list_course_learners(self.conn, 1, bad)
        self.assertIsNone(list_course_learners(self.conn, 99))
        self.assertIsNone(list_course_learners(self.conn, 99, ""))
        self.assertIsNone(
            list_course_learners(self.conn, SQLITE_INT64_MAX + 1)
        )
        self.assertEqual(dump_business_data(self.db_file), before)

        self.conn.close()
        self.conn = connect(str(self.db_file))
        self.assertEqual(
            list_course_learners(self.conn, 1, " Ann "), expected
        )
        self.assertEqual(
            list_course_learners(self.conn, 1, "ann"),
            [{"learner_id": 2, "name": "ann"}],
        )
        self.assertEqual(dump_business_data(self.db_file), before)


if __name__ == "__main__":
    unittest.main()
