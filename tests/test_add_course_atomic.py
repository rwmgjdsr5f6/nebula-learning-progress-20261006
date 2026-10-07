"""add_course 写入中途失败时课程与有序章节整体保存的回归测试。

通过测试专用的 SQLite 触发器，让课程记录与第一章写入后、第二章插入时
抛出 sqlite3.IntegrityError，确认 add_course 不把数据库错误改写成
ValidationError、不返回课程编号，且本次登记在数据库中不留任何课程或
章节残留（同一连接与关闭重开后分别确认，不凭内存返回值判定回滚）。
故障解除后同一数据库可继续正常登记，编号连续递增。

分别覆盖空数据库与已有一门课程的数据库两种情形。只调用
course_progress 已导出的 connect、add_course、get_course、list_courses，
每个用例使用独立的临时 SQLite 文件，结束后自动清理，不读取或覆盖仓库
中已有的数据库文件。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError

# 固定样例课程：标题“入门培训”，章节依次排列。
SAMPLE_TITLE = "入门培训"
SAMPLE_CHAPTERS = ["准备", "学习", "回顾"]

# 已有库情形中预先登记的课程。
EXISTING_TITLE = "已有课程"
EXISTING_CHAPTERS = ["原章"]

# 测试专用故障：第二章（position = 1）插入时被约束拒绝。
# TEMP 触发器仅存在于当前连接，DROP TRIGGER 或连接关闭后即撤销。
FAULT_TRIGGER_SQL = """
CREATE TEMP TRIGGER fail_second_chapter_insert
BEFORE INSERT ON chapters
WHEN NEW.position = 1
BEGIN
    SELECT RAISE(ABORT, 'test-injected chapter failure');
END;
"""


class AddCourseAtomicityTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与数据库文件，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "atomic.db"
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)

    # ---- 故障设置与撤销 ----

    def install_fault(self):
        """在当前连接上安装故障：第二章插入将被 SQLite 约束拒绝。"""
        self.conn.execute(FAULT_TRIGGER_SQL)

    def remove_fault(self):
        """撤销测试故障，恢复正常的章节写入。"""
        self.conn.execute("DROP TRIGGER fail_second_chapter_insert")

    # ---- 读取辅助 ----

    def chapter_rows(self, conn):
        """直接读取 chapters 表全部行，用于确认没有遗漏的章节数据。"""
        return conn.execute(
            "SELECT course_id, position, name FROM chapters"
            " ORDER BY course_id, position"
        ).fetchall()

    def reopen(self):
        """关闭当前连接并重新打开同一数据库文件，返回新连接。"""
        self.conn.close()
        self.conn = connect(self.db_path)
        return self.conn

    # ---- 公共断言 ----

    def assert_registration_fails(self):
        """登记固定样例抛出 sqlite3.IntegrityError，且不返回课程编号。

        数据库错误不得被改写成 ValidationError；异常消息不做限定，
        以兼容不同 SQLite 版本的措辞。
        """
        with self.assertRaises(sqlite3.IntegrityError) as caught:
            add_course(self.conn, SAMPLE_TITLE, SAMPLE_CHAPTERS)
        self.assertNotIsInstance(caught.exception, ValidationError)
        # 失败后连接不处于未决事务中，仍可继续使用
        self.assertFalse(self.conn.in_transaction)

    def test_empty_database_rolls_back_then_registers_as_id_1(self):
        """空库：写入中途失败后无任何残留，解除故障再登记得到编号 1。"""
        self.install_fault()
        self.assert_registration_fails()

        # 失败后同一连接：查不到本次课程，概览为空，无章节残留
        self.assertIsNone(get_course(self.conn, 1))
        self.assertEqual(list_courses(self.conn), [])
        self.assertEqual(self.chapter_rows(self.conn), [])

        # 解除测试故障后关闭连接，重新打开同一文件确认回滚已落盘
        self.remove_fault()
        conn = self.reopen()
        self.assertIsNone(get_course(conn, 1))
        self.assertEqual(list_courses(conn), [])
        self.assertEqual(self.chapter_rows(conn), [])

        # 故障已撤销，重新登记固定样例得到编号 1
        self.assertEqual(add_course(conn, SAMPLE_TITLE, SAMPLE_CHAPTERS), 1)
        expected_overview = [
            {"course_id": 1, "title": SAMPLE_TITLE, "chapter_count": 3}
        ]
        self.assertEqual(
            get_course(conn, 1),
            {
                "course_id": 1,
                "title": SAMPLE_TITLE,
                "chapters": SAMPLE_CHAPTERS,
            },
        )
        self.assertEqual(list_courses(conn), expected_overview)
        self.assertEqual(
            self.chapter_rows(conn),
            [(1, 0, "准备"), (1, 1, "学习"), (1, 2, "回顾")],
        )

        # 关闭重开后仍返回完整课程与三章概览
        conn = self.reopen()
        self.assertEqual(
            get_course(conn, 1),
            {
                "course_id": 1,
                "title": SAMPLE_TITLE,
                "chapters": SAMPLE_CHAPTERS,
            },
        )
        self.assertEqual(list_courses(conn), expected_overview)
        self.assertEqual(
            self.chapter_rows(conn),
            [(1, 0, "准备"), (1, 1, "学习"), (1, 2, "回顾")],
        )

    def test_existing_course_untouched_then_registers_as_id_2(self):
        """已有库：失败尝试不改变已有课程，解除故障再登记得到编号 2。"""
        existing_id = add_course(self.conn, EXISTING_TITLE, EXISTING_CHAPTERS)
        self.assertEqual(existing_id, 1)
        existing_course = {
            "course_id": 1,
            "title": EXISTING_TITLE,
            "chapters": EXISTING_CHAPTERS,
        }
        existing_overview = [
            {"course_id": 1, "title": EXISTING_TITLE, "chapter_count": 1}
        ]
        existing_rows = [(1, 0, "原章")]

        self.install_fault()
        self.assert_registration_fails()

        # 失败后同一连接：已有课程的标题、章节与概览不变，无本次残留
        self.assertEqual(get_course(self.conn, 1), existing_course)
        self.assertIsNone(get_course(self.conn, 2))
        self.assertEqual(list_courses(self.conn), existing_overview)
        self.assertEqual(self.chapter_rows(self.conn), existing_rows)

        # 解除测试故障后关闭重开，确认已有数据与回滚结果一致
        self.remove_fault()
        conn = self.reopen()
        self.assertEqual(get_course(conn, 1), existing_course)
        self.assertIsNone(get_course(conn, 2))
        self.assertEqual(list_courses(conn), existing_overview)
        self.assertEqual(self.chapter_rows(conn), existing_rows)

        # 故障已撤销，下一次成功登记得到编号 2
        self.assertEqual(add_course(conn, SAMPLE_TITLE, SAMPLE_CHAPTERS), 2)
        full_overview = [
            {"course_id": 1, "title": EXISTING_TITLE, "chapter_count": 1},
            {"course_id": 2, "title": SAMPLE_TITLE, "chapter_count": 3},
        ]
        full_rows = [
            (1, 0, "原章"),
            (2, 0, "准备"),
            (2, 1, "学习"),
            (2, 2, "回顾"),
        ]
        self.assertEqual(get_course(conn, 1), existing_course)
        self.assertEqual(
            get_course(conn, 2),
            {
                "course_id": 2,
                "title": SAMPLE_TITLE,
                "chapters": SAMPLE_CHAPTERS,
            },
        )
        self.assertEqual(list_courses(conn), full_overview)
        self.assertEqual(self.chapter_rows(conn), full_rows)

        # 关闭重开后两门课程与概览保持一致
        conn = self.reopen()
        self.assertEqual(get_course(conn, 1), existing_course)
        self.assertEqual(
            get_course(conn, 2),
            {
                "course_id": 2,
                "title": SAMPLE_TITLE,
                "chapters": SAMPLE_CHAPTERS,
            },
        )
        self.assertEqual(list_courses(conn), full_overview)
        self.assertEqual(self.chapter_rows(conn), full_rows)


if __name__ == "__main__":
    unittest.main()
