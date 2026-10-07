"""学员存储接口（Python 调用方）的回归测试。

直接调用 course_progress 导出的 connect、add_learner、get_learner，
围绕“登记后持久保存并按编号读取”这一条流程核对返回值、异常与保存结果：
成功登记与重连读取、同名学员分别登记、空姓名校验失败不留记录、
未知及超出 SQLite 整数范围的编号返回 None、不同数据库文件相互隔离。
每个用例使用独立的临时目录与临时 SQLite 文件，结束时关闭连接并自动清理。
"""

import tempfile
import unittest
from pathlib import Path

import course_progress.core
from course_progress import add_learner, connect, get_learner
from course_progress.core import ValidationError

SQLITE_INT64_MAX = 2**63 - 1
SQLITE_INT64_MAX_PLUS_ONE = 2**63
SQLITE_INT64_MIN_MINUS_ONE = -(2**63) - 1

RAW_NAME = "  Zhang  San  "
STRIPPED_NAME = "Zhang  San"
ERR_EMPTY_NAME = "学员姓名不能为空"
EMPTY_NAMES = ["", "   ", "\t", "\n", " \t\n ", "\t\t", "\n  \n"]


class LearnerStorageTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，默认连接一个空数据库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = self.open_connection()

    def open_connection(self):
        """打开当前临时数据库并注册关闭清理（清理按注册逆序执行，
        因此连接先于临时目录关闭）。"""
        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def reopen_connection(self):
        """关闭当前连接并重新连接同一文件，模拟跨连接持久化读取。"""
        self.conn.close()
        self.conn = self.open_connection()
        return self.conn

    def assert_empty_name_rejected(self, conn, name):
        with self.assertRaises(ValidationError) as ctx:
            add_learner(conn, name)
        self.assertIs(type(ctx.exception), ValidationError)
        self.assertIs(ctx.exception.__class__, course_progress.core.ValidationError)
        self.assertEqual(str(ctx.exception), ERR_EMPTY_NAME)


class TestAddThenGet(LearnerStorageTestCase):
    def test_add_returns_integer_id_starting_at_one(self):
        learner_id = add_learner(self.conn, RAW_NAME)
        self.assertIsInstance(learner_id, int)
        self.assertNotIsInstance(learner_id, bool)
        self.assertEqual(learner_id, 1)

    def test_get_returns_exact_dict_with_only_edge_whitespace_trimmed(self):
        learner_id = add_learner(self.conn, RAW_NAME)
        self.assertEqual(learner_id, 1)
        # 整体严格相等：键名、编号、姓名均一致；内部双空格与大小写保留
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        self.assertEqual(get_learner(self.conn, 1)["name"], "Zhang  San")

    def test_same_name_registers_as_two_independent_records(self):
        first = add_learner(self.conn, RAW_NAME)
        second = add_learner(self.conn, RAW_NAME)
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        self.assertEqual(
            get_learner(self.conn, 2),
            {"learner_id": 2, "name": STRIPPED_NAME},
        )

    def test_records_persist_after_closing_and_reconnecting(self):
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)
        self.assertEqual(add_learner(self.conn, RAW_NAME), 2)

        conn = self.reopen_connection()
        self.assertEqual(
            get_learner(conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        self.assertEqual(
            get_learner(conn, 2),
            {"learner_id": 2, "name": STRIPPED_NAME},
        )


class TestAddValidationFailures(LearnerStorageTestCase):
    def test_empty_and_blank_names_raise_validation_error(self):
        for name in EMPTY_NAMES:
            with self.subTest(name=repr(name)):
                self.assert_empty_name_rejected(self.conn, name)

    def test_failures_after_two_learners_leave_records_and_sequence_intact(self):
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)
        self.assertEqual(add_learner(self.conn, RAW_NAME), 2)

        for name in EMPTY_NAMES:
            with self.subTest(name=repr(name)):
                self.assert_empty_name_rejected(self.conn, name)

        # 原有两条记录不变
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        self.assertEqual(
            get_learner(self.conn, 2),
            {"learner_id": 2, "name": STRIPPED_NAME},
        )
        # 失败不占用编号：下一次成功登记仍是 3
        self.assertEqual(add_learner(self.conn, "Li Si"), 3)
        self.assertEqual(
            get_learner(self.conn, 3),
            {"learner_id": 3, "name": "Li Si"},
        )

    def test_failure_then_success_on_empty_db_starts_at_one(self):
        self.assert_empty_name_rejected(self.conn, " \t\n")
        self.assertIsNone(get_learner(self.conn, 1))
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )


class TestGetUnknown(LearnerStorageTestCase):
    def test_unregistered_positive_id_returns_none(self):
        self.assertIsNone(get_learner(self.conn, 42))
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)
        self.assertIsNone(get_learner(self.conn, 2))

    def test_out_of_int64_range_ids_return_none_without_overflow_error(self):
        # 超出 SQLite 64 位有符号整数范围的编号视为不存在，不抛 OverflowError
        self.assertIsNone(
            get_learner(self.conn, SQLITE_INT64_MAX_PLUS_ONE)
        )
        self.assertIsNone(
            get_learner(self.conn, SQLITE_INT64_MIN_MINUS_ONE)
        )

    def test_int64_max_on_empty_db_returns_none(self):
        self.assertIsNone(get_learner(self.conn, SQLITE_INT64_MAX))

    def test_unknown_queries_change_nothing_and_sequence_continues(self):
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)
        self.assertEqual(add_learner(self.conn, RAW_NAME), 2)

        for learner_id in (
            3,
            42,
            SQLITE_INT64_MAX,
            SQLITE_INT64_MAX_PLUS_ONE,
            SQLITE_INT64_MIN_MINUS_ONE,
        ):
            with self.subTest(learner_id=learner_id):
                self.assertIsNone(get_learner(self.conn, learner_id))

        # 已保存的姓名和编号不变，递增顺序延续
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        self.assertEqual(
            get_learner(self.conn, 2),
            {"learner_id": 2, "name": STRIPPED_NAME},
        )
        self.assertEqual(add_learner(self.conn, "Li Si"), 3)
        self.assertEqual(
            get_learner(self.conn, 3),
            {"learner_id": 3, "name": "Li Si"},
        )


class TestDatabaseIsolation(LearnerStorageTestCase):
    def test_learners_are_not_visible_in_another_db_file(self):
        self.assertEqual(add_learner(self.conn, RAW_NAME), 1)

        other_path = Path(self._tmp.name) / "other.db"
        other_conn = connect(other_path)
        self.addCleanup(other_conn.close)

        # 一个文件中的学员在另一文件中不可见
        self.assertIsNone(get_learner(other_conn, 1))
        # 第二个文件的首位学员仍从 1 开始编号
        self.assertEqual(add_learner(other_conn, RAW_NAME), 1)
        self.assertEqual(
            get_learner(other_conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )
        # 两个文件中的 1 号学员互不影响，原文件记录不变
        self.assertEqual(
            get_learner(self.conn, 1),
            {"learner_id": 1, "name": STRIPPED_NAME},
        )


if __name__ == "__main__":
    unittest.main()
