"""回归测试：课程登记后按原顺序读取的完整流程。

通过子进程调用 ``python -m course_progress``，仅验证已公开的命令行行为。
每个用例使用独立的临时数据库目录，执行结束后自动清理，
不依赖公网、预存课程或固定文件路径，可重复执行且结果一致。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


class CourseProgressCliTest(unittest.TestCase):
    """以独立进程调用 CLI，验证登记、读取与失败提示。"""

    def setUp(self):
        # 每个用例一个全新临时目录，数据库互不影响
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(Path(self._tmp.name) / "course.db")

    def run_cli(self, *argv):
        """以独立子进程运行 CLI，返回 CompletedProcess。"""
        return subprocess.run(
            [sys.executable, "-m", "course_progress", *argv],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

    def add_course(self, title, chapters, db_before_subcommand=True):
        """登记课程；db_before_subcommand 控制 --db 写在子命令前或后。"""
        chapter_args = []
        for name in chapters:
            chapter_args += ["--chapter", name]
        if db_before_subcommand:
            argv = ["--db", self.db_path, "add-course", "--title", title]
        else:
            argv = ["add-course", "--db", self.db_path, "--title", title]
        return self.run_cli(*(argv + chapter_args))

    def get_course(self, course_id, db_before_subcommand=True):
        if db_before_subcommand:
            return self.run_cli("--db", self.db_path, "get-course", str(course_id))
        return self.run_cli("get-course", "--db", self.db_path, str(course_id))

    def list_courses(self):
        return self.run_cli("--db", self.db_path, "list-courses")

    def assert_failed_add_leaves_no_trace(self, title, chapters, expected_error):
        """登记失败：退出码 1、stdout 为空、stderr 为指定消息；
        随后数据库中不留任何记录，再登记有效课程仍得到编号 1。"""
        result = self.add_course(title, chapters)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), expected_error)

        # 失败的登记不应在数据库中留下任何记录
        listing = self.list_courses()
        self.assertEqual(listing.returncode, 0)
        self.assertEqual(json.loads(listing.stdout), {"courses": []})

        # 同一数据库再登记有效课程，仍从编号 1 开始
        added = self.add_course("有效课程", ["第一章"])
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        # 详情仅含本次输入的章节
        detail = self.get_course(1)
        self.assertEqual(detail.returncode, 0)
        self.assertEqual(
            json.loads(detail.stdout),
            {"course_id": 1, "title": "有效课程", "chapters": ["第一章"]},
        )

    # ---- 成功流程 ----

    def test_add_then_get_roundtrip_preserves_order(self):
        # --db 写在子命令之前；标题与章节名带首尾空白
        added = self.add_course(
            " Python 入门 ", [" 安装环境 ", "第一段程序"]
        )
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        # 按 JSON 内容比较，不限定字段顺序或空格格式
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        # 另一次独立进程调用读取同一数据库（--db 写在子命令之后），
        # 证明前一次进程退出后数据仍然保留
        detail = self.get_course(1, db_before_subcommand=False)
        self.assertEqual(detail.returncode, 0)
        self.assertEqual(detail.stderr, "")
        self.assertEqual(
            json.loads(detail.stdout),
            {
                "course_id": 1,
                "title": "Python 入门",
                "chapters": ["安装环境", "第一段程序"],
            },
        )

    def test_db_option_after_subcommand_for_add(self):
        # 登记时 --db 写在子命令之后同样有效
        added = self.add_course(
            " Python 入门 ",
            [" 安装环境 ", "第一段程序"],
            db_before_subcommand=False,
        )
        self.assertEqual(added.returncode, 0)
        self.assertEqual(added.stderr, "")
        self.assertEqual(json.loads(added.stdout), {"course_id": 1})

        detail = self.get_course(1)
        self.assertEqual(detail.returncode, 0)
        self.assertEqual(
            json.loads(detail.stdout),
            {
                "course_id": 1,
                "title": "Python 入门",
                "chapters": ["安装环境", "第一段程序"],
            },
        )

    # ---- 登记失败 ----

    def test_add_blank_title_rejected(self):
        self.assert_failed_add_leaves_no_trace(
            "   ", ["安装环境"], "课程标题不能为空"
        )

    def test_add_without_chapters_rejected(self):
        self.assert_failed_add_leaves_no_trace("Python 入门", [], "章节不能为空")

    def test_add_blank_chapter_name_rejected(self):
        self.assert_failed_add_leaves_no_trace(
            "Python 入门", ["安装环境", "  "], "章节不能为空"
        )

    def test_add_duplicate_chapter_after_strip_rejected(self):
        self.assert_failed_add_leaves_no_trace(
            "Python 入门", [" 安装环境 ", "安装环境"], "章节名重复"
        )

    def test_title_error_reported_before_chapter_error(self):
        # 标题错误与章节错误同时存在时只报告标题错误
        self.assert_failed_add_leaves_no_trace("  ", [], "课程标题不能为空")

    def test_empty_chapter_reported_before_duplicate(self):
        # 空章节与重复同时存在时只报告空章节
        self.assert_failed_add_leaves_no_trace(
            "Python 入门", ["", "安装环境", "安装环境"], "章节不能为空"
        )

    # ---- 查询失败 ----

    def test_get_unknown_id_reports_not_found(self):
        result = self.get_course(999)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "课程不存在")

    def test_get_zero_id_reports_bad_id(self):
        result = self.get_course(0)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "课程编号必须为正整数")


if __name__ == "__main__":
    unittest.main()
