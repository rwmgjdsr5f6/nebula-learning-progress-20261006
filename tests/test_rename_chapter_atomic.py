"""单个章节改名写入失败（事务回滚）的持久化回归测试。

直接调用 course_progress.core.rename_chapter，验证一次改名的名称写入要么
整体提交、要么整体撤销：用测试触发器在课程 1 的“学习”改名为“实践”的
UPDATE 已经执行、事务尚未提交时制造 SQLite 约束失败。触发器挂在
AFTER UPDATE 上且按 NEW.course_id/NEW.position/NEW.name 精确限定，因此
只有目标行确实写入了新名称之后故障才会发生——属于名称写入中途失败而非
写入前的输入校验失败：传给 rename_chapter 的原名称存在、新名称非空且
不与同课程其他章节重名，正常情况下必然走到名称 UPDATE。

rename_chapter 必须原样抛出 sqlite3.IntegrityError（不返回成功详情、
不伪装成 ValidationError 或 None，异常消息措辞不绑定 SQLite 版本），且
事务回滚：同一连接与关闭重开后的独立连接上，课程 1 仍为准备、学习、
回顾，编号、标题、章节数与保存的 position 与操作前完全一致，课程 2 与
全部课程概览也保持原值，没有半完成的改名残留。撤销测试故障后在原连接
重试同一合法改名应成功并持久化，课程 2 的“学习”始终不被改名。

每个用例使用独立的临时 SQLite 文件，课程样例通过 add_course 登记入口
建立，结束后关闭连接并自动清理，不读取或覆盖仓库中已有的业务数据库；
整组测试只依赖 Python 标准库，可由 ``python -m unittest discover``
发现并执行。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from course_progress import add_course, connect, get_course, list_courses
from course_progress.core import ValidationError, rename_chapter

# 固定样例：编号 1 为“入门培训”，章节依次为准备、学习、回顾；编号 2 为
# “进阶培训”，仅有学习一章（与课程 1 的目标章节改名前同名）。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "进阶培训"
SECOND_CHAPTERS = ["学习"]

# 改名目标与结果：课程 1 中间章节“学习”改为“实践”。原名称存在，新名称
# 非空且不与同课程的准备、回顾重名，因此失败不可能来自输入校验。
TARGET_CHAPTER = "学习"
NEW_NAME = "实践"

# 测试故障：课程 1 的 position = 1 章节被 UPDATE 为“实践”之后（AFTER
# UPDATE 只在行更新已经发生后触发），事务提交之前，触发器主动中止。
# 此时名称写入已经在本次事务内生效但尚未提交，rename_chapter 的
# ``with conn:`` 必须整体回滚，而不是留下半完成的名称变更。WHEN 三重
# 限定使故障只针对本次操作：其他课程（含课程 2 的“学习”）、其他位置与
# 其他新名称的改名都不受影响。DROP TRIGGER 即可可靠撤销；异常消息措辞
# 由测试自己设定，也不断言其内容。
FAULT_TRIGGER_SQL = """
CREATE TRIGGER fail_course_one_rename_to_practice
AFTER UPDATE ON chapters
WHEN NEW.course_id = 1
 AND NEW.position = 1
 AND NEW.name = '实践'
BEGIN
    SELECT RAISE(ABORT, '测试故障：名称写入后提交前中止');
END
"""
DROP_FAULT_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS fail_course_one_rename_to_practice"
)

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
RENAMED_FIRST_COURSE = {
    "course_id": 1,
    "title": FIRST_TITLE,
    "chapters": ["准备", NEW_NAME, "回顾"],
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
    (2, 0, "学习"),
]
# 故障解除、重试成功后应有的全部行：仅课程 1 的 position = 1 名称变为
# “实践”，position 仍连续为 0、1、2，课程 2 保持原样。
RENAMED_CHAPTER_ROWS = [
    (1, 0, "准备"),
    (1, 1, NEW_NAME),
    (1, 2, "回顾"),
    (2, 0, "学习"),
]


class RenameChapterAtomicTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，两门固定课程与一个默认连接。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "rename_atomic.db"
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
        """设置测试故障：课程 1“学习”改名为“实践”的名称写入后中止事务。"""
        self.conn.execute(FAULT_TRIGGER_SQL)

    def remove_fault(self):
        """撤销测试故障，恢复课程 1 的正常写入。"""
        self.conn.execute(DROP_FAULT_TRIGGER_SQL)

    def assert_rename_raises_integrity_error(self):
        """合法改名在名称写入后抛出 sqlite3.IntegrityError：不返回成功
        详情，也不把数据库错误改成 ValidationError 或 None；异常消息的
        具体措辞不作限定。"""
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            rename_chapter(self.conn, 1, TARGET_CHAPTER, NEW_NAME)
        self.assertIs(type(ctx.exception), sqlite3.IntegrityError)
        self.assertNotIsInstance(ctx.exception, ValidationError)

    def assert_chapter_rows(self, conn, expected):
        """直接读取 chapters 表，按 (course_id, position, name) 整体核对，
        确认保存的章节位置与名称，没有半完成的改名残留。"""
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

    def assert_state_renamed(self, conn):
        """课程 1 变为准备、实践、回顾，课程 2 与其余数据保持原样。"""
        self.assertEqual(get_course(conn, 1), RENAMED_FIRST_COURSE)
        self.assertEqual(get_course(conn, 2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(list_courses(conn), ORIGINAL_OVERVIEW)
        self.assert_chapter_rows(conn, RENAMED_CHAPTER_ROWS)

    def test_failed_rename_rolls_back_partial_writes(self):
        """名称写入后、提交前约束失败：本次 UPDATE 整体回滚，同连接与
        重开均无半完成的名称变更。"""
        self.install_fault()

        self.assert_rename_raises_integrity_error()

        # 失败后同一连接仍可使用：课程 1 编号、标题、三章及原顺序不变，
        # 中间章节仍为“学习”，概览章节数仍为 3；课程 2 详情与章节数、
        # 章节名保持原值
        self.assert_state_unchanged(self.conn)

        # 关闭并重开同一文件，以独立连接确认回滚已落库：没有任何改名被
        # 提交，保存的 position 仍为 0、1、2，与操作前完全一致
        self.reopen_connection()
        self.assert_state_unchanged(self.conn)

    def test_rename_succeeds_after_fault_removed(self):
        """故障解除后在原连接重试同一合法改名：成功返回完整详情并持久化。"""
        self.install_fault()
        self.assert_rename_raises_integrity_error()

        # 失败后连接继续可用：撤销测试故障后在同一连接上重试同一改名
        self.remove_fault()
        detail = rename_chapter(self.conn, 1, TARGET_CHAPTER, NEW_NAME)

        # 返回与 get_course 相同结构的课程详情，章节依次为准备、实践、回顾；
        # 课程编号、标题与章节数量保持不变
        self.assertEqual(detail, get_course(self.conn, 1))
        self.assert_state_renamed(self.conn)

        # 关闭并重开同一文件，查询结果与成功返回内容一致；课程 2 的
        # “学习”仍未改名
        self.reopen_connection()
        self.assert_state_renamed(self.conn)


if __name__ == "__main__":
    unittest.main()
