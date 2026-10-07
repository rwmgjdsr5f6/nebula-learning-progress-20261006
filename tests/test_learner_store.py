"""学员存储接口（add_learner / get_learner）的回归测试。

直接调用 course_progress 导出的 connect、add_learner、get_learner，
围绕“登记后持久保存并按编号读取”这一条流程核对返回值、异常与保存
结果：姓名只去首尾空白（内部空白与大小写保留）、同名学员分别登记、
关闭连接后重连数据不变、空白姓名抛出 ValidationError 且不占用编号、
未登记及超出 SQLite 整数范围的编号返回 None、不同数据库文件互不可见。

每个用例使用独立的临时目录与 SQLite 文件，结束时关闭连接并清理，
重复执行结果一致，不依赖外部服务或第三方包。
"""

import tempfile
import unittest
from pathlib import Path

from course_progress import add_learner, connect, get_learner
from course_progress.core import ValidationError

# SQLite 整数为 64 位有符号；边界内外的代表性编号。
SQLITE_INT64_MAX = 2**63 - 1
SQLITE_INT64_OVERFLOW = 2**63
SQLITE_INT64_UNDERFLOW = -(2**63) - 1

# 固定样例：首尾各两个空格应去除，内部双空格与大小写保留。
PADDED_NAME = "  Zhang  San  "
STORED_NAME = "Zhang  San"

ERR_EMPTY_LEARNER_NAME = "学员姓名不能为空"


class LearnerStoreTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，连接在用例结束时关闭。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"

    def open_db(self, db_path=None):
        """打开（必要时创建）数据库，连接随用例清理自动关闭。"""
        conn = connect(str(db_path or self.db_path))
        self.addCleanup(conn.close)
        return conn

    def assert_blank_name_rejected(self, conn, name):
        with self.assertRaises(ValidationError) as ctx:
            add_learner(conn, name)
        self.assertEqual(str(ctx.exception), ERR_EMPTY_LEARNER_NAME)


class TestAddThenGet(LearnerStoreTestCase):
    def test_register_returns_int_one_and_strips_surrounding_whitespace(self):
        conn = self.open_db()
        learner_id = add_learner(conn, PADDED_NAME)
        self.assertIsInstance(learner_id, int)
        self.assertEqual(learner_id, 1)
        self.assertEqual(
            get_learner(conn, 1), {"learner_id": 1, "name": STORED_NAME}
        )

    def test_internal_whitespace_and_case_preserved(self):
        conn = self.open_db()
        add_learner(conn, PADDED_NAME)
        learner = get_learner(conn, 1)
        # 内部双空格与大小写原样保留，仅首尾空白被去除
        self.assertEqual(learner["name"], "Zhang  San")

    def test_same_name_registers_separately(self):
        conn = self.open_db()
        first = add_learner(conn, PADDED_NAME)
        second = add_learner(conn, PADDED_NAME)
        self.assertEqual((first, second), (1, 2))
        for learner_id in (1, 2):
            self.assertEqual(
                get_learner(conn, learner_id),
                {"learner_id": learner_id, "name": STORED_NAME},
            )

    def test_records_survive_close_and_reconnect(self):
        conn = self.open_db()
        first = add_learner(conn, PADDED_NAME)
        second = add_learner(conn, " 学员乙 ")
        conn.close()

        reopened = self.open_db()
        self.assertEqual(
            get_learner(reopened, first),
            {"learner_id": 1, "name": STORED_NAME},
        )
        self.assertEqual(
            get_learner(reopened, second),
            {"learner_id": 2, "name": "学员乙"},
        )


class TestAddValidationFailures(LearnerStoreTestCase):
    def test_blank_names_raise_validation_error(self):
        conn = self.open_db()
        for name in ["", "   ", "\t\t", "\n", " \t\n "]:
            with self.subTest(name=repr(name)):
                self.assert_blank_name_rejected(conn, name)

    def test_failure_on_empty_db_does_not_consume_id(self):
        conn = self.open_db()
        self.assert_blank_name_rejected(conn, "")
        self.assertEqual(add_learner(conn, PADDED_NAME), 1)
        self.assertEqual(
            get_learner(conn, 1), {"learner_id": 1, "name": STORED_NAME}
        )

    def test_failures_leave_existing_records_and_sequence_untouched(self):
        conn = self.open_db()
        self.assertEqual(add_learner(conn, PADDED_NAME), 1)
        self.assertEqual(add_learner(conn, "学员乙"), 2)

        for name in ["", "   ", "\t", "\n"]:
            with self.subTest(name=repr(name)):
                self.assert_blank_name_rejected(conn, name)

        # 原记录不变，随后的成功登记沿用递增顺序
        self.assertEqual(
            get_learner(conn, 1), {"learner_id": 1, "name": STORED_NAME}
        )
        self.assertEqual(
            get_learner(conn, 2), {"learner_id": 2, "name": "学员乙"}
        )
        self.assertEqual(add_learner(conn, "学员丙"), 3)
        self.assertEqual(
            get_learner(conn, 3), {"learner_id": 3, "name": "学员丙"}
        )


class TestGetUnknownIds(LearnerStoreTestCase):
    def test_unregistered_positive_id_returns_none(self):
        conn = self.open_db()
        add_learner(conn, PADDED_NAME)
        self.assertIsNone(get_learner(conn, 2))
        self.assertIsNone(get_learner(conn, 42))

    def test_out_of_range_ids_return_none_without_overflow(self):
        conn = self.open_db()
        add_learner(conn, PADDED_NAME)
        self.assertIsNone(get_learner(conn, SQLITE_INT64_OVERFLOW))
        self.assertIsNone(get_learner(conn, SQLITE_INT64_UNDERFLOW))

    def test_int64_max_on_empty_db_returns_none(self):
        conn = self.open_db()
        self.assertIsNone(get_learner(conn, SQLITE_INT64_MAX))

    def test_queries_do_not_change_records_or_id_sequence(self):
        conn = self.open_db()
        self.assertEqual(add_learner(conn, PADDED_NAME), 1)

        for learner_id in (2, SQLITE_INT64_MAX, SQLITE_INT64_OVERFLOW,
                           SQLITE_INT64_UNDERFLOW):
            with self.subTest(learner_id=learner_id):
                self.assertIsNone(get_learner(conn, learner_id))

        # 查询为只读：已保存的姓名与编号不变，下一次登记继续递增
        self.assertEqual(
            get_learner(conn, 1), {"learner_id": 1, "name": STORED_NAME}
        )
        self.assertEqual(add_learner(conn, "学员乙"), 2)


class TestDatabaseIsolation(LearnerStoreTestCase):
    def test_learners_not_visible_across_db_files(self):
        first_conn = self.open_db()
        self.assertEqual(add_learner(first_conn, PADDED_NAME), 1)

        other_path = Path(self._tmp.name) / "other.db"
        other_conn = self.open_db(other_path)
        self.assertIsNone(get_learner(other_conn, 1))

        # 第二个文件的编号独立，首位学员仍从 1 开始
        self.assertEqual(add_learner(other_conn, "学员乙"), 1)
        self.assertEqual(
            get_learner(other_conn, 1), {"learner_id": 1, "name": "学员乙"}
        )
        # 第一个文件不受第二个文件影响
        self.assertIsNone(get_learner(first_conn, 2))
        self.assertEqual(
            get_learner(first_conn, 1), {"learner_id": 1, "name": STORED_NAME}
        )


if __name__ == "__main__":
    unittest.main()
