"""课程报名名册（list-course-learners）公开行为的回归测试。

通过子进程调用 ``python -m course_progress``，并核对包导出的
``list_course_learners`` 返回值，验证：名册输出结构与字段、按学员
编号升序、只列已报名学员、改名后显示新姓名、无人报名时返回空数组、
编号解析与错误结果和报名入口一致、查询为只读操作、跨进程持久化、
数据库文件隔离、兼容此前数据库（原有课程名册初始为空）。每个用例
使用独立临时目录，结束后自动清理，不依赖仓库中已有数据库或联网。
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


class RosterTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "roster.db"

    def open_db(self):
        """打开（必要时创建）临时数据库，用例结束时自动关闭连接。"""
        conn = course_progress.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def add_course(self, title="入门培训"):
        result = run_cli(
            self.db_path, "add-course", "--title", title, "--chapter", "准备"
        )
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id):
        result = run_cli(
            self.db_path, "enroll-learner", str(learner_id),
            "--course", str(course_id),
        )
        self.assertEqual(result.returncode, 0)

    def roster_cli(self, course_id, db_first=True):
        result = run_cli(
            self.db_path, "list-course-learners", str(course_id),
            db_first=db_first,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_roster_success(self, result):
        """成功名册查询：退出码 0、标准错误为空、输出一行可解析 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def assert_failure(self, result, exit_code, message):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)


class TestRosterContent(RosterTestCase):
    def test_fixed_sample(self):
        """固定样例：名册为 {"course_id":1,"learners":[{"learner_id":1,"name":"学员甲"}]}。"""
        self.assertEqual(self.add_course("入门培训"), 1)
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.enroll(1, 1)

        result = run_cli(self.db_path, "list-course-learners", "1")
        payload = self.assert_roster_success(result)

        self.assertEqual(
            payload,
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        self.assertEqual(set(payload.keys()), {"course_id", "learners"})
        self.assertEqual(
            set(payload["learners"][0].keys()), {"learner_id", "name"}
        )

    def test_function_matches_cli(self):
        """函数入口与 CLI 输出一致。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备"])
        course_progress.add_learner(conn, "学员甲")
        course_progress.enroll_learner(conn, 1, 1)

        expected = course_progress.list_course_learners(conn, 1)
        self.assertEqual(
            expected,
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        conn.close()

        self.assertEqual(self.roster_cli(1), expected)

    def test_empty_roster_for_existing_course(self):
        """课程存在但无人报名时返回该编号和空数组。"""
        self.add_course("入门培训")
        self.add_learner("学员甲")

        self.assertEqual(
            self.roster_cli(1), {"course_id": 1, "learners": []}
        )
        conn = self.open_db()
        self.assertEqual(
            course_progress.list_course_learners(conn, 1),
            {"course_id": 1, "learners": []},
        )

    def test_legacy_database_course_starts_with_empty_roster(self):
        """兼容此前数据库：只含课程的旧库打开后名册初始为空。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备", "学习"])
        conn.close()

        self.assertEqual(
            self.roster_cli(1), {"course_id": 1, "learners": []}
        )
        # 课程详情保持原样
        conn = self.open_db()
        self.assertEqual(
            course_progress.get_course(conn, 1),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备", "学习"]},
        )

    def test_learners_ordered_by_id_not_enroll_order(self):
        """learners 按学员编号升序，与报名先后顺序无关。"""
        self.add_course()
        for name in ["学员丙", "学员乙", "学员甲"]:
            self.add_learner(name)
        # 刻意按 3、1、2 的顺序报名
        for learner_id in (3, 1, 2):
            self.enroll(learner_id, 1)

        payload = self.roster_cli(1)
        self.assertEqual(
            payload,
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员丙"},
                    {"learner_id": 2, "name": "学员乙"},
                    {"learner_id": 3, "name": "学员甲"},
                ],
            },
        )

    def test_only_enrolled_learners_listed(self):
        """只列已报名学员；报名到其他课程的学员不出现。"""
        self.add_course("入门培训")
        self.add_course("进阶培训")
        self.add_learner("学员甲")
        self.add_learner("学员乙")
        self.add_learner("学员丙")
        self.enroll(1, 1)
        self.enroll(3, 2)

        self.assertEqual(
            self.roster_cli(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        self.assertEqual(
            self.roster_cli(2),
            {"course_id": 2, "learners": [{"learner_id": 3, "name": "学员丙"}]},
        )

    def test_roster_uses_current_name_after_rename(self):
        """名册使用当前保存的姓名，改名后显示新姓名。"""
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)

        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", "学员甲（新）"
        )
        self.assertEqual(renamed.returncode, 0)

        self.assertEqual(
            self.roster_cli(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲（新）"}]},
        )

    def test_same_name_learners_listed_separately(self):
        """同名学员按编号分别列出，不合并。"""
        self.add_course()
        self.add_learner("学员甲")
        self.add_learner("学员甲")
        self.enroll(1, 1)
        self.enroll(2, 1)

        self.assertEqual(
            self.roster_cli(1),
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员甲"},
                ],
            },
        )

    def test_id_affixes_follow_existing_semantics(self):
        """课程编号沿用现有解析语义：正号、前导零、外围空白均可。"""
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)

        expected = {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]}
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(course_id=raw):
                self.assertEqual(self.roster_cli(raw), expected)


class TestRosterReadOnlyAndPersistence(RosterTestCase):
    def test_query_is_read_only(self):
        """查询不修改报名关系、课程与学员记录，也不占用编号。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备"])
        course_progress.add_learner(conn, "学员甲")
        course_progress.enroll_learner(conn, 1, 1)
        before = course_progress.list_course_learners(conn, 1)
        conn.close()

        self.roster_cli(1)
        self.roster_cli(1)

        conn = self.open_db()
        self.assertEqual(course_progress.list_course_learners(conn, 1), before)
        self.assertEqual(
            course_progress.get_course(conn, 1),
            {"course_id": 1, "title": "入门培训", "chapters": ["准备"]},
        )
        self.assertEqual(
            course_progress.get_learner(conn, 1),
            {"learner_id": 1, "name": "学员甲"},
        )
        self.assertEqual(course_progress.add_learner(conn, "学员乙"), 2)

    def test_reopen_and_other_process_see_same_roster(self):
        """关闭连接后重开同一文件，以及另一个进程查询，名册一致。"""
        conn = self.open_db()
        course_progress.add_course(conn, "入门培训", ["准备"])
        course_progress.add_learner(conn, "学员甲")
        course_progress.enroll_learner(conn, 1, 1)
        expected = course_progress.list_course_learners(conn, 1)
        conn.close()

        reopened = self.open_db()
        self.assertEqual(course_progress.list_course_learners(reopened, 1), expected)
        reopened.close()

        self.assertEqual(self.roster_cli(1), expected)

    def test_rosters_isolated_between_db_files(self):
        """不同数据库文件的名册互相隔离。"""
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)

        other_db = Path(self._tmp.name) / "other.db"
        run_cli(other_db, "add-course", "--title", "入门培训", "--chapter", "准备")

        other = run_cli(other_db, "list-course-learners", "1")
        self.assertEqual(
            self.assert_roster_success(other), {"course_id": 1, "learners": []}
        )
        self.assertEqual(
            self.roster_cli(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )


class TestRosterFailures(RosterTestCase):
    def test_bad_course_id_format(self):
        self.add_course()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(course_id=raw):
                result = run_cli(self.db_path, "list-course-learners", raw)
                self.assert_failure(result, 2, "课程编号必须为正整数")

    def test_unknown_course(self):
        self.add_course()
        result = run_cli(self.db_path, "list-course-learners", "42")
        self.assert_failure(result, 1, "课程不存在")

    def test_overflow_id_treated_as_unknown(self):
        """超出 SQLite 整数范围的编号按不存在处理。"""
        self.add_course()
        for raw in [
            str(SQLITE_INT64_MAX + 1),
            " +9223372036854775808 ",
            "009223372036854775808",
            "9" * 26,
            str(SQLITE_INT64_MAX),
        ]:
            with self.subTest(course_id=raw):
                result = run_cli(self.db_path, "list-course-learners", raw)
                self.assert_failure(result, 1, "课程不存在")

    def test_failure_does_not_create_or_modify_records(self):
        """查询失败不在数据库中留下任何记录。"""
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)

        result = run_cli(self.db_path, "list-course-learners", "99")
        self.assert_failure(result, 1, "课程不存在")

        self.assertEqual(
            self.roster_cli(1),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        conn = self.open_db()
        self.assertIsNone(course_progress.get_course(conn, 99))

    def test_missing_course_id_argument_exits_2(self):
        result = run_cli(self.db_path, "list-course-learners")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_option_exits_with_code_2(self):
        result = subprocess.run(
            [sys.executable, "-m", "course_progress", "list-course-learners", "1"],
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


class TestDbOptionPlacement(RosterTestCase):
    def test_db_option_before_and_after_subcommand_are_equivalent(self):
        """--db 写在 list-course-learners 前后结果相同。"""
        self.add_course()
        self.add_learner("学员甲")
        self.enroll(1, 1)

        expected = {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]}
        self.assertEqual(self.roster_cli(1, db_first=True), expected)
        self.assertEqual(self.roster_cli(1, db_first=False), expected)


if __name__ == "__main__":
    unittest.main()
