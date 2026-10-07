"""学员报名（enroll-learner）公开行为的回归测试。

通过子进程调用 ``python -m course_progress``，并核对包导出的
``enroll_learner`` 返回值，验证：成功报名的输出与退出码、重复报名
幂等、多对多报名、同名学员与同标题课程按编号区分、编号解析语义
（正号、前导零、外围空白、超出 SQLite 整数范围）、四类错误按
学员编号格式、课程编号格式、学员存在性、课程存在性的顺序只报首个、
失败不产生报名记录、跨进程持久化与数据库文件隔离。每个用例使用
独立临时目录，结束后自动清理，不依赖仓库中已有数据库或联网。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import course_progress

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
        self.db_path = Path(self._tmp.name) / "enroll.db"

    def open_db(self):
        """打开（必要时创建）临时数据库，用例结束时自动关闭连接。"""
        conn = course_progress.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def add_course(self, title="入门培训", chapters=("准备",)):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            title,
            *sum((["--chapter", c] for c in chapters), []),
        )
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name="学员甲"):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course, db_first=True):
        return run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course),
            db_first=db_first,
        )

    def roster(self, course_id):
        result = run_cli(self.db_path, "list-course-learners", str(course_id))
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)

    def assert_enroll_success(self, result, learner_id, course_id):
        """成功报名：退出码 0、标准错误为空、输出一行仅含两个编号的 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload, {"learner_id": learner_id, "course_id": course_id}
        )
        return payload

    def assert_failure(self, result, exit_code, message):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)


class TestEnrollSuccess(EnrollTestCase):
    def test_fixed_sample(self):
        """固定样例：编号均为 1 的课程与学员，报名输出两个编号均为 1。"""
        self.assertEqual(self.add_course("入门培训"), 1)
        self.assertEqual(self.add_learner("学员甲"), 1)

        result = self.enroll(1, 1)
        self.assert_enroll_success(result, 1, 1)

    def test_function_matches_cli_and_returns_same_content(self):
        """函数入口与 CLI 结果一致，重复报名返回相同内容且名册只记一次。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备"])
        course_progress.add_learner(conn, "学员甲")

        first = course_progress.enroll_learner(conn, 1, 1)
        second = course_progress.enroll_learner(conn, 1, 1)
        self.assertEqual(first, {"learner_id": 1, "course_id": 1})
        self.assertEqual(second, first)
        conn.close()

        cli = self.enroll(1, 1)
        self.assert_enroll_success(cli, 1, 1)
        self.assertEqual(
            self.roster(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_duplicate_enroll_via_cli_roster_lists_once(self):
        """CLI 重复报名同一组合仍成功，名册中该学员只出现一次。"""
        self.add_course()
        self.add_learner()

        self.assert_enroll_success(self.enroll(1, 1), 1, 1)
        self.assert_enroll_success(self.enroll(1, 1), 1, 1)

        self.assertEqual(
            self.roster(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_many_to_many(self):
        """一个学员可报名多门课程，一门课程可接收多名学员。"""
        self.add_course("入门培训")
        self.add_course("进阶培训")
        self.add_learner("学员甲")
        self.add_learner("学员乙")

        self.assert_enroll_success(self.enroll(1, 1), 1, 1)
        self.assert_enroll_success(self.enroll(1, 2), 1, 2)
        self.assert_enroll_success(self.enroll(2, 1), 2, 1)

        self.assertEqual(
            self.roster(1),
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员乙"},
                ],
            },
        )
        self.assertEqual(
            self.roster(2),
            {"course_id": 2, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_same_names_distinguished_by_id(self):
        """同名学员、同标题课程按各自编号区分，互不影响。"""
        self.add_course("入门培训")
        self.add_course("入门培训")
        self.add_learner("学员甲")
        self.add_learner("学员甲")

        self.assert_enroll_success(self.enroll(2, 2), 2, 2)

        self.assertEqual(self.roster(1), {"course_id": 1, "learners": []})
        self.assertEqual(
            self.roster(2),
            {"course_id": 2, "learners": [{"learner_id": 2, "name": "学员甲"}]},
        )

    def test_enroll_leaves_course_and_learner_records_unchanged(self):
        """报名不修改课程详情与学员记录，也不占用任何编号。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备", "学习"])
        course_progress.add_learner(conn, "学员甲")
        course_progress.enroll_learner(conn, 1, 1)

        self.assertEqual(
            course_progress.get_course(conn, 1),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备", "学习"]},
        )
        self.assertEqual(
            course_progress.get_learner(conn, 1),
            {"learner_id": 1, "name": "学员甲"},
        )
        # 编号生成不受报名影响
        self.assertEqual(course_progress.add_course(conn, "进阶培训", ["实战"]), 2)
        self.assertEqual(course_progress.add_learner(conn, "学员乙"), 2)

    def test_db_option_after_subcommand(self):
        """--db 写在子命令之后同样可用。"""
        self.add_course()
        self.add_learner()

        result = self.enroll(1, 1, db_first=False)
        self.assert_enroll_success(result, 1, 1)

    def test_id_affixes_follow_existing_semantics(self):
        """编号沿用现有解析语义：正号、前导零、外围空白均可。"""
        self.add_course()
        self.add_learner()

        for raw_learner, raw_course in [
            ("+1", "1"),
            ("01", "1"),
            (" 1 ", "1"),
            ("1", "+1"),
            ("1", "01"),
            ("1", " 1 "),
        ]:
            with self.subTest(learner_id=raw_learner, course=raw_course):
                self.assert_enroll_success(
                    self.enroll(raw_learner, raw_course), 1, 1
                )

    def test_persists_across_reopen(self):
        """报名关系落盘保存，重开同一数据库仍能查询。"""
        self.add_course()
        self.add_learner()
        self.assert_enroll_success(self.enroll(1, 1), 1, 1)

        conn = self.open_db()
        self.assertEqual(
            course_progress.list_course_learners(conn, 1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        conn.close()

        self.assertEqual(
            self.roster(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_enrollments_isolated_between_db_files(self):
        """不同数据库文件的报名关系互相隔离。"""
        self.add_course()
        self.add_learner()
        self.assert_enroll_success(self.enroll(1, 1), 1, 1)

        other_db = Path(self._tmp.name) / "other.db"
        run_cli(other_db, "add-course", "--title", "入门培训", "--chapter", "准备")
        run_cli(other_db, "add-learner", "--name", "学员甲")

        other_roster = run_cli(other_db, "list-course-learners", "1")
        self.assertEqual(
            json.loads(other_roster.stdout), {"course_id": 1, "learners": []}
        )
        # 原库名册不受另一库操作影响
        self.assertEqual(
            self.roster(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )


class TestEnrollFailures(EnrollTestCase):
    def test_missing_arguments_exit_2(self):
        """缺少学员编号或 --course 时沿用参数解析错误，退出码 2。"""
        for argv in [
            ["enroll-learner"],
            ["enroll-learner", "1"],
            ["enroll-learner", "--course", "1"],
        ]:
            with self.subTest(argv=argv):
                result = run_cli(self.db_path, *argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)

    def test_bad_learner_id_format(self):
        self.add_course()
        self.add_learner()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    self.enroll(raw, 1), 2, "学员编号必须为正整数"
                )

    def test_bad_course_id_format(self):
        self.add_course()
        self.add_learner()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(course=raw):
                self.assert_failure(
                    self.enroll(1, raw), 2, "课程编号必须为正整数"
                )

    def test_error_order_format_before_existence(self):
        """两类格式错误都先于存在性判定；学员编号格式最先。"""
        self.add_course()
        self.add_learner()
        # 学员编号格式优先于课程编号格式
        self.assert_failure(
            self.enroll("abc", "xyz"), 2, "学员编号必须为正整数"
        )
        # 课程编号格式优先于学员存在性
        self.assert_failure(
            self.enroll(99, "xyz"), 2, "课程编号必须为正整数"
        )
        # 学员存在性优先于课程存在性
        self.assert_failure(self.enroll(99, 99), 1, "学员不存在")

    def test_unknown_learner(self):
        self.add_course()
        self.assert_failure(self.enroll(42, 1), 1, "学员不存在")

    def test_unknown_course(self):
        self.add_learner()
        self.assert_failure(self.enroll(1, 42), 1, "课程不存在")

    def test_overflow_ids_treated_as_unknown(self):
        """超出 SQLite 整数范围的编号按不存在处理。"""
        self.add_course()
        self.add_learner()
        for raw in [
            str(SQLITE_INT64_MAX + 1),
            " +9223372036854775808 ",
            "009223372036854775808",
            "9" * 26,
        ]:
            with self.subTest(raw=raw):
                self.assert_failure(self.enroll(raw, 1), 1, "学员不存在")
                self.assert_failure(self.enroll(1, raw), 1, "课程不存在")
        # int64 上限本身只是普通的不存在编号
        self.assert_failure(
            self.enroll(SQLITE_INT64_MAX, 1), 1, "学员不存在"
        )
        self.assert_failure(
            self.enroll(1, SQLITE_INT64_MAX), 1, "课程不存在"
        )

    def test_failure_leaves_no_enrollment(self):
        """业务失败不新增报名记录，也不修改已有数据。"""
        self.add_course()
        self.add_learner()
        self.assert_enroll_success(self.enroll(1, 1), 1, 1)
        before = self.roster(1)

        self.assert_failure(self.enroll(99, 1), 1, "学员不存在")
        self.assert_failure(self.enroll(1, 99), 1, "课程不存在")
        self.assert_failure(self.enroll("abc", 1), 2, "学员编号必须为正整数")

        self.assertEqual(self.roster(1), before)
        conn = self.open_db()
        self.assertEqual(
            course_progress.list_course_learners(conn, 1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_missing_db_option_exits_with_code_2(self):
        result = subprocess.run(
            [sys.executable, "-m", "course_progress", "enroll-learner", "1",
             "--course", "1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(), "必须通过 --db 指定数据库文件"
        )
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
