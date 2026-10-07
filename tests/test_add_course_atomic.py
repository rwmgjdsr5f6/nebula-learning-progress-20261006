"""课程登记写入中途失败（原子性）的回归测试。

直接调用 course_progress 导出的 connect、add_course、get_course、list_courses，
验证课程与其有序章节只能整体保存：用测试触发器在第二章（position = 1）
写入时制造 SQLite 约束失败，此时课程记录与第一章已进入本次写入，add_course
必须抛出 sqlite3.IntegrityError（不返回课程编号、不伪装成 ValidationError），
且本次课程记录与已写入章节全部回滚——同一连接与关闭重开后的独立连接均
查询不到任何残留。撤销测试故障后再次登记固定样例应成功，编号语义不变。

分别覆盖空数据库（失败后仍无课程，故障解除后登记得到编号 1）与已有一门
课程的数据库（已有课程的标题、章节与概览不受影响，故障解除后登记得到
编号 2）。每个用例使用独立的临时 SQLite 文件，结束后自动清理，
不接触仓库中已有数据库。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError

# 固定样例：标题“入门培训”，章节依次排列。
SAMPLE_TITLE = "入门培训"
SAMPLE_CHAPTERS = ["准备", "学习", "回顾"]

# 已有库场景中预先登记的课程。
EXISTING_TITLE = "已有课程"
EXISTING_CHAPTERS = ["原章"]

# 测试故障：第二章（position = 1）写入时被触发器主动中止，模拟写入中途
# 发生的 SQLite 约束失败；此时课程记录与第一章已进入本次写入。
# DROP TRIGGER 即可可靠撤销，异常消息措辞由测试自己设定，不依赖 SQLite
# 版本相关的内置措辞。
FAULT_TRIGGER_SQL = """
CREATE TRIGGER fail_second_chapter_insert
BEFORE INSERT ON chapters
WHEN NEW.position = 1
BEGIN
    SELECT RAISE(ABORT, '测试故障：拒绝第二章写入');
END
"""
DROP_FAULT_TRIGGER_SQL = "DROP TRIGGER IF EXISTS fail_second_chapter_insert"


def sample_course(course_id):
    """固定样例登记成功后可读取的课程详情。"""
    return {
        "course_id": course_id,
        "title": SAMPLE_TITLE,
        "chapters": list(SAMPLE_CHAPTERS),
    }


def sample_overview_entry(course_id):
    """固定样例登记成功后概览中的条目（章节数为 3）。"""
    return {"course_id": course_id, "title": SAMPLE_TITLE, "chapter_count": 3}


def sample_chapter_rows(course_id):
    """固定样例在 chapters 表中应留下的全部行（按 position 升序）。"""
    return [
        (course_id, position, name)
        for position, name in enumerate(SAMPLE_CHAPTERS)
    ]


EXISTING_COURSE = {
    "course_id": 1,
    "title": EXISTING_TITLE,
    "chapters": list(EXISTING_CHAPTERS),
}
EXISTING_OVERVIEW_ENTRY = {
    "course_id": 1,
    "title": EXISTING_TITLE,
    "chapter_count": 1,
}
EXISTING_CHAPTER_ROWS = [(1, 0, "原章")]


class AddCourseAtomicTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，默认连接一个空数据库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "atomic.db"
        self.conn = self.open_connection()

    def open_connection(self):
        """打开当前临时数据库并注册关闭清理（清理按注册逆序执行，
        因此连接先于临时目录关闭）。"""
        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def reopen_connection(self):
        """关闭当前连接并重新连接同一文件，以独立连接确认落库结果，
        不仅凭内存中的返回值判定回滚完成。"""
        self.conn.close()
        self.conn = self.open_connection()
        return self.conn

    def install_second_chapter_fault(self, conn):
        """设置测试故障：第二章（position = 1）的写入被约束拒绝。"""
        conn.execute(FAULT_TRIGGER_SQL)

    def remove_second_chapter_fault(self, conn):
        """撤销测试故障，恢复章节正常写入。"""
        conn.execute(DROP_FAULT_TRIGGER_SQL)

    def assert_add_sample_fails_with_integrity_error(self, conn):
        """固定样例登记抛出 sqlite3.IntegrityError：不返回课程编号，
        也不把数据库错误改成 ValidationError；异常消息的具体措辞随
        SQLite 版本而定，不作限定。"""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            add_course(conn, SAMPLE_TITLE, SAMPLE_CHAPTERS)
        self.assertIs(type(ctx.exception), sqlite3.IntegrityError)
        self.assertNotIsInstance(ctx.exception, ValidationError)

    def assert_course_rows(self, conn, expected):
        """直接读取 courses 表，核对课程记录本身无残留。"""
        rows = conn.execute(
            "SELECT id, title FROM courses ORDER BY id"
        ).fetchall()
        self.assertEqual(rows, expected)

    def assert_chapter_rows(self, conn, expected):
        """直接读取 chapters 表，按 (course_id, position, name) 整体核对，
        确认没有本次失败登记的章节残留、也没有遗漏的章节数据。"""
        rows = conn.execute(
            "SELECT course_id, position, name FROM chapters"
            " ORDER BY course_id, position"
        ).fetchall()
        self.assertEqual(rows, expected)


class TestEmptyDatabase(AddCourseAtomicTestCase):
    """空数据库：失败后仍无课程，故障解除后登记固定样例得到编号 1。"""

    def test_failed_registration_leaves_no_trace(self):
        """第二章写入失败后，同一连接与重开连接均查不到本次任何残留。"""
        self.install_second_chapter_fault(self.conn)

        self.assert_add_sample_fails_with_integrity_error(self.conn)

        # 失败后同一连接仍可使用：查不到课程、概览为空、无章节残留
        self.assertIsNone(get_course(self.conn, 1))
        self.assertEqual(list_courses(self.conn), [])
        self.assert_course_rows(self.conn, [])
        self.assert_chapter_rows(self.conn, [])

        # 关闭并重开同一文件，以独立连接确认回滚已落库
        self.reopen_connection()
        self.assertIsNone(get_course(self.conn, 1))
        self.assertEqual(list_courses(self.conn), [])
        self.assert_course_rows(self.conn, [])
        self.assert_chapter_rows(self.conn, [])

    def test_fault_only_rejects_the_second_chapter(self):
        """故障只针对第二章：单章课程在故障设置期间仍可正常登记。"""
        self.install_second_chapter_fault(self.conn)

        course_id = add_course(self.conn, "单章课程", ["准备"])

        self.assertEqual(course_id, 1)
        self.assertEqual(
            get_course(self.conn, 1),
            {"course_id": 1, "title": "单章课程", "chapters": ["准备"]},
        )

    def test_registration_succeeds_with_id_one_after_fault_removed(self):
        """故障解除后再次登记固定样例：得到编号 1，三章按序整体保存。"""
        self.install_second_chapter_fault(self.conn)
        self.assert_add_sample_fails_with_integrity_error(self.conn)

        # 撤销测试故障后在同一连接上再次登记，证明连接仍可继续写入
        self.remove_second_chapter_fault(self.conn)
        course_id = add_course(self.conn, SAMPLE_TITLE, SAMPLE_CHAPTERS)

        self.assertEqual(course_id, 1)
        self.assertEqual(get_course(self.conn, 1), sample_course(1))
        self.assertEqual(list_courses(self.conn), [sample_overview_entry(1)])
        self.assert_course_rows(self.conn, [(1, SAMPLE_TITLE)])
        self.assert_chapter_rows(self.conn, sample_chapter_rows(1))

        # 关闭并重开同一文件，结果保持一致
        self.reopen_connection()
        self.assertEqual(get_course(self.conn, 1), sample_course(1))
        self.assertEqual(list_courses(self.conn), [sample_overview_entry(1)])
        self.assert_course_rows(self.conn, [(1, SAMPLE_TITLE)])
        self.assert_chapter_rows(self.conn, sample_chapter_rows(1))


class TestExistingCourseDatabase(AddCourseAtomicTestCase):
    """已有编号 1“已有课程”（章节“原章”）的数据库：失败尝试不得改变
    已有课程的标题、章节与概览，故障解除后下一次登记得到编号 2。"""

    def setUp(self):
        super().setUp()
        existing_id = add_course(self.conn, EXISTING_TITLE, EXISTING_CHAPTERS)
        self.assertEqual(existing_id, 1)

    def assert_existing_course_intact(self, conn):
        """已有课程保持原样，且没有失败登记的课程或章节残留。"""
        self.assertEqual(get_course(conn, 1), EXISTING_COURSE)
        self.assertIsNone(get_course(conn, 2))
        self.assertEqual(list_courses(conn), [EXISTING_OVERVIEW_ENTRY])
        self.assert_course_rows(conn, [(1, EXISTING_TITLE)])
        self.assert_chapter_rows(conn, EXISTING_CHAPTER_ROWS)

    def test_failed_registration_preserves_existing_course(self):
        """第二章写入失败后，已有课程不变，同一连接与重开连接均无残留。"""
        self.install_second_chapter_fault(self.conn)

        self.assert_add_sample_fails_with_integrity_error(self.conn)

        # 失败后同一连接仍可使用：已有课程原样保留，无本次残留
        self.assert_existing_course_intact(self.conn)

        # 关闭并重开同一文件，以独立连接确认回滚已落库
        self.reopen_connection()
        self.assert_existing_course_intact(self.conn)

    def test_next_registration_gets_id_two_after_fault_removed(self):
        """故障解除后登记固定样例得到编号 2，已有课程不受影响。"""
        self.install_second_chapter_fault(self.conn)
        self.assert_add_sample_fails_with_integrity_error(self.conn)

        # 撤销测试故障后在同一连接上再次登记，证明连接仍可继续写入
        self.remove_second_chapter_fault(self.conn)
        course_id = add_course(self.conn, SAMPLE_TITLE, SAMPLE_CHAPTERS)

        self.assertEqual(course_id, 2)
        self.assertEqual(get_course(self.conn, 2), sample_course(2))
        self.assertEqual(get_course(self.conn, 1), EXISTING_COURSE)
        self.assertEqual(
            list_courses(self.conn),
            [EXISTING_OVERVIEW_ENTRY, sample_overview_entry(2)],
        )
        self.assert_course_rows(
            self.conn, [(1, EXISTING_TITLE), (2, SAMPLE_TITLE)]
        )
        self.assert_chapter_rows(
            self.conn, EXISTING_CHAPTER_ROWS + sample_chapter_rows(2)
        )

        # 关闭并重开同一文件，结果保持一致
        self.reopen_connection()
        self.assertEqual(get_course(self.conn, 2), sample_course(2))
        self.assertEqual(get_course(self.conn, 1), EXISTING_COURSE)
        self.assertEqual(
            list_courses(self.conn),
            [EXISTING_OVERVIEW_ENTRY, sample_overview_entry(2)],
        )
        self.assert_course_rows(
            self.conn, [(1, EXISTING_TITLE), (2, SAMPLE_TITLE)]
        )
        self.assert_chapter_rows(
            self.conn, EXISTING_CHAPTER_ROWS + sample_chapter_rows(2)
        )


if __name__ == "__main__":
    unittest.main()
