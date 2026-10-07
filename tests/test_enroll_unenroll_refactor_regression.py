"""报名与取消报名共用校验流程的重构回归测试。

针对 enroll-learner 与 unenroll-learner 共用同一套编号解析与存在性
校验流程的重构，通过子进程调用 ``python -m course_progress`` 验证：
固定样例下的取消与再报名闭环、重复操作幂等、跨进程持久化、两种命令
一致的错误优先级（学员编号格式 → 课程编号格式 → 学员存在性 → 课程
存在性）、失败不改动业务记录、--db 位置与数据库先于编号校验打开。
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
OVERFLOW_ID = str(SQLITE_INT64_MAX + 1)  # 9223372036854775808

ENROLL = "enroll-learner"
UNENROLL = "unenroll-learner"
BOTH_COMMANDS = (ENROLL, UNENROLL)


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


class EnrollmentRefactorTestCase(unittest.TestCase):
    """固定样例：课程1“入门培训”（章节“准备”）、学员1“学员甲”、
    学员2“学员乙”，两人均已报名课程1。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.assertEqual(
            run_cli(
                self.db_path,
                "add-course",
                "--title",
                "入门培训",
                "--chapter",
                "准备",
            ).returncode,
            0,
        )
        self.assertEqual(
            run_cli(self.db_path, "add-learner", "--name", "学员甲").returncode,
            0,
        )
        self.assertEqual(
            run_cli(self.db_path, "add-learner", "--name", "学员乙").returncode,
            0,
        )
        self.assertEqual(self.run_command(ENROLL, 1, 1).returncode, 0)
        self.assertEqual(self.run_command(ENROLL, 2, 1).returncode, 0)

    def run_command(self, command, learner_id, course_id, db_first=True):
        return run_cli(
            self.db_path,
            command,
            str(learner_id),
            "--course",
            str(course_id),
            db_first=db_first,
        )

    def roster(self):
        result = run_cli(self.db_path, "list-course-learners", "1")
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learners"]

    def learner_courses(self, learner_id):
        result = run_cli(self.db_path, "list-learner-courses", str(learner_id))
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["courses"]

    def assert_failure(self, result, exit_code, message):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def assert_sample_untouched(self):
        """固定样例的业务记录保持初始状态。"""
        self.assertEqual(
            self.roster(),
            [
                {"learner_id": 1, "name": "学员甲"},
                {"learner_id": 2, "name": "学员乙"},
            ],
        )
        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )
        learner = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(learner.stdout), {"learner_id": 1, "name": "学员甲"}
        )


class TestSharedSuccessFlow(EnrollmentRefactorTestCase):
    def test_unenroll_then_reenroll_roundtrip(self):
        unenrolled = self.run_command(UNENROLL, 1, 1)
        self.assertEqual(unenrolled.returncode, 0)
        self.assertEqual(unenrolled.stderr, "")
        # 标准输出为一行仅含整数 learner_id 与 course_id 的 JSON
        self.assertEqual(
            unenrolled.stdout, '{"learner_id": 1, "course_id": 1}\n'
        )

        # 每条命令都是独立进程，再次查询即重新打开同一数据库
        self.assertEqual(self.roster(), [{"learner_id": 2, "name": "学员乙"}])
        self.assertEqual(self.learner_courses(1), [])
        # 课程详情、学员记录与学员2的报名不变
        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )
        learner = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(learner.stdout), {"learner_id": 1, "name": "学员甲"}
        )
        self.assertEqual(
            [c["course_id"] for c in self.learner_courses(2)], [1]
        )

        # 再报名学员1，名册恢复
        reenrolled = self.run_command(ENROLL, 1, 1)
        self.assertEqual(reenrolled.returncode, 0)
        self.assertEqual(reenrolled.stderr, "")
        self.assertEqual(
            json.loads(reenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )
        self.assertEqual(
            self.roster(),
            [
                {"learner_id": 1, "name": "学员甲"},
                {"learner_id": 2, "name": "学员乙"},
            ],
        )

    def test_enroll_output_is_single_line_id_json(self):
        result = self.run_command(ENROLL, 1, 1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, '{"learner_id": 1, "course_id": 1}\n')

    def test_duplicate_enroll_keeps_single_relation(self):
        first = self.run_command(ENROLL, 1, 1)
        second = self.run_command(ENROLL, 1, 1)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            self.roster(),
            [
                {"learner_id": 1, "name": "学员甲"},
                {"learner_id": 2, "name": "学员乙"},
            ],
        )

    def test_repeat_unenroll_is_idempotent(self):
        first = self.run_command(UNENROLL, 1, 1)
        second = self.run_command(UNENROLL, 1, 1)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(self.roster(), [{"learner_id": 2, "name": "学员乙"}])

    def test_unenroll_never_enrolled_valid_pair_succeeds(self):
        run_cli(self.db_path, "add-course", "--title", "进阶培训",
                "--chapter", "深入")
        result = self.run_command(UNENROLL, 1, 2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "course_id": 2}
        )
        # 原有报名关系不受影响
        self.assertEqual(len(self.roster()), 2)

    def test_unenroll_only_removes_target_pair(self):
        self.assertEqual(self.run_command(UNENROLL, 1, 1).returncode, 0)
        self.assertEqual(self.roster(), [{"learner_id": 2, "name": "学员乙"}])
        self.assertEqual(
            [c["course_id"] for c in self.learner_courses(2)], [1]
        )

    def test_db_option_after_subcommand(self):
        unenrolled = self.run_command(UNENROLL, 1, 1, db_first=False)
        self.assertEqual(unenrolled.returncode, 0)
        self.assertEqual(
            json.loads(unenrolled.stdout), {"learner_id": 1, "course_id": 1}
        )
        enrolled = self.run_command(ENROLL, 1, 1, db_first=False)
        self.assertEqual(enrolled.returncode, 0)
        self.assertEqual(len(self.roster()), 2)

    def test_id_affixes_accepted(self):
        for command in BOTH_COMMANDS:
            for raw in ["+1", "01", " 1 ", "\t1\n"]:
                with self.subTest(command=command, learner_id=raw):
                    result = run_cli(
                        self.db_path, command, raw, "--course", " 01 "
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(
                        json.loads(result.stdout),
                        {"learner_id": 1, "course_id": 1},
                    )
                    # 恢复固定样例，保证下一轮子测试起点一致
                    self.assertEqual(self.run_command(ENROLL, 1, 1).returncode, 0)


class TestSharedErrorPriority(EnrollmentRefactorTestCase):
    """两种命令按相同顺序只报告首个错误，且失败不改动业务记录。"""

    def test_bad_learner_id_format_reported_first(self):
        for command in BOTH_COMMANDS:
            for raw in ["0", "-3", "abc", "1.5", ""]:
                with self.subTest(command=command, learner_id=raw):
                    self.assert_failure(
                        self.run_command(command, raw, 1),
                        2,
                        "学员编号必须为正整数",
                    )
        self.assert_sample_untouched()

    def test_bad_course_id_format(self):
        for command in BOTH_COMMANDS:
            for raw in ["0", "-2", "xyz", ""]:
                with self.subTest(command=command, course_id=raw):
                    self.assert_failure(
                        self.run_command(command, 1, raw),
                        2,
                        "课程编号必须为正整数",
                    )
        self.assert_sample_untouched()

    def test_learner_format_error_precedes_course_format_error(self):
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_command(command, "abc", "xyz"),
                    2,
                    "学员编号必须为正整数",
                )

    def test_course_format_error_precedes_learner_existence(self):
        # 未知学员搭配非法课程编号时，两种命令均先报课程编号错误
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_command(command, 99, "abc"),
                    2,
                    "课程编号必须为正整数",
                )
                self.assert_failure(
                    self.run_command(command, 99, "0"),
                    2,
                    "课程编号必须为正整数",
                )
        self.assert_sample_untouched()

    def test_unknown_learner_precedes_course_existence(self):
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_command(command, 99, 1), 1, "学员不存在"
                )
                # 学员与课程都不存在时只报学员不存在
                self.assert_failure(
                    self.run_command(command, 99, 98), 1, "学员不存在"
                )
        self.assert_sample_untouched()

    def test_unknown_course(self):
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_command(command, 1, 98), 1, "课程不存在"
                )
        self.assert_sample_untouched()

    def test_overflow_ids_treated_as_unknown(self):
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_command(command, OVERFLOW_ID, 1),
                    1,
                    "学员不存在",
                )
                self.assert_failure(
                    self.run_command(command, 1, OVERFLOW_ID),
                    1,
                    "课程不存在",
                )
        self.assert_sample_untouched()

    def test_missing_arguments_use_argparse_error(self):
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                missing_course = run_cli(self.db_path, command, "1")
                self.assertEqual(missing_course.returncode, 2)
                self.assertEqual(missing_course.stdout, "")
                missing_learner = run_cli(self.db_path, command, "--course", "1")
                self.assertEqual(missing_learner.returncode, 2)
                self.assertEqual(missing_learner.stdout, "")
        self.assert_sample_untouched()

    def test_db_opened_before_id_validation(self):
        # 数据库打开或创建先于业务编号校验：库无法打开时报数据库错误，
        # 即使编号格式非法也不报编号错误
        bad_db = Path(self._tmp.name) / "missing" / "test.db"
        for command in BOTH_COMMANDS:
            with self.subTest(command=command):
                result = run_cli(bad_db, command, "abc", "--course", "xyz")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertTrue(
                    result.stderr.startswith("无法打开数据库"),
                    result.stderr,
                )
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
