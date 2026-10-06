"""按编号修改课程标题（rename-course）公开行为的回归测试。

以 README 的公开语义为准，通过子进程调用 ``python -m course_progress``
操作独立的临时 SQLite 文件，验证：

- 成功修改后标准输出为一行 JSON，退出码 0，标准错误为空；
- 修改进程退出后，新的调用确认仅标题变化，章节名称、数量、顺序不变，
  另一门课程不受影响，同标题课程分别列出且不合并；
- 改为相同标题同样成功；``--db`` 写在子命令前后等价；
- 各类失败（非法编号、未知编号、空白新标题及多错误并存时的优先级）
  的退出码与标准错误消息，且失败后数据库内容保持不变。

每个用例使用独立临时目录与固定合成课程，结束后自动清理，
不读取或覆盖用户数据库，不依赖网络或外部服务。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定合成课程：编号 1 含三章，编号 2 标题内部含两个空格、仅一章。
COURSE_ONE_TITLE = "入门培训"
COURSE_ONE_CHAPTERS = ["准备", "学习", "回顾"]
COURSE_TWO_TITLE = "Python  基础"
COURSE_TWO_CHAPTERS = ["实践"]

# 新标题带首尾空白，去空白后与编号 2 的课程同名（内部双空格保留）。
NEW_TITLE_RAW = "  Python  基础  "
NEW_TITLE = "Python  基础"

EXPECTED_COURSE_ONE = {
    "course_id": 1,
    "title": COURSE_ONE_TITLE,
    "chapters": COURSE_ONE_CHAPTERS,
}
EXPECTED_COURSE_TWO = {
    "course_id": 2,
    "title": COURSE_TWO_TITLE,
    "chapters": COURSE_TWO_CHAPTERS,
}
EXPECTED_OVERVIEW = {
    "courses": [
        {"course_id": 1, "title": COURSE_ONE_TITLE, "chapter_count": 3},
        {"course_id": 2, "title": COURSE_TWO_TITLE, "chapter_count": 1},
    ]
}

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_TITLE = "课程标题不能为空"


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


def add_course(db_path, title, chapters, expected_id):
    """登记一门课程并断言成功且编号符合预期。"""
    chapter_args = [arg for name in chapters for arg in ("--chapter", name)]
    result = run_cli(
        db_path, "add-course", "--title", title, *chapter_args
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"course_id": expected_id}


def rename_course(db_path, course_id, title, db_first=True):
    return run_cli(
        db_path, "rename-course", course_id, "--title", title, db_first=db_first
    )


class RenameTestCase(unittest.TestCase):
    """每个用例一个独立临时目录，并预先登记两门固定合成课程。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        add_course(self.db_path, COURSE_ONE_TITLE, COURSE_ONE_CHAPTERS, 1)
        add_course(self.db_path, COURSE_TWO_TITLE, COURSE_TWO_CHAPTERS, 2)

    def read_state(self):
        """通过新的独立调用读取两门课程详情与概览（JSON 按内容解析）。"""
        got_one = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(got_one.returncode, 0)
        self.assertEqual(got_one.stderr, "")
        got_two = run_cli(self.db_path, "get-course", "2")
        self.assertEqual(got_two.returncode, 0)
        self.assertEqual(got_two.stderr, "")
        listed = run_cli(self.db_path, "list-courses")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(listed.stderr, "")
        return (
            json.loads(got_one.stdout),
            json.loads(got_two.stdout),
            json.loads(listed.stdout),
        )

    def assert_failure(self, result, exit_code, message):
        """失败时退出码与消息符合约定，标准输出为空，无异常堆栈。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def assert_state_matches(self, state):
        """当前数据库内容与给定快照一致。"""
        self.assertEqual(self.read_state(), state)


class TestRenameSuccess(RenameTestCase):
    def test_rename_updates_only_title_and_persists(self):
        before = self.read_state()
        self.assertEqual(
            before, (EXPECTED_COURSE_ONE, EXPECTED_COURSE_TWO, EXPECTED_OVERVIEW)
        )

        renamed = rename_course(self.db_path, "1", NEW_TITLE_RAW)
        self.assertEqual(renamed.returncode, 0)
        self.assertEqual(renamed.stderr, "")
        # 标准输出只有一行 JSON，按解析后的内容比较，不限定键序或空格
        self.assertEqual(len(renamed.stdout.splitlines()), 1)
        self.assertEqual(
            json.loads(renamed.stdout), {"course_id": 1, "title": NEW_TITLE}
        )

        # 修改进程退出后，新的调用确认：编号 1 只改标题，编号 2 不变，
        # 两门同名课程分别列出，概览章节数仍为 3 和 1
        course_one, course_two, overview = self.read_state()
        self.assertEqual(
            course_one,
            {
                "course_id": 1,
                "title": NEW_TITLE,
                "chapters": COURSE_ONE_CHAPTERS,
            },
        )
        self.assertEqual(course_two, EXPECTED_COURSE_TWO)
        self.assertEqual(
            overview,
            {
                "courses": [
                    {"course_id": 1, "title": NEW_TITLE, "chapter_count": 3},
                    {"course_id": 2, "title": COURSE_TWO_TITLE, "chapter_count": 1},
                ]
            },
        )

    def test_rename_to_same_title_succeeds(self):
        renamed = rename_course(self.db_path, "1", COURSE_ONE_TITLE)
        self.assertEqual(renamed.returncode, 0)
        self.assertEqual(renamed.stderr, "")
        self.assertEqual(
            json.loads(renamed.stdout),
            {"course_id": 1, "title": COURSE_ONE_TITLE},
        )
        self.assert_state_matches(
            (EXPECTED_COURSE_ONE, EXPECTED_COURSE_TWO, EXPECTED_OVERVIEW)
        )

    def test_db_option_after_subcommand(self):
        renamed = rename_course(self.db_path, "1", NEW_TITLE_RAW, db_first=False)
        self.assertEqual(renamed.returncode, 0)
        self.assertEqual(renamed.stderr, "")
        self.assertEqual(
            json.loads(renamed.stdout), {"course_id": 1, "title": NEW_TITLE}
        )

        got = run_cli(self.db_path, "get-course", "1", db_first=False)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(got.stderr, "")
        self.assertEqual(
            json.loads(got.stdout),
            {
                "course_id": 1,
                "title": NEW_TITLE,
                "chapters": COURSE_ONE_CHAPTERS,
            },
        )


class TestRenameFailures(RenameTestCase):
    def assert_failed_rename_keeps_state(self, course_id, title, exit_code, message):
        """失败的修改不改变任何课程内容与概览。"""
        before = self.read_state()
        result = rename_course(self.db_path, course_id, title)
        self.assert_failure(result, exit_code, message)
        self.assert_state_matches(before)

    def test_non_positive_and_non_numeric_ids(self):
        for raw in ("0", "-3", "abc"):
            with self.subTest(course_id=raw):
                self.assert_failed_rename_keeps_state(raw, "新标题", 2, ERR_BAD_ID)

    def test_unknown_ids(self):
        for raw in ("42", "9223372036854775808"):
            with self.subTest(course_id=raw):
                self.assert_failed_rename_keeps_state(raw, "新标题", 1, ERR_NOT_FOUND)

    def test_blank_new_title_on_existing_course(self):
        for blank in ("   ", "\t \n"):
            with self.subTest(title=blank):
                self.assert_failed_rename_keeps_state("1", blank, 1, ERR_EMPTY_TITLE)

    def test_bad_id_with_blank_title_reports_id_error_only(self):
        for raw in ("0", "-3", "abc"):
            with self.subTest(course_id=raw):
                self.assert_failed_rename_keeps_state(raw, "   ", 2, ERR_BAD_ID)

    def test_unknown_id_with_blank_title_reports_not_found_only(self):
        for raw in ("42", "9223372036854775808"):
            with self.subTest(course_id=raw):
                self.assert_failed_rename_keeps_state(raw, "   ", 1, ERR_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
