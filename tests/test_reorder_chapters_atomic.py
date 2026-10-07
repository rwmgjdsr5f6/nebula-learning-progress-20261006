"""章节重排写入中途失败（原子性）的回归测试。

直接调用 course_progress.core 的 reorder_chapters，并以 connect、add_course、
get_course、list_courses 核对结果，验证一次重排只能整体生效：用测试触发器
在目标课程已有部分位置变更后拒绝后续写入（此时整体平移与首个章节落位已
进入本次写入），reorder_chapters 必须抛出 sqlite3.IntegrityError——不返回
成功详情、也不把数据库错误伪装成 ValidationError——且本次全部位置变更
回滚：同一连接与关闭重开后的独立连接读到的课程详情、章节位置、其他课程
与全部课程概览均与操作前一致，没有部分重排状态残留。另覆盖故障解除后的
恢复：撤销测试故障后在原连接重试同一合法顺序，重排整体成功并跨连接持久化。

每个用例使用独立的临时 SQLite 文件，样例课程与测试故障都只存在于该文件
内，用例结束后关闭连接并清理临时目录，不接触仓库中已有数据库。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError, reorder_chapters

# 固定样例：编号 1“入门培训”有三章，编号 2“进阶研讨”仅有一章。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "进阶研讨"
SECOND_CHAPTERS = ["准备"]

# 对编号 1 请求的合法新顺序（通过全部输入校验，失败只能来自写入本身）。
NEW_ORDER = ["回顾", "准备", "学习"]

ORIGINAL_FIRST_COURSE = {
    "course_id": 1,
    "title": FIRST_TITLE,
    "chapters": list(FIRST_CHAPTERS),
}
ORIGINAL_SECOND_COURSE = {
    "course_id": 2,
    "title": SECOND_TITLE,
    "chapters": list(SECOND_CHAPTERS),
}
ORIGINAL_OVERVIEW = [
    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 3},
    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
]
# 操作前 chapters 表的全部行（按 course_id、position 升序）。
ORIGINAL_CHAPTER_ROWS = [
    (1, position, name) for position, name in enumerate(FIRST_CHAPTERS)
] + [(2, 0, SECOND_CHAPTERS[0])]

# 测试故障：重排先把目标课程的 position 整体平移（3、4、5），再按新顺序
# 逐章落位（回顾→0、准备→1、学习→2）。触发器只在目标课程（course_id = 1）
# 的章节落位到 position = 1 时中止本次写入，此时平移与“回顾”落位已完成，
# 即失败发生在写入中途而非输入校验阶段；编号 2 的章节不受影响。
# DROP TRIGGER 即可可靠撤销，异常消息措辞由测试自己设定，不依赖 SQLite
# 版本相关的内置措辞。
FAULT_TRIGGER_SQL = """
CREATE TRIGGER fail_target_course_reorder
BEFORE UPDATE ON chapters
WHEN NEW.course_id = 1 AND NEW.position = 1
BEGIN
    SELECT RAISE(ABORT, '测试故障：拒绝目标章节落位');
END
"""
DROP_FAULT_TRIGGER_SQL = "DROP TRIGGER IF EXISTS fail_target_course_reorder"


class ReorderChaptersAtomicTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与两门固定课程，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "reorder_atomic.db"
        self.conn = self.open_connection()
        self.assertEqual(
            add_course(self.conn, FIRST_TITLE, FIRST_CHAPTERS), 1
        )
        self.assertEqual(
            add_course(self.conn, SECOND_TITLE, SECOND_CHAPTERS), 2
        )

    def open_connection(self):
        """打开当前临时数据库并注册关闭清理（清理按注册逆序执行，
        因此连接先于临时目录关闭）。"""
        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def reopen_connection(self):
        """关闭当前连接并重新连接同一文件，以独立连接确认落库结果，
        不仅凭内存中的连接状态判定回滚完成。"""
        self.conn.close()
        self.conn = self.open_connection()
        return self.conn

    def install_reorder_fault(self, conn):
        """设置测试故障：目标课程的章节落位到 position = 1 时被拒绝。"""
        conn.execute(FAULT_TRIGGER_SQL)

    def remove_reorder_fault(self, conn):
        """撤销测试故障，恢复章节正常写入。"""
        conn.execute(DROP_FAULT_TRIGGER_SQL)

    def assert_reorder_fails_with_integrity_error(self, conn):
        """合法新顺序的重排抛出 sqlite3.IntegrityError：不返回课程详情，
        也不把数据库错误改成 ValidationError；异常消息的具体措辞随
        SQLite 版本而定，不作限定。"""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            reorder_chapters(conn, 1, NEW_ORDER)
        self.assertIs(type(ctx.exception), sqlite3.IntegrityError)
        self.assertNotIsInstance(ctx.exception, ValidationError)

    def assert_chapter_rows(self, conn, expected):
        """直接读取 chapters 表，按 (course_id, position, name) 整体核对，
        确认保存的章节位置与操作前完全一致，没有部分重排状态残留。"""
        rows = conn.execute(
            "SELECT course_id, position, name FROM chapters"
            " ORDER BY course_id, position"
        ).fetchall()
        self.assertEqual(rows, expected)

    def assert_original_state(self, conn):
        """两门课程的详情、全部课程概览与章节行均与操作前一致。"""
        self.assertEqual(get_course(conn, 1), ORIGINAL_FIRST_COURSE)
        self.assertEqual(get_course(conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(conn, ORIGINAL_CHAPTER_ROWS)

    def test_failed_reorder_leaves_no_partial_state(self):
        """写入中途失败后，同一连接与重开连接均查不到任何部分重排残留。"""
        self.install_reorder_fault(self.conn)

        self.assert_reorder_fails_with_integrity_error(self.conn)

        # 失败后同一连接仍可使用：编号 1 的编号、标题、章节顺序与章节数
        # 保持原值，编号 2 与全部课程概览不变，章节位置无部分变更残留
        self.assert_original_state(self.conn)

        # 关闭并重开同一文件，以独立连接确认回滚已落库
        self.reopen_connection()
        self.assert_original_state(self.conn)

    def test_reorder_succeeds_after_fault_removed(self):
        """故障解除后在原连接重试同一合法顺序：重排整体成功并持久化。"""
        self.install_reorder_fault(self.conn)
        self.assert_reorder_fails_with_integrity_error(self.conn)

        # 撤销测试故障后在同一连接上重试，证明连接仍可继续写入
        self.remove_reorder_fault(self.conn)
        result = reorder_chapters(self.conn, 1, NEW_ORDER)

        # 返回编号 1 的完整详情，章节依次为回顾、准备、学习
        self.assertEqual(
            result,
            {"course_id": 1, "title": FIRST_TITLE, "chapters": NEW_ORDER},
        )
        self.assertEqual(get_course(self.conn, 1), result)
        # 编号 2 保持原样，概览中两门课程的章节数不变
        self.assertEqual(get_course(self.conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(self.conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(
            self.conn,
            [(1, position, name) for position, name in enumerate(NEW_ORDER)]
            + [(2, 0, SECOND_CHAPTERS[0])],
        )

        # 关闭并重开同一文件，以独立连接确认新顺序已落库
        self.reopen_connection()
        self.assertEqual(get_course(self.conn, 1), result)
        self.assertEqual(get_course(self.conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(self.conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(
            self.conn,
            [(1, position, name) for position, name in enumerate(NEW_ORDER)]
            + [(2, 0, SECOND_CHAPTERS[0])],
        )


if __name__ == "__main__":
    unittest.main()
