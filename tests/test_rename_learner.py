"""按编号修改学员姓名的回归测试。

通过子进程调用 ``python -m course_progress``，验证 rename-learner 的
公开 CLI 行为：成功改名并跨进程读取、--db 位置、姓名去首尾空白保留
内部空白、改为原名与同名学员、编号解析与错误优先级、失败不改动数据。
每个用例使用独立的临时数据库，结束后自动清理。
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


class RenameLearnerTestCase(unittest.TestCase):
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

    def add_learner(self, name):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learner_id"]

    def get_learner(self, learner_id):
        result = run_cli(self.db_path, "get-learner", str(learner_id))
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)


class TestRenameSuccess(RenameLearnerTestCase):
    def test_rename_and_read_back(self):
        self.add_learner("学员甲")
        result = run_cli(
            self.db_path, "rename-learner", "1", "--name", " Zhang  San "
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"learner_id": 1, "name": "Zhang  San"},
        )
        # 独立进程读取，内部两个空格保留
        self.assertEqual(
            self.get_learner(1), {"learner_id": 1, "name": "Zhang  San"}
        )

    def test_db_option_after_subcommand(self):
        self.add_learner("学员甲")
        result = run_cli(
            self.db_path,
            "rename-learner",
            "1",
            "--name",
            "学员乙",
            db_first=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "name": "学员乙"}
        )

    def test_rename_to_same_name_succeeds(self):
        self.add_learner("学员甲")
        result = run_cli(
            self.db_path, "rename-learner", "1", "--name", "学员甲"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_duplicate_name_allowed_without_merging(self):
        self.add_learner("学员甲")
        self.add_learner("学员乙")
        result = run_cli(
            self.db_path, "rename-learner", "2", "--name", "学员甲"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 2, "name": "学员甲"}
        )
        # 两条记录各自独立存在
        for learner_id in (1, 2):
            self.assertEqual(
                self.get_learner(learner_id),
                {"learner_id": learner_id, "name": "学员甲"},
            )

    def test_other_records_and_next_id_untouched(self):
        self.add_learner("学员甲")
        self.add_learner("Zhang  San")
        course = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "入门培训",
            "--chapter",
            "准备",
            "--chapter",
            "实践",
        )
        self.assertEqual(json.loads(course.stdout), {"course_id": 1})

        run_cli(self.db_path, "rename-learner", "1", "--name", "学员丙")

        self.assertEqual(
            self.get_learner(2), {"learner_id": 2, "name": "Zhang  San"}
        )
        got_course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(got_course.stdout),
            {
                "course_id": 1,
                "title": "入门培训",
                "chapters": ["准备", "实践"],
            },
        )
        added = run_cli(self.db_path, "add-learner", "--name", "学员丁")
        self.assertEqual(json.loads(added.stdout), {"learner_id": 3})

    def test_affixes_follow_existing_id_semantics(self):
        self.add_learner("学员甲")
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(learner_id=raw):
                result = run_cli(
                    self.db_path, "rename-learner", raw, "--name", "学员乙"
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(
                    json.loads(result.stdout),
                    {"learner_id": 1, "name": "学员乙"},
                )


class TestRenameFailures(RenameLearnerTestCase):
    def test_bad_ids(self):
        self.add_learner("学员甲")
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    run_cli(
                        self.db_path, "rename-learner", raw, "--name", "学员乙"
                    ),
                    2,
                    "学员编号必须为正整数",
                )
        # 失败不改动已保存记录
        self.assertEqual(
            self.get_learner(1), {"learner_id": 1, "name": "学员甲"}
        )

    def test_unknown_positive_id(self):
        self.assert_failure(
            run_cli(self.db_path, "rename-learner", "42", "--name", "学员乙"),
            1,
            "学员不存在",
        )

    def test_overflow_id_treated_as_unknown(self):
        self.add_learner("学员甲")
        for raw in [
            str(SQLITE_INT64_MAX + 1),
            " +9223372036854775808 ",
            "009223372036854775808",
            "9" * 26,
        ]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    run_cli(
                        self.db_path, "rename-learner", raw, "--name", "学员乙"
                    ),
                    1,
                    "学员不存在",
                )

    def test_missing_name(self):
        self.add_learner("学员甲")
        self.assert_failure(
            run_cli(self.db_path, "rename-learner", "1"),
            1,
            "学员姓名不能为空",
        )

    def test_blank_name(self):
        self.add_learner("学员甲")
        for name in ["", "   ", "\t\n"]:
            with self.subTest(name=name):
                self.assert_failure(
                    run_cli(
                        self.db_path, "rename-learner", "1", "--name", name
                    ),
                    1,
                    "学员姓名不能为空",
                )
        # 失败不改动已保存记录
        self.assertEqual(
            self.get_learner(1), {"learner_id": 1, "name": "学员甲"}
        )

    def test_error_priority_id_before_existence_before_name(self):
        self.add_learner("学员甲")
        # 编号非法优先于学员不存在与姓名为空
        self.assert_failure(
            run_cli(self.db_path, "rename-learner", "0"),
            2,
            "学员编号必须为正整数",
        )
        # 学员不存在优先于姓名为空
        self.assert_failure(
            run_cli(self.db_path, "rename-learner", "42"),
            1,
            "学员不存在",
        )


if __name__ == "__main__":
    unittest.main()
