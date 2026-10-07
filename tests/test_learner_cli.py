"""学员登记与按编号查询的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
成功登记与跨进程读取、同名学员分别登记、编号与课程编号独立、
各类登记与查询失败。每个用例使用独立的临时数据库，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SQLITE_INT64_MAX = 2**63 - 1


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


class LearnerTestCase(unittest.TestCase):
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

    def add_learner(self, name, db_first=True):
        return run_cli(
            self.db_path, "add-learner", "--name", name, db_first=db_first
        )


class TestAddThenGet(LearnerTestCase):
    def test_strips_surrounding_whitespace_and_persists(self):
        added = self.add_learner(" 学员甲 ")
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

        got = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_db_option_after_subcommand(self):
        added = self.add_learner("学员甲", db_first=False)
        self.assertEqual(added.returncode, 0)
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})

        got = run_cli(self.db_path, "get-learner", "1", db_first=False)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_internal_whitespace_and_case_preserved(self):
        added = self.add_learner("  Zhang  San ")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})
        got = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(got.stdout), {"learner_id": 1, "name": "Zhang  San"}
        )

    def test_same_name_registers_separately(self):
        first = self.add_learner("学员甲")
        second = self.add_learner("学员甲")
        self.assertEqual(json.loads(first.stdout), {"learner_id": 1})
        self.assertEqual(json.loads(second.stdout), {"learner_id": 2})
        for learner_id in (1, 2):
            got = run_cli(self.db_path, "get-learner", str(learner_id))
            self.assertEqual(
                json.loads(got.stdout),
                {"learner_id": learner_id, "name": "学员甲"},
            )

    def test_learner_ids_independent_of_course_ids(self):
        course = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "入门培训",
            "--chapter",
            "准备",
        )
        self.assertEqual(json.loads(course.stdout), {"course_id": 1})

        learner = self.add_learner("学员甲")
        self.assertEqual(json.loads(learner.stdout), {"learner_id": 1})

        second_learner = self.add_learner("学员乙")
        self.assertEqual(json.loads(second_learner.stdout), {"learner_id": 2})

        # 课程详情接口与数据保持原样
        got_course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(got_course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )

    def test_learners_isolated_between_db_files(self):
        self.add_learner("学员甲")
        other_db = Path(self._tmp.name) / "other.db"
        missing = run_cli(other_db, "get-learner", "1")
        self.assert_failure(missing, 1, "学员不存在")

        added = run_cli(other_db, "add-learner", "--name", "学员甲")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})


class TestAddValidationFailures(LearnerTestCase):
    def test_missing_name(self):
        result = run_cli(self.db_path, "add-learner")
        self.assert_failure(result, 1, "学员姓名不能为空")

    def test_blank_name(self):
        result = self.add_learner("   ")
        self.assert_failure(result, 1, "学员姓名不能为空")

    def test_failure_leaves_no_records(self):
        result = run_cli(self.db_path, "add-learner")
        self.assertEqual(result.returncode, 1)
        # 失败不占用编号：随后登记的首位学员仍是 1
        added = self.add_learner("学员甲")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 1})


class TestGetFailures(LearnerTestCase):
    def test_zero_id(self):
        self.assert_failure(
            run_cli(self.db_path, "get-learner", "0"),
            2,
            "学员编号必须为正整数",
        )

    def test_negative_id(self):
        self.assert_failure(
            run_cli(self.db_path, "get-learner", "-3"),
            2,
            "学员编号必须为正整数",
        )

    def test_non_numeric_id(self):
        self.assert_failure(
            run_cli(self.db_path, "get-learner", "abc"),
            2,
            "学员编号必须为正整数",
        )

    def test_unknown_positive_id(self):
        self.assert_failure(
            run_cli(self.db_path, "get-learner", "42"), 1, "学员不存在"
        )

    def test_overflow_id_treated_as_unknown(self):
        for raw in [
            str(SQLITE_INT64_MAX + 1),
            " +9223372036854775808 ",
            "009223372036854775808",
            "9" * 26,
        ]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    run_cli(self.db_path, "get-learner", raw),
                    1,
                    "学员不存在",
                )

    def test_int64_max_id_is_an_ordinary_unknown_id(self):
        self.assert_failure(
            run_cli(self.db_path, "get-learner", str(SQLITE_INT64_MAX)),
            1,
            "学员不存在",
        )

    def test_affixes_follow_existing_id_semantics(self):
        self.add_learner("学员甲")
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(learner_id=raw):
                result = run_cli(self.db_path, "get-learner", raw)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(
                    json.loads(result.stdout),
                    {"learner_id": 1, "name": "学员甲"},
                )


if __name__ == "__main__":
    unittest.main()
