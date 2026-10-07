"""删除单个章节写入中途失败（原子性）的回归测试。

直接调用 course_progress.core.remove_chapter，验证一次删除的全部写入只能
整体生效：用测试触发器在课程 1 删除“学习”后按剩余顺序落位的最后一章
（回顾落到 position = 1）写入时制造 SQLite 约束失败。此时整体平移、目标
章节删除与前一章（准备落到 position = 0）落位已经在同一事务内执行，属于
写入中途失败而非输入校验失败——提交给 remove_chapter 的章节名本身始终
合法。

remove_chapter 必须原样抛出 sqlite3.IntegrityError（不返回成功详情、
不伪装成 ValidationError，异常消息措辞不绑定 SQLite 版本），且事务回滚：
同一连接与关闭重开后的独立连接上，课程 1 仍为准备、学习、回顾，编号、
标题、章节数与保存的 position 与操作前完全一致，课程 2 与全部课程概览
也保持原值，没有部分删除状态残留。撤销测试故障后在原连接重试同一合法
删除应成功并持久化。

每个用例使用独立的临时 SQLite 文件，课程样例通过 add_course 登记入口
建立，结束后关闭连接并自动清理，不接触仓库中已有的业务数据库；整组
测试只依赖 Python 标准库。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError, remove_chapter

# 固定样例：编号 1 为“入门培训”，章节依次为准备、学习、回顾；编号 2 为
# “复习培训”，仅有学习一章（与课程 1 的目标章节同名）。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "复习培训"
SECOND_CHAPTERS = ["学习"]

# 删除目标：课程 1 的中间章节。
TARGET = "学习"

# 测试故障：课程 1 的“回顾”落到 position = 1（本次删除最后一次写入）时
# 被触发器主动中止。remove_chapter 先把课程 1 全部 position 整体平移到
# 3、4、5，再删掉目标章节（学习，平移后的 position = 4），然后按剩余顺序
# 逐章落位：准备落到 0、回顾落到 1。触发器在回顾落位时才拒绝，平移、删除
# 与准备的落位均已在同一事务内发生，因此这是写入中途的约束失败，而不是
# 输入校验失败。WHEN 限定 course_id = 1，故障只针对目标课程，课程 2 不受
# 影响。DROP TRIGGER 即可可靠撤销；异常消息措辞由测试自己设定，也不断言
# 其内容。
FAULT_TRIGGER_SQL = """
CREATE TRIGGER fail_last_chapter_landing
BEFORE UPDATE ON chapters
WHEN NEW.course_id = 1 AND NEW.name = '回顾' AND NEW.position = 1
BEGIN
    SELECT RAISE(ABORT, '测试故障：拒绝课程 1 最后一章落位');
END
"""
DROP_FAULT_TRIGGER_SQL = "DROP TRIGGER IF EXISTS fail_last_chapter_landing"

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
REMOVED_FIRST_COURSE = {
    "course_id": 1,
    "title": FIRST_TITLE,
    "chapters": ["准备", "回顾"],
}
ORIGINAL_OVERVIEW = [
    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 3},
    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
]
REMOVED_OVERVIEW = [
    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 2},
    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
]

# 操作前 chapters 表应有的全部行（按 course_id、position 升序）。
ORIGINAL_CHAPTER_ROWS = [
    (1, 0, "准备"),
    (1, 1, "学习"),
    (1, 2, "回顾"),
    (2, 0, "学习"),
]
# 故障解除、重试成功后应有的全部行：课程 1 只剩准备、回顾且 position
# 连续为 0、1，课程 2 保持原样。
REMOVED_CHAPTER_ROWS = [
    (1, 0, "准备"),
    (1, 1, "回顾"),
    (2, 0, "学习"),
]


class RemoveChapterAtomicTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，两门固定课程与一个默认连接。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "remove_atomic.db"
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
        """关闭当前连接并重新连接同一文件，以独立连接确认回滚或提交已
        落库，不仅凭同一连接内存中的读取判定。"""
        self.conn.close()
        self.conn = self.open_connection()
        return self.conn

    def install_fault(self):
        """设置测试故障：课程 1 最后一次落位写入被约束拒绝。"""
        self.conn.execute(FAULT_TRIGGER_SQL)

    def remove_fault(self):
        """撤销测试故障，恢复课程 1 的正常写入。"""
        self.conn.execute(DROP_FAULT_TRIGGER_SQL)

    def assert_remove_raises_integrity_error(self):
        """合法章节名的删除在写入中途抛出 sqlite3.IntegrityError：
        不返回成功详情，也不把数据库错误改成 ValidationError；异常消息的
        具体措辞不作限定。"""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            remove_chapter(self.conn, 1, TARGET)
        self.assertIs(type(ctx.exception), sqlite3.IntegrityError)
        self.assertNotIsInstance(ctx.exception, ValidationError)

    def assert_chapter_rows(self, conn, expected):
        """直接读取 chapters 表，按 (course_id, position, name) 整体核对，
        确认保存的章节位置与名称，没有部分删除状态残留。"""
        rows = conn.execute(
            "SELECT course_id, position, name FROM chapters"
            " ORDER BY course_id, position"
        ).fetchall()
        self.assertEqual(rows, expected)

    def assert_state_unchanged(self, conn):
        """两门课程详情、全部课程概览与保存的章节行均与操作前一致。"""
        self.assertEqual(get_course(conn, 1), ORIGINAL_FIRST_COURSE)
        self.assertEqual(get_course(conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(conn, ORIGINAL_CHAPTER_ROWS)

    def assert_state_removed(self, conn):
        """课程 1 只剩准备、回顾，课程 2 与其余数据保持原样。"""
        self.assertEqual(get_course(conn, 1), REMOVED_FIRST_COURSE)
        self.assertEqual(get_course(conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(conn), REMOVED_OVERVIEW)
        self.assert_chapter_rows(conn, REMOVED_CHAPTER_ROWS)

    def test_failed_remove_rolls_back_partial_writes(self):
        """写入中途约束失败：平移、删除与已落位写入全部回滚，同连接与
        重开均无残留。"""
        self.install_fault()

        self.assert_remove_raises_integrity_error()

        # 失败后同一连接仍可使用：课程 1 编号、标题、三章及原顺序不变，
        # 概览章节数仍为 3，课程 2 详情与章节数保持原值
        self.assert_state_unchanged(self.conn)

        # 关闭并重开同一文件，以独立连接确认回滚已落库，保存的 position
        # 仍为 0、1、2，与操作前完全一致
        self.reopen_connection()
        self.assert_state_unchanged(self.conn)

    def test_remove_succeeds_after_fault_removed(self):
        """故障解除后在原连接重试同一合法删除：成功返回完整详情并持久化。"""
        self.install_fault()
        self.assert_remove_raises_integrity_error()

        # 撤销测试故障后在同一连接上重试，证明连接仍可继续写入
        self.remove_fault()
        detail = remove_chapter(self.conn, 1, TARGET)

        # 返回课程 1 的完整详情，章节变为准备、回顾
        self.assertEqual(detail, REMOVED_FIRST_COURSE)
        self.assert_state_removed(self.conn)

        # 关闭并重开同一文件，结果保持一致
        self.reopen_connection()
        self.assert_state_removed(self.conn)


if __name__ == "__main__":
    unittest.main()
