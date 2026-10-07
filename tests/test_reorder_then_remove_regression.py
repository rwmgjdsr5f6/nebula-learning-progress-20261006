"""先重排再删除的组合流程回归测试。

针对章节重排与删除共用同一套章节顺序保存规则的重构，沿既有
reorder-chapters / remove-chapter 子进程测试的风格，补充两条命令
先后作用于同一课程的组合用例：编号 1“入门培训”原有准备、学习、回顾
三章，先重排为回顾、准备、学习，再删除准备，最终详情应只剩回顾、
学习；重新打开同一数据库后结果一致。

同时覆盖：两条命令成功时均输出与 get-course 同结构的一行 JSON
（退出码 0、标准错误为空）；删除的是重排后定位到的章节，剩余章节
相对顺序不变且 position 重新连续（此后追加仍位于末尾）；允许在组合
中按当前顺序提交重排；课程编号、标题、其他课程、学员与报名关系保持
原值；组合状态下再删已不存在的章节报章节不存在、删除唯一章节报课程
至少保留一个章节，均退出 1、标准输出为空、无堆栈且数据不变。

每个用例使用独立的临时 SQLite 文件，结束后自动清理，不读取或覆盖
仓库中的 data/course.db。
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
SECOND_CHAPTERS = ["学习"]

NEW_ORDER = ["回顾", "准备", "学习"]
REMOVED_TARGET = "准备"
FINAL_CHAPTERS = ["回顾", "学习"]

ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_CHAPTER_NOT_FOUND = "章节不存在"
ERR_LAST_CHAPTER = "课程至少保留一个章节"


def run_cli(db_path, *args):
    """以独立进程运行 CLI，返回 CompletedProcess。"""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "course_progress",
            "--db",
            str(db_path),
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def chapter_args(chapters):
    """把章节名列表展开为成对的 --chapter 参数。"""
    return [arg for name in chapters for arg in ("--chapter", name)]


class ReorderThenRemoveTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，含两门固定课程、一名学员及其报名。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "combo.db"
        self.assertEqual(
            self.register_course(FIRST_TITLE, FIRST_CHAPTERS), 1
        )
        self.assertEqual(
            self.register_course(SECOND_TITLE, SECOND_CHAPTERS), 2
        )
        # 学员 1 报名课程 1，用于确认组合操作不触碰学员与报名关系
        self.assertEqual(self.register_learner("学员甲"), 1)
        enroll = run_cli(
            self.db_path, "enroll-learner", "1", "--course", "1"
        )
        self.assertEqual(enroll.returncode, 0, enroll.stderr)

    def register_course(self, title, chapters):
        """通过公开 CLI 登记一门课程，断言成功并返回新课程编号。"""
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

    def register_learner(self, name):
        """通过公开 CLI 登记一名学员，断言成功并返回新学员编号。"""
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["learner_id"]

    def get_course(self, course_id):
        """新进程查询课程详情，断言调用成功并返回解析后的 JSON。"""
        result = run_cli(self.db_path, "get-course", str(course_id))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def get_overview(self):
        """新进程查询课程概览，断言调用成功并返回课程列表。"""
        result = run_cli(self.db_path, "list-courses")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["courses"]

    def get_roster(self, course_id):
        """新进程查询课程报名名册，断言成功并返回学员列表。"""
        result = run_cli(
            self.db_path, "list-course-learners", str(course_id)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["learners"]

    def assert_course_detail_success(self, result, course_id, title, chapters):
        """成功结果：退出码 0、标准错误为空、一行与 get-course 同结构 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(result.stdout),
            {"course_id": course_id, "title": title, "chapters": chapters},
        )

    def reorder(self, course_id, chapters):
        """执行重排并返回 CompletedProcess。"""
        return run_cli(
            self.db_path,
            "reorder-chapters",
            str(course_id),
            *chapter_args(chapters),
        )

    def remove(self, course_id, chapter):
        """执行删除并返回 CompletedProcess。"""
        return run_cli(
            self.db_path,
            "remove-chapter",
            str(course_id),
            "--chapter",
            chapter,
        )

    def test_reorder_then_remove_acceptance_persists(self):
        """验收：重排为回顾、准备、学习后删除准备，最终只剩回顾、学习。"""
        reordered = self.reorder(1, NEW_ORDER)
        self.assert_course_detail_success(
            reordered, 1, FIRST_TITLE, NEW_ORDER
        )

        removed = self.remove(1, REMOVED_TARGET)
        self.assert_course_detail_success(
            removed, 1, FIRST_TITLE, FINAL_CHAPTERS
        )

        # 删除进程已退出，新进程重新打开同一数据库，结果一致
        self.assertEqual(
            self.get_course(1),
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": FINAL_CHAPTERS,
            },
        )

    def test_remove_targets_chapter_after_reorder(self):
        """删除按重排后的名称定位：准备位于中间，剩余首尾两章相对顺序不变。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        removed = self.remove(1, REMOVED_TARGET)
        self.assert_course_detail_success(
            removed, 1, FIRST_TITLE, FINAL_CHAPTERS
        )
        # 首尾两章保持重排后的先后：回顾在前、学习在后
        self.assertEqual(self.get_course(1)["chapters"], ["回顾", "学习"])

    def test_append_after_combined_ops_lands_at_end(self):
        """组合操作后追加章节仍位于末尾，证明剩余 position 已重新连续。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        self.assertEqual(self.remove(1, REMOVED_TARGET).returncode, 0)

        appended = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "测验"
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)
        self.assertEqual(appended.stderr, "")
        self.assertEqual(
            json.loads(appended.stdout),
            {"course_id": 1, "chapter_count": 3},
        )
        self.assertEqual(
            self.get_course(1)["chapters"], ["回顾", "学习", "测验"]
        )

    def test_submit_current_order_between_ops_succeeds(self):
        """组合中按当前顺序提交重排仍成功，内容不变，随后删除照常生效。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        # 已处于新顺序后再原样提交一次
        same_order = self.reorder(1, NEW_ORDER)
        self.assert_course_detail_success(
            same_order, 1, FIRST_TITLE, NEW_ORDER
        )
        self.assertEqual(self.get_course(1)["chapters"], NEW_ORDER)

        self.assertEqual(self.remove(1, REMOVED_TARGET).returncode, 0)
        self.assertEqual(self.get_course(1)["chapters"], FINAL_CHAPTERS)

    def test_combined_ops_leave_other_data_untouched(self):
        """课程编号、标题、其他课程、学员与报名关系在组合后保持原值。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        self.assertEqual(self.remove(1, REMOVED_TARGET).returncode, 0)

        # 编号 1 编号与标题不变，仅章节变为回顾、学习
        self.assertEqual(
            self.get_course(1),
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": FINAL_CHAPTERS,
            },
        )
        # 编号 2 完全不变
        self.assertEqual(
            self.get_course(2),
            {
                "course_id": 2,
                "title": SECOND_TITLE,
                "chapters": SECOND_CHAPTERS,
            },
        )
        # 概览仅课程 1 章节数由 3 变为 2，编号与标题均不变
        self.assertEqual(
            self.get_overview(),
            [
                {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 2},
                {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
            ],
        )
        # 学员与报名关系不变
        self.assertEqual(
            self.get_roster(1),
            [{"learner_id": 1, "name": "学员甲"}],
        )
        learner = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(learner.returncode, 0, learner.stderr)
        self.assertEqual(
            json.loads(learner.stdout), {"learner_id": 1, "name": "学员甲"}
        )

    def assert_failure_unchanged(self, result, message, expected_chapters):
        """组合状态下的失败：退出 1、空标准输出、无堆栈，数据保持不变。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), message)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.get_course(1)["chapters"], expected_chapters)

    def test_remove_again_after_combined_remove_reports_not_found(self):
        """组合删除后再删准备：报章节不存在，剩余两章不变。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        self.assertEqual(self.remove(1, REMOVED_TARGET).returncode, 0)

        self.assert_failure_unchanged(
            self.remove(1, REMOVED_TARGET),
            ERR_CHAPTER_NOT_FOUND,
            FINAL_CHAPTERS,
        )

    def test_remove_last_remaining_chapter_rejected(self):
        """组合删到只剩一章后再删：报课程至少保留一个章节，该章保留。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        self.assertEqual(self.remove(1, "准备").returncode, 0)
        self.assertEqual(self.remove(1, "学习").returncode, 0)
        self.assertEqual(self.get_course(1)["chapters"], ["回顾"])

        self.assert_failure_unchanged(
            self.remove(1, "回顾"),
            ERR_LAST_CHAPTER,
            ["回顾"],
        )

    def test_blank_chapter_after_combo_reports_empty(self):
        """组合状态下空白章节名仍先报章节不能为空，数据不变。"""
        self.assertEqual(self.reorder(1, NEW_ORDER).returncode, 0)
        self.assertEqual(self.remove(1, REMOVED_TARGET).returncode, 0)

        self.assert_failure_unchanged(
            self.remove(1, "   "),
            ERR_EMPTY_CHAPTER,
            FINAL_CHAPTERS,
        )


if __name__ == "__main__":
    unittest.main()
