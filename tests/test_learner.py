"""学员登记与按编号查询的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
登记成功与跨进程读取、同名学员分别编号、各类登记与查询失败、
失败不产生记录、学员编号独立于课程编号。
每个用例使用独立的临时数据库，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        self.assertNotIn("Traceback", result.stderr)


class TestAddThenGetLearner(CliTestCase):
    def test_add_then_get_across_processes(self):
        added = run_cli(self.db_path, "add-learner", "--name", " 学员甲 ")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

        # 另一次独立调用读取同一数据库，证明姓名已持久保存且首尾空白已去除
        got = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_db_option_after_subcommand(self):
        added = run_cli(
            self.db_path, "add-learner", "--name", "学员甲", db_first=False
        )
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

        got = run_cli(self.db_path, "get-learner", "1", db_first=False)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_same_name_registered_separately(self):
        first = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(json.loads(first.stdout), {"learner_id": 1})
        second = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stderr, "")
        self.assertEqual(json.loads(second.stdout), {"learner_id": 2})

        got = run_cli(self.db_path, "get-learner", "2")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 2, "name": "学员甲"}
        )

    def test_inner_whitespace_and_case_preserved(self):
        added = run_cli(self.db_path, "add-learner", "--name", " Alice  B ")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})
        got = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "Alice  B"}
        )

    def test_learner_ids_independent_of_course_ids(self):
        added_course = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "Python 入门",
            "--chapter",
            "安装环境",
        )
        self.assertEqual(json.loads(added_course.stdout), {"course_id": 1})

        added_learner = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(json.loads(added_learner.stdout), {"learner_id": 1})

        # 课程详情保持原样，不受学员登记影响
        got_course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(got_course.returncode, 0)
        self.assertEqual(got_course.stderr, "")
        self.assertEqual(
            json.loads(got_course.stdout),
            {
                "course_id": 1,
                "title": "Python 入门",
                "chapters": ["安装环境"],
            },
        )

    def test_learners_isolated_between_db_files(self):
        other_db = Path(self._tmp.name) / "other.db"
        added = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

        missing = run_cli(other_db, "get-learner", "1")
        self.assert_failure(missing, 1, "学员不存在")

        added_other = run_cli(other_db, "add-learner", "--name", "学员乙")
        self.assertEqual(json.loads(added_other.stdout), {"learner_id": 1})
        got = run_cli(other_db, "get-learner", "1")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员乙"}
        )


class TestAddLearnerValidationFailures(CliTestCase):
    def assert_no_learner_added(self):
        """确认数据库中仍无学员：编号 1 查询不到，重新登记仍得编号 1。"""
        got = run_cli(self.db_path, "get-learner", "1")
        self.assert_failure(got, 1, "学员不存在")

        added = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

    def test_missing_name_option(self):
        result = run_cli(self.db_path, "add-learner")
        self.assert_failure(result, 1, "学员姓名不能为空")
        self.assert_no_learner_added()

    def test_blank_name(self):
        result = run_cli(self.db_path, "add-learner", "--name", "   ")
        self.assert_failure(result, 1, "学员姓名不能为空")
        self.assert_no_learner_added()

    def test_empty_name(self):
        result = run_cli(self.db_path, "add-learner", "--name", "")
        self.assert_failure(result, 1, "学员姓名不能为空")
        self.assert_no_learner_added()


class TestGetLearnerFailures(CliTestCase):
    def setUp(self):
        super().setUp()
        added = run_cli(self.db_path, "add-learner", "--name", "学员甲")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

    def assert_learner_intact(self):
        """失败查询不新增或改变任何记录：学员 1 仍在，新学员编号为 2。"""
        got = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员甲"}
        )

        added = run_cli(self.db_path, "add-learner", "--name", "学员乙")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 2})

    def test_unknown_positive_id(self):
        result = run_cli(self.db_path, "get-learner", "42")
        self.assert_failure(result, 1, "学员不存在")
        self.assert_learner_intact()

    def test_zero_id(self):
        result = run_cli(self.db_path, "get-learner", "0")
        self.assert_failure(result, 2, "学员编号必须为正整数")
        self.assert_learner_intact()

    def test_negative_id(self):
        result = run_cli(self.db_path, "get-learner", "-3")
        self.assert_failure(result, 2, "学员编号必须为正整数")
        self.assert_learner_intact()

    def test_non_numeric_id(self):
        result = run_cli(self.db_path, "get-learner", "abc")
        self.assert_failure(result, 2, "学员编号必须为正整数")
        self.assert_learner_intact()

    def test_overflow_id_treated_as_unknown_learner(self):
        result = run_cli(self.db_path, "get-learner", "9223372036854775808")
        self.assert_failure(result, 1, "学员不存在")
        self.assert_learner_intact()

    def test_id_with_sign_leading_zeros_and_whitespace(self):
        for raw in ["+1", "01", " 1 "]:
            with self.subTest(learner_id=raw):
                got = run_cli(self.db_path, "get-learner", raw)
                self.assertEqual(got.returncode, 0)
                self.assertEqual(got.stderr, "")
                self.assertEqual(
                    json.loads(got.stdout),
                    {"learner_id": 1, "name": "学员甲"},
                )


class TestCoreGetLearnerOverflow(CliTestCase):
    """core.get_learner 收到超界的 Python 正整数时返回 None。"""

    def test_overflow_int_returns_none(self):
        from course_progress.core import add_learner, connect, get_learner

        conn = connect(str(self.db_path))
        with conn:
            learner_id = add_learner(conn, "学员甲")
            self.assertEqual(get_learner(conn, learner_id)["name"], "学员甲")
            self.assertIsNone(get_learner(conn, 2**63))
            self.assertIsNone(get_learner(conn, 2**63 - 1))
            self.assertIsNone(get_learner(conn, learner_id + 1))
        conn.close()


if __name__ == "__main__":
    unittest.main()
