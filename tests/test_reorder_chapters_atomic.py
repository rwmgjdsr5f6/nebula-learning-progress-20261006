"""章节重排写入中途失败（原子性）的回归测试。

直接调用 course_progress.core.reorder_chapters，验证一次重排的全部位置
写入只能整体生效：用测试触发器在课程 1 按“回顾、准备、学习”落位的最后
一章（学习落到 position = 2）写入时制造 SQLite 约束失败。此时整体平移与
前两章落位已经在同一事务内执行，属于写入中途失败而非输入校验失败——
提交给 reorder_chapters 的章节列表本身始终合法。

reorder_chapters 必须原样抛出 sqlite3.IntegrityError（不返回成功详情、
不伪装成 ValidationError，异常消息措辞不绑定 SQLite 版本），且事务回滚：
同一连接与关闭重开后的独立连接上，课程 1 仍为准备、学习、回顾，编号、
标题、章节数与保存的 position 与操作前完全一致，课程 2 与全部课程概览
也保持原值，没有部分重排状态残留。撤销测试故障后在原连接重试同一合法
顺序应成功并持久化。测试故障只针对课程 1，故障期间课程 2 的重排不受
影响。

每个用例使用独立的临时 SQLite 文件，结束后关闭连接并自动清理，
不接触仓库中已有的业务数据库；整组测试只依赖 Python 标准库。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError, reorder_chapters

# 固定样例：编号 1 为“入门培训”，章节依次为准备、学习、回顾；编号 2 为
# 另一课程，仅有准备一章（与课程 1 的首章同名）。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "Python  基础"
SECOND_CHAPTERS = ["准备"]

# 课程 1 请求的新顺序，是与现有章节集合一致的合法列表。
NEW_ORDER = ["回顾", "准备", "学习"]

# 测试故障：课程 1 的“学习”落到 position = 2（本次重排最后一次写入）时
# 被触发器主动中止。reorder_chapters 先把课程 1 全部 position 整体平移到
# 3、4、5，再逐章按 0、1、2 落位；触发器在第三次落位时才拒绝，平移与前
# 两次落位均已在同一事务内发生，因此这是写入中途的约束失败，而不是输入
# 校验失败。WHEN 限定 course_id = 1，故障只针对目标课程，课程 2 不受影响。
# DROP TRIGGER 即可可靠撤销；异常消息措辞由测试自己设定，也不断言其内容。
FAULT_TRIGGER_SQL = """
CREATE TRIGGER fail_last_chapter_landing
BEFORE UPDATE ON chapters
WHEN NEW.course_id = 1 AND NEW.name = '学习' AND NEW.position = 2
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
REORDERED_FIRST_COURSE = {
    "course_id": 1,
    "title": FIRST_TITLE,
    "chapters": list(NEW_ORDER),
}
ORIGINAL_OVERVIEW = [
    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 3},
    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
]

# 操作前 chapters 表应有的全部行（按 course_id、position 升序）。
ORIGINAL_CHAPTER_ROWS = [
    (1, 0, "准备"),
    (1, 1, "学习"),
    (1, 2, "回顾"),
    (2, 0, "准备"),
]
# 故障解除、重试成功后应有的全部行：仅课程 1 的 name 随 position 变化，
# position 仍连续为 0、1、2，课程 2 保持原样。
REORDERED_CHAPTER_ROWS = [
    (1, 0, "回顾"),
    (1, 1, "准备"),
    (1, 2, "学习"),
    (2, 0, "准备"),
]


class ReorderChaptersAtomicTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，两门固定课程与一个默认连接。"""

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

    def assert_reorder_raises_integrity_error(self):
        """合法新顺序的重排在写入中途抛出 sqlite3.IntegrityError：
        不返回成功详情，也不把数据库错误改成 ValidationError；异常消息的
        具体措辞不作限定。"""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            reorder_chapters(self.conn, 1, NEW_ORDER)
        self.assertIs(type(ctx.exception), sqlite3.IntegrityError)
        self.assertNotIsInstance(ctx.exception, ValidationError)

    def assert_chapter_rows(self, conn, expected):
        """直接读取 chapters 表，按 (course_id, position, name) 整体核对，
        确认保存的章节位置与名称，没有部分重排状态残留。"""
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

    def assert_state_reordered(self, conn):
        """课程 1 为新顺序，课程 2、概览章节数与其余数据保持原样。"""
        self.assertEqual(get_course(conn, 1), REORDERED_FIRST_COURSE)
        self.assertEqual(get_course(conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(conn, REORDERED_CHAPTER_ROWS)

    def test_failed_reorder_rolls_back_partial_writes(self):
        """写入中途约束失败：部分位置变更全部回滚，同连接与重开均无残留。"""
        self.install_fault()

        self.assert_reorder_raises_integrity_error()

        # 失败后同一连接仍可使用：课程 1 编号、标题、章节顺序与数量不变，
        # 课程 2 详情与全部课程概览保持原值
        self.assert_state_unchanged(self.conn)

        # 关闭并重开同一文件，以独立连接确认回滚已落库，保存的 position
        # 与操作前完全一致
        self.reopen_connection()
        self.assert_state_unchanged(self.conn)

    def test_reorder_succeeds_after_fault_removed(self):
        """故障解除后在原连接重试同一合法顺序：成功返回完整详情并持久化。"""
        self.install_fault()
        self.assert_reorder_raises_integrity_error()

        # 撤销测试故障后在同一连接上重试，证明连接仍可继续写入
        self.remove_fault()
        detail = reorder_chapters(self.conn, 1, NEW_ORDER)

        # 返回课程 1 的完整详情，章节依次为回顾、准备、学习
        self.assertEqual(detail, REORDERED_FIRST_COURSE)
        self.assert_state_reordered(self.conn)

        # 关闭并重开同一文件，结果保持一致
        self.reopen_connection()
        self.assertEqual(get_course(self.conn, 1), REORDERED_FIRST_COURSE)
        self.assert_state_reordered(self.conn)

    def test_fault_only_targets_first_course(self):
        """故障只针对课程 1：故障设置期间课程 2 的重排仍成功且数据不变。"""
        self.install_fault()

        # 课程 2 只有一章，按原顺序提交是合法重排；任何写入的
        # NEW.course_id 均为 2，不满足触发器条件
        detail = reorder_chapters(self.conn, 2, SECOND_CHAPTERS)
        self.assertEqual(detail, ORIGINAL_SECOND_COURSE)

        # 课程 1 未受影响，课程 2 与全部行保持操作前状态
        self.assert_state_unchanged(self.conn)


if __name__ == "__main__":
    unittest.main()
