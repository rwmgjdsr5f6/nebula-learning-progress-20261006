"""课程报名与报名名册查询的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
报名成功与跨进程读取、重复报名幂等、多对多关系、名册排序与改名后
显示新姓名、空名册、各类编号与存在性失败的顺序及退出码、数据库文件
相互隔离。每个用例使用独立的临时数据库，结束后自动清理。
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


class EnrollTestCase(unittest.TestCase):
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

    def add_course(self, title="入门培训"):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            title,
            "--chapter",
            "准备",
        )
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name="学员甲"):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id, db_first=True):
        return run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
            db_first=db_first,
        )

    def list_course_learners(self, course_id, db_first=True):
        return run_cli(
            self.db_path,
            "list-course-learners",
            str(course_id),
            db_first=db_first,
        )


class TestEnrollThenList(EnrollTestCase):
    def test_fixed_sample_flow(self):
        self.assertEqual(self.add_course("入门培训"), 1)
        self.assertEqual(self.add_learner("学员甲"), 1)

        enrolled = self.enroll(1, 1)
        self.assertEqual(enrolled.returncode, 0)
        self.assertEqual(enrolled.stderr, "")
        self.assertEqual(
            json.loads(enrolled.stdout), {"learner_id": 1, "course_id": 1}
        )

        roster = self.list_course_learners(1)
        self.assertEqual(roster.returncode, 0)
        self.assertEqual(roster.stderr, "")
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_db_option_after_subcommand(self):
        self.add_course()
        self.add_learner()
        enrolled = self.enroll(1, 1, db_first=False)
        self.assertEqual(enrolled.returncode, 0)
        self.assertEqual(
            json.loads(enrolled.stdout), {"learner_id": 1, "course_id": 1}
        )
        roster = self.list_course_learners(1, db_first=False)
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_duplicate_enroll_is_idempotent(self):
        self.add_course()
        self.add_learner()
        first = self.enroll(1, 1)
        second = self.enroll(1, 1)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(second.returncode, 0)
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout)["learners"],
            [{"learner_id": 1, "name": "学员甲"}],
        )

    def test_many_to_many_and_roster_sorted_by_learner_id(self):
        self.assertEqual(self.add_course("入门培训"), 1)
        self.assertEqual(self.add_course("进阶培训"), 2)
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员乙"), 2)
        self.assertEqual(self.add_learner("学员丙"), 3)

        # 一门课程接收多个学员（报名顺序与编号顺序相反）
        self.assertEqual(self.enroll(3, 1).returncode, 0)
        self.assertEqual(self.enroll(1, 1).returncode, 0)
        self.assertEqual(self.enroll(2, 1).returncode, 0)
        # 一个学员报名多门课程
        self.assertEqual(self.enroll(1, 2).returncode, 0)

        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout),
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员乙"},
                    {"learner_id": 3, "name": "学员丙"},
                ],
            },
        )
        other = self.list_course_learners(2)
        self.assertEqual(
            json.loads(other.stdout),
            {"course_id": 2, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_same_names_distinguished_by_id(self):
        self.assertEqual(self.add_course("入门培训"), 1)
        self.assertEqual(self.add_course("入门培训"), 2)
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员甲"), 2)

        self.assertEqual(self.enroll(2, 2).returncode, 0)
        roster = self.list_course_learners(2)
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 2, "learners": [{"learner_id": 2, "name": "学员甲"}]},
        )
        empty = self.list_course_learners(1)
        self.assertEqual(json.loads(empty.stdout), {"course_id": 1, "learners": []})

    def test_empty_roster_for_course_without_enrollments(self):
        self.add_course()
        roster = self.list_course_learners(1)
        self.assertEqual(roster.returncode, 0)
        self.assertEqual(roster.stderr, "")
        self.assertEqual(
            json.loads(roster.stdout), {"course_id": 1, "learners": []}
        )

    def test_roster_uses_current_name_after_rename(self):
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)
        renamed = run_cli(self.db_path, "rename-learner", "1", "--name", "学员丁")
        self.assertEqual(renamed.returncode, 0)
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员丁"}]},
        )

    def test_enroll_does_not_change_course_or_learner_records(self):
        self.add_course("入门培训")
        self.add_learner("学员甲")
        self.enroll(1, 1)

        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )
        learner = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(learner.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def test_id_affixes_follow_existing_semantics(self):
        self.add_course()
        self.add_learner()
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(learner_id=raw):
                enrolled = run_cli(
                    self.db_path, "enroll-learner", raw, "--course", " 01 "
                )
                self.assertEqual(enrolled.returncode, 0)
                self.assertEqual(
                    json.loads(enrolled.stdout),
                    {"learner_id": 1, "course_id": 1},
                )
        roster = run_cli(self.db_path, "list-course-learners", "+01")
        self.assertEqual(roster.returncode, 0)
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_enrollments_isolated_between_db_files(self):
        self.add_course()
        self.add_learner()
        self.enroll(1, 1)

        other_db = Path(self._tmp.name) / "other.db"
        run_cli(other_db, "add-course", "--title", "入门培训", "--chapter", "准备")
        run_cli(other_db, "add-learner", "--name", "学员甲")
        roster = run_cli(other_db, "list-course-learners", "1")
        self.assertEqual(
            json.loads(roster.stdout), {"course_id": 1, "learners": []}
        )


class TestEnrollFailures(EnrollTestCase):
    def test_missing_arguments_use_argparse_error(self):
        self.add_course()
        self.add_learner()
        missing_course = run_cli(self.db_path, "enroll-learner", "1")
        self.assertEqual(missing_course.returncode, 2)
        self.assertEqual(missing_course.stdout, "")
        missing_learner = run_cli(self.db_path, "enroll-learner", "--course", "1")
        self.assertEqual(missing_learner.returncode, 2)
        self.assertEqual(missing_learner.stdout, "")

    def test_bad_learner_id_format_reported_first(self):
        self.add_course()
        self.add_learner()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    self.enroll(raw, 1), 2, "学员编号必须为正整数"
                )

    def test_bad_course_id_format_reported_after_learner_format(self):
        self.add_course()
        self.add_learner()
        for raw in ["0", "-2", "xyz", ""]:
            with self.subTest(course_id=raw):
                self.assert_failure(
                    self.enroll(1, raw), 2, "课程编号必须为正整数"
                )
        # 两个编号都非法时只报学员编号格式错误
        self.assert_failure(
            self.enroll("abc", "xyz"), 2, "学员编号必须为正整数"
        )

    def test_unknown_learner_reported_before_unknown_course(self):
        self.add_course()
        self.add_learner()
        self.assert_failure(self.enroll(2, 1), 1, "学员不存在")
        # 学员与课程都不存在时只报学员不存在
        self.assert_failure(self.enroll(2, 2), 1, "学员不存在")

    def test_unknown_course(self):
        self.add_course()
        self.add_learner()
        self.assert_failure(self.enroll(1, 2), 1, "课程不存在")

    def test_overflow_ids_treated_as_unknown(self):
        self.add_course()
        self.add_learner()
        overflow = str(SQLITE_INT64_MAX + 1)
        self.assert_failure(self.enroll(overflow, 1), 1, "学员不存在")
        self.assert_failure(self.enroll(1, overflow), 1, "课程不存在")
        self.assert_failure(
            self.enroll(1, str(SQLITE_INT64_MAX)), 1, "课程不存在"
        )

    def test_failure_leaves_no_enrollment(self):
        self.add_course()
        self.add_learner()
        self.enroll(2, 1)
        self.enroll(1, 2)
        self.enroll("abc", 1)
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout), {"course_id": 1, "learners": []}
        )
        # 失败不影响后续成功报名
        self.assertEqual(self.enroll(1, 1).returncode, 0)
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout)["learners"],
            [{"learner_id": 1, "name": "学员甲"}],
        )


class TestListCourseLearnersFailures(EnrollTestCase):
    def test_bad_course_id_format(self):
        self.add_course()
        for raw in ["0", "-1", "abc", ""]:
            with self.subTest(course_id=raw):
                self.assert_failure(
                    self.list_course_learners(raw), 2, "课程编号必须为正整数"
                )

    def test_unknown_course(self):
        self.add_course()
        self.assert_failure(self.list_course_learners(2), 1, "课程不存在")

    def test_overflow_id_treated_as_unknown(self):
        self.add_course()
        self.assert_failure(
            self.list_course_learners(str(SQLITE_INT64_MAX + 1)),
            1,
            "课程不存在",
        )

    def test_missing_argument_uses_argparse_error(self):
        result = run_cli(self.db_path, "list-course-learners")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
