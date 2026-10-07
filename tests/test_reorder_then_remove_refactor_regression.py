"""重排后删除（reorder-chapters → remove-chapter）组合流程的回归测试。

针对章节顺序保存规则在重排与删除两条路径间集中维护的重构，通过子进程
调用 ``python -m course_progress`` 验证两条命令先后串用时公开行为保持：
以编号 1 的“入门培训”（准备、学习、回顾）为先，先重排为回顾、准备、
学习，再删除准备，最终详情只剩回顾、学习，且重新打开同一数据库后一致；
删除在重排后的位置与名称上生效，其余章节相对顺序沿用重排后的新顺序。

同时核对组合过程中课程编号与标题、其他课程、学员与报名关系保持原值；
重排允许提交当前顺序、删除后追加仍位于末尾；以及第二条命令在第一条
结果之上的失败情形（章节不存在、唯一章节不可删除）退出码、标准错误与
数据保持。每个用例使用独立的临时 SQLite 文件，结束后自动清理，不读取
或覆盖仓库中的 data/course.db。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "复习培训"
SECOND_CHAPTERS = ["总览"]
LEARNER_NAME = "学员甲"

ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_CHAPTER_NOT_FOUND = "章节不存在"
ERR_LAST_CHAPTER = "课程至少保留一个章节"


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


def chapter_args(chapters):
    """把章节名列表展开为成对的 --chapter 参数。"""
    return [arg for name in chapters for arg in ("--chapter", name)]


class ReorderThenRemoveTestCase(unittest.TestCase):
    """每用例一个独立临时目录：课程1有三章，课程2有一章，学员1报名两门课。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "reorder_remove.db"
        self.assertEqual(
            self.register_course(FIRST_TITLE, FIRST_CHAPTERS), 1
        )
        self.assertEqual(
            self.register_course(SECOND_TITLE, SECOND_CHAPTERS), 2
        )
        # 一名学员报名两门课程，用于确认组合操作不动报名关系
        self.assertEqual(self.add_learner(LEARNER_NAME), 1)
        self.assertEqual(self.enroll(1, 1).returncode, 0)
        self.assertEqual(self.enroll(1, 2).returncode, 0)

    def register_course(self, title, chapters):
        result = run_cli(
            self.db_path,
            "add-course",
            "--title",
            title,
            *chapter_args(chapters),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id):
        return run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
        )

    def get_course(self, course_id):
        """新进程查询课程详情，断言成功并返回解析后的 JSON。"""
        result = run_cli(self.db_path, "get-course", str(course_id))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def roster(self, course_id):
        result = run_cli(
            self.db_path, "list-course-learners", str(course_id)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["learners"]

    def learner_courses(self, learner_id):
        result = run_cli(
            self.db_path, "list-learner-courses", str(learner_id)
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["courses"]

    def assert_detail(self, course_id, title, chapters):
        self.assertEqual(
            self.get_course(course_id),
            {"course_id": course_id, "title": title, "chapters": chapters},
        )

    def reorder(self, course_id, chapters, db_first=True):
        return run_cli(
            self.db_path,
            "reorder-chapters",
            str(course_id),
            *chapter_args(chapters),
            db_first=db_first,
        )

    def remove(self, course_id, chapter, db_first=True):
        return run_cli(
            self.db_path,
            "remove-chapter",
            str(course_id),
            "--chapter",
            chapter,
            db_first=db_first,
        )

    def test_acceptance_reorder_then_remove_persists(self):
        """验收：先重排为回顾、准备、学习，再删准备，最终只剩回顾、学习。"""
        new_order = ["回顾", "准备", "学习"]

        reordered = self.reorder(1, new_order)
        self.assertEqual(reordered.returncode, 0)
        self.assertEqual(reordered.stderr, "")
        self.assertEqual(len(reordered.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(reordered.stdout),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": new_order},
        )

        removed = self.remove(1, "准备")
        self.assertEqual(removed.returncode, 0)
        self.assertEqual(removed.stderr, "")
        self.assertEqual(len(removed.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(removed.stdout),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": ["回顾", "学习"]},
        )

        # 重新打开同一数据库（新进程）后结果一致
        self.assert_detail(1, FIRST_TITLE, ["回顾", "学习"])

        # 课程编号、标题不变；章节数随删除变为 2
        self.assertEqual(self.get_course(1)["course_id"], 1)
        overview = run_cli(self.db_path, "list-courses")
        self.assertEqual(overview.returncode, 0)
        self.assertEqual(
            json.loads(overview.stdout)["courses"],
            [
                {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 2},
                {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
            ],
        )

        # 其他课程、学员与报名关系保持原值
        self.assert_detail(2, SECOND_TITLE, SECOND_CHAPTERS)
        self.assertEqual(
            self.roster(1), [{"learner_id": 1, "name": LEARNER_NAME}]
        )
        self.assertEqual(
            self.roster(2), [{"learner_id": 1, "name": LEARNER_NAME}]
        )
        self.assertEqual(
            [course["course_id"] for course in self.learner_courses(1)],
            [1, 2],
        )

    def test_reorder_submit_current_order_then_remove(self):
        """重排允许提交当前顺序；随后删除仍按当前顺序生效并相对排序不变。"""
        # 先提交一次真正的重排，再按“当前顺序”重复提交
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        resubmit = self.reorder(1, ["回顾", "准备", "学习"])
        self.assertEqual(resubmit.returncode, 0)
        self.assertEqual(resubmit.stderr, "")
        self.assertEqual(
            json.loads(resubmit.stdout)["chapters"],
            ["回顾", "准备", "学习"],
        )

        removed = self.remove(1, "学习")
        self.assertEqual(removed.returncode, 0)
        self.assert_detail(1, FIRST_TITLE, ["回顾", "准备"])

    def test_append_after_reorder_and_remove_lands_at_end(self):
        """重排后删除，再追加的章节仍位于末尾。"""
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        self.assertEqual(self.remove(1, "准备").returncode, 0)

        appended = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "测验"
        )
        self.assertEqual(appended.returncode, 0)
        self.assertEqual(appended.stderr, "")
        self.assertEqual(
            json.loads(appended.stdout),
            {"course_id": 1, "chapter_count": 3},
        )
        self.assert_detail(1, FIRST_TITLE, ["回顾", "学习", "测验"])

    def test_remove_after_reorder_uses_new_positions(self):
        """删除在重排后的顺序上定位：原首章“准备”现处中间，删除后首尾保留。"""
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        # 删掉重排后位于中间的“准备”，首尾两章相对顺序不变
        self.assertEqual(self.remove(1, "准备").returncode, 0)
        self.assert_detail(1, FIRST_TITLE, ["回顾", "学习"])
        # 被删章节再次删除按章节不存在处理，数据不再变化
        again = self.remove(1, "准备")
        self.assertEqual(again.returncode, 1)
        self.assertEqual(again.stdout, "")
        self.assertEqual(again.stderr.rstrip("\r\n"), ERR_CHAPTER_NOT_FOUND)
        self.assertNotIn("Traceback", again.stderr)
        self.assert_detail(1, FIRST_TITLE, ["回顾", "学习"])

    def test_remove_each_chapter_after_reorder_leaves_one(self):
        """重排后连删两章：最后一章不可删除，报错且该章保留。"""
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        self.assertEqual(self.remove(1, "准备").returncode, 0)
        self.assertEqual(self.remove(1, "回顾").returncode, 0)
        self.assert_detail(1, FIRST_TITLE, ["学习"])

        last = self.remove(1, "学习")
        self.assertEqual(last.returncode, 1)
        self.assertEqual(last.stdout, "")
        self.assertEqual(last.stderr.rstrip("\r\n"), ERR_LAST_CHAPTER)
        self.assertNotIn("Traceback", last.stderr)
        self.assert_detail(1, FIRST_TITLE, ["学习"])

    def test_failed_remove_after_reorder_keeps_reordered_state(self):
        """重排后删除一个不存在的名称：删除失败但重排结果保留。"""
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        result = self.remove(1, "不存在的章节")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_CHAPTER_NOT_FOUND)
        self.assertNotIn("Traceback", result.stderr)
        # 三章仍为重排后的顺序
        self.assert_detail(1, FIRST_TITLE, ["回顾", "准备", "学习"])

    def test_blank_chapter_after_reorder_reports_empty(self):
        """重排后以空白名称删除：报章节不能为空，重排结果保留。"""
        self.assertEqual(
            self.reorder(1, ["回顾", "准备", "学习"]).returncode, 0
        )
        result = self.remove(1, "   ")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_EMPTY_CHAPTER)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_detail(1, FIRST_TITLE, ["回顾", "准备", "学习"])

    def test_db_option_after_subcommand_throughout(self):
        """组合全程把 --db 写在子命令后同样成功并持久化。"""
        new_order = ["回顾", "准备", "学习"]
        self.assertEqual(
            self.reorder(1, new_order, db_first=False).returncode, 0
        )
        self.assertEqual(
            self.remove(1, "准备", db_first=False).returncode, 0
        )
        self.assert_detail(1, FIRST_TITLE, ["回顾", "学习"])
        self.assert_detail(2, SECOND_TITLE, SECOND_CHAPTERS)


if __name__ == "__main__":
    unittest.main()
