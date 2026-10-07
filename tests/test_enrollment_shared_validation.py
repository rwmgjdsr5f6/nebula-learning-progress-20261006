"""报名/取消报名共用校验流程的回归测试。

针对 enroll-learner 与 unenroll-learner 共用编号与存在性校验的重构，
通过子进程调用 ``python -m course_progress`` 验证公开行为不变：
固定样例（课程1“入门培训”/章节“准备”，学员1“学员甲”、学员2“学员乙”，
两人均已报名）下的取消、重开库持久化与再报名恢复；重复报名/重复取消/
取消未报名组合的幂等性；两种命令一致的错误优先级——学员编号格式、
课程编号格式、学员存在性、课程存在性，只报告首个错误（特别地，未知
学员搭配非法课程编号时先报课程编号格式错误）；失败时标准输出为空、
无异常堆栈、业务记录不变。每个用例使用独立的临时数据库，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 超出 SQLite 64 位整数范围的未知编号样例
OVERFLOW_ID = str(2**63)

ENROLL_COMMANDS = ["enroll-learner", "unenroll-learner"]


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


class EnrollmentTestCase(unittest.TestCase):
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

    def run_enrollment_command(self, command, learner_id, course_id, db_first=True):
        return run_cli(
            self.db_path,
            command,
            str(learner_id),
            "--course",
            str(course_id),
            db_first=db_first,
        )

    def enroll(self, learner_id, course_id, db_first=True):
        return self.run_enrollment_command(
            "enroll-learner", learner_id, course_id, db_first=db_first
        )

    def unenroll(self, learner_id, course_id, db_first=True):
        return self.run_enrollment_command(
            "unenroll-learner", learner_id, course_id, db_first=db_first
        )

    def list_course_learners(self, course_id):
        return run_cli(self.db_path, "list-course-learners", str(course_id))

    def list_learner_courses(self, learner_id):
        return run_cli(self.db_path, "list-learner-courses", str(learner_id))

    def setup_fixed_sample(self):
        """固定样例：课程1“入门培训”/章节“准备”，学员1“学员甲”、
        学员2“学员乙”，两人均已报名课程1。"""
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "入门培训",
            "--chapter",
            "准备",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["course_id"], 1)
        for learner_id, name in [(1, "学员甲"), (2, "学员乙")]:
            result = run_cli(self.db_path, "add-learner", "--name", name)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["learner_id"], learner_id)
        for learner_id in (1, 2):
            self.assertEqual(self.enroll(learner_id, 1).returncode, 0)


class TestFixedSampleFlow(EnrollmentTestCase):
    def test_unenroll_persist_and_reenroll(self):
        self.setup_fixed_sample()

        unenrolled = self.unenroll(1, 1)
        self.assertEqual(unenrolled.returncode, 0)
        self.assertEqual(unenrolled.stderr, "")
        self.assertEqual(
            json.loads(unenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )

        # 每条命令都是独立进程，再次查询即重新打开同一数据库
        roster = self.list_course_learners(1)
        self.assertEqual(roster.returncode, 0)
        self.assertEqual(roster.stderr, "")
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 2, "name": "学员乙"}]},
        )
        courses = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(courses.stdout), {"learner_id": 1, "courses": []}
        )

        # 课程详情、学员记录及其他报名关系不变
        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )
        for learner_id, name in [(1, "学员甲"), (2, "学员乙")]:
            learner = run_cli(self.db_path, "get-learner", str(learner_id))
            self.assertEqual(
                json.loads(learner.stdout),
                {"learner_id": learner_id, "name": name},
            )
        learner2_courses = self.list_learner_courses(2)
        self.assertEqual(
            [c["course_id"] for c in json.loads(learner2_courses.stdout)["courses"]],
            [1],
        )

        # 再报名学员1，名册恢复
        reenrolled = self.enroll(1, 1)
        self.assertEqual(reenrolled.returncode, 0)
        self.assertEqual(reenrolled.stderr, "")
        self.assertEqual(
            json.loads(reenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout),
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员乙"},
                ],
            },
        )

    def test_db_option_after_subcommand(self):
        self.setup_fixed_sample()
        unenrolled = self.unenroll(1, 1, db_first=False)
        self.assertEqual(unenrolled.returncode, 0)
        self.assertEqual(
            json.loads(unenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )
        reenrolled = self.enroll(1, 1, db_first=False)
        self.assertEqual(reenrolled.returncode, 0)
        self.assertEqual(
            json.loads(reenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )


class TestIdempotency(EnrollmentTestCase):
    def test_duplicate_enroll_keeps_single_record(self):
        self.setup_fixed_sample()
        first = self.enroll(1, 1)
        second = self.enroll(1, 1)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        roster = self.list_course_learners(1)
        learners = json.loads(roster.stdout)["learners"]
        self.assertEqual(
            learners,
            [
                {"learner_id": 1, "name": "学员甲"},
                {"learner_id": 2, "name": "学员乙"},
            ],
        )

    def test_repeat_unenroll_is_idempotent(self):
        self.setup_fixed_sample()
        first = self.unenroll(1, 1)
        second = self.unenroll(1, 1)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(second.stderr, "")
        self.assertEqual(
            json.loads(second.stdout), {"learner_id": 1, "course_id": 1}
        )
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 2, "name": "学员乙"}]},
        )

    def test_unenroll_never_enrolled_valid_pair_succeeds(self):
        self.setup_fixed_sample()
        # 学员1从未报名课程2（先登记课程2），取消该组合仍成功
        run_cli(self.db_path, "add-course", "--title", "进阶培训", "--chapter", "准备")
        result = self.unenroll(1, 2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "course_id": 2}
        )
        # 原有报名关系不受影响
        roster = self.list_course_learners(1)
        self.assertEqual(len(json.loads(roster.stdout)["learners"]), 2)


class TestSharedErrorPriority(EnrollmentTestCase):
    """两种命令走同一套校验流程，错误顺序与内容完全一致。"""

    def test_bad_learner_id_format_reported_first(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            for raw in ["0", "-3", "abc", ""]:
                with self.subTest(command=command, learner_id=raw):
                    self.assert_failure(
                        self.run_enrollment_command(command, raw, 1),
                        2,
                        "学员编号必须为正整数",
                    )

    def test_bad_course_id_format_reported_after_learner_format(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            for raw in ["0", "-2", "xyz", ""]:
                with self.subTest(command=command, course_id=raw):
                    self.assert_failure(
                        self.run_enrollment_command(command, 1, raw),
                        2,
                        "课程编号必须为正整数",
                    )
            # 两个编号都非法时只报学员编号格式错误
            with self.subTest(command=command, case="both_bad"):
                self.assert_failure(
                    self.run_enrollment_command(command, "abc", "xyz"),
                    2,
                    "学员编号必须为正整数",
                )

    def test_unknown_learner_with_bad_course_id_reports_course_format(self):
        """未知学员搭配非法课程编号时，两种命令均先报课程编号错误。"""
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_enrollment_command(command, 99, "abc"),
                    2,
                    "课程编号必须为正整数",
                )

    def test_unknown_learner_reported_before_unknown_course(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_enrollment_command(command, 3, 1), 1, "学员不存在"
                )
                # 学员与课程都不存在时只报学员不存在
                self.assert_failure(
                    self.run_enrollment_command(command, 3, 99), 1, "学员不存在"
                )

    def test_unknown_course(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_enrollment_command(command, 1, 99), 1, "课程不存在"
                )

    def test_overflow_ids_treated_as_unknown(self):
        self.setup_fixed_sample()
        self.assertEqual(OVERFLOW_ID, "9223372036854775808")
        for command in ENROLL_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_enrollment_command(command, OVERFLOW_ID, 1),
                    1,
                    "学员不存在",
                )
                self.assert_failure(
                    self.run_enrollment_command(command, 1, OVERFLOW_ID),
                    1,
                    "课程不存在",
                )

    def test_id_affixes_accepted(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            for raw in ["+1", "01", " 1 ", "\t1\n"]:
                with self.subTest(command=command, learner_id=raw):
                    result = self.run_enrollment_command(command, raw, " 01 ")
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(
                        json.loads(result.stdout),
                        {"learner_id": 1, "course_id": 1},
                    )

    def test_missing_arguments_use_argparse_error(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            with self.subTest(command=command):
                missing_course = run_cli(self.db_path, command, "1")
                self.assertEqual(missing_course.returncode, 2)
                self.assertEqual(missing_course.stdout, "")
                missing_learner = run_cli(self.db_path, command, "--course", "1")
                self.assertEqual(missing_learner.returncode, 2)
                self.assertEqual(missing_learner.stdout, "")

    def test_failure_leaves_records_untouched(self):
        self.setup_fixed_sample()
        for command in ENROLL_COMMANDS:
            self.run_enrollment_command(command, 3, 1)
            self.run_enrollment_command(command, 1, 99)
            self.run_enrollment_command(command, "abc", 1)
            self.run_enrollment_command(command, 1, "xyz")
        roster = self.list_course_learners(1)
        self.assertEqual(
            json.loads(roster.stdout),
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员乙"},
                ],
            },
        )
        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )


if __name__ == "__main__":
    unittest.main()
