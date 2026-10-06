"""修改课程标题（rename-course）公开语义的回归测试。

只覆盖 rename-course 这一条流程：成功修改后的跨进程持久化（编号不变、
章节名称/数量/顺序不变、与其他课程同标题不合并、再次改为相同标题成功、
--db 写在子命令前后两种形式），以及编号格式错误、课程不存在、新标题为
空白等失败的退出码、标准错误消息与失败后的数据保持。

通过子进程调用 ``python -m course_progress``，每个用例使用独立的临时
SQLite 文件，结束后自动清理，不读取或覆盖仓库中的 data/course.db。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定合成课程：编号 1 有三章，编号 2 的标题中包含两个连续空格、只有实践一章。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "Python  基础"
SECOND_CHAPTERS = ["实践"]

# 新标题首尾各带两个空格，去首尾空白后与编号 2 同标题（内部双空格保留）。
NEW_TITLE_PADDED = "  Python  基础  "
NEW_TITLE = "Python  基础"

BAD_IDS = ["0", "-3", "abc"]
UNKNOWN_IDS = ["42", "9223372036854775808"]
BLANK_TITLE = "   "

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_TITLE = "课程标题不能为空"

ORIGINAL_FIRST_COURSE = {
    "course_id": 1,
    "title": FIRST_TITLE,
    "chapters": FIRST_CHAPTERS,
}
ORIGINAL_SECOND_COURSE = {
    "course_id": 2,
    "title": SECOND_TITLE,
    "chapters": SECOND_CHAPTERS,
}
ORIGINAL_OVERVIEW = {
    "courses": [
        {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 3},
        {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
    ]
}
RENAMED_OVERVIEW = {
    "courses": [
        {"course_id": 1, "title": NEW_TITLE, "chapter_count": 3},
        {"course_id": 2, "title": NEW_TITLE, "chapter_count": 1},
    ]
}


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


def register_course(db_path, title, chapters):
    """通过公开 CLI 登记一门课程，断言成功并返回新课程编号。"""
    chapter_args = [arg for name in chapters for arg in ("--chapter", name)]
    result = run_cli(db_path, "add-course", "--title", title, *chapter_args)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)["course_id"]


class RenameCourseTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与两门固定课程，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "rename.db"
        self.assertEqual(
            register_course(self.db_path, FIRST_TITLE, FIRST_CHAPTERS), 1
        )
        self.assertEqual(
            register_course(self.db_path, SECOND_TITLE, SECOND_CHAPTERS), 2
        )

    def get_course(self, course_id):
        """新进程查询课程详情，断言调用成功并返回解析后的 JSON。"""
        result = run_cli(self.db_path, "get-course", str(course_id))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def get_overview(self):
        """新进程查询课程概览，断言调用成功并返回解析后的 JSON。"""
        result = run_cli(self.db_path, "list-courses")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_rename_success(self, raw_id, title, db_first=True):
        """修改标题成功：退出码 0、标准错误为空、标准输出为一行指定 JSON。"""
        result = run_cli(
            self.db_path,
            "rename-course",
            str(raw_id),
            "--title",
            title,
            db_first=db_first,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出只有一行 JSON，按解析后的内容比较，不限定键序或空格
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload, {"course_id": int(raw_id), "title": title.strip()}
        )
        return payload

    def assert_rename_failure(self, raw_id, title, exit_code, message):
        """修改标题失败：退出码、空标准输出、唯一标准错误消息，并保持数据。"""
        result = run_cli(
            self.db_path, "rename-course", raw_id, "--title", title
        )
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        # 去掉末尾换行后只有对应消息，且不出现异常堆栈
        self.assertEqual(result.stderr.rstrip("\r\n"), message)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_state_unchanged()

    def assert_state_unchanged(self):
        """两门课程的详情与概览均与失败前（登记时）一致。"""
        self.assertEqual(self.get_course(1), ORIGINAL_FIRST_COURSE)
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(self.get_overview(), ORIGINAL_OVERVIEW)

    def test_rename_persists_with_only_title_changed(self):
        """正常路径：改标题后跨进程读取，章节不变，编号 2 不变，同标题分别列出。"""
        self.assert_rename_success(1, NEW_TITLE_PADDED)

        # 修改进程已退出，新的调用确认编号 1 只改标题
        first = self.get_course(1)
        self.assertEqual(first["course_id"], 1)
        self.assertEqual(first["title"], NEW_TITLE)
        self.assertEqual(first["chapters"], FIRST_CHAPTERS)

        # 编号 2 内容完全不变
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

        # 两门同标题课程分别列出，概览章节数仍为 3 和 1
        overview = self.get_overview()
        self.assertEqual(overview, RENAMED_OVERVIEW)
        courses = overview["courses"]
        self.assertEqual([c["course_id"] for c in courses], [1, 2])
        self.assertEqual([c["title"] for c in courses], [NEW_TITLE, NEW_TITLE])
        self.assertEqual([c["chapter_count"] for c in courses], [3, 1])

    def test_rename_to_same_title_succeeds(self):
        """改成与当前相同的标题同样成功，输出与数据保持一致。"""
        self.assert_rename_success(1, NEW_TITLE_PADDED)
        # 再次改为相同标题（仍带首尾空白）仍成功
        self.assert_rename_success(1, NEW_TITLE_PADDED)

        self.assertEqual(self.get_course(1)["title"], NEW_TITLE)
        self.assertEqual(self.get_course(1)["chapters"], FIRST_CHAPTERS)
        self.assertEqual(self.get_overview(), RENAMED_OVERVIEW)

    def test_db_option_after_subcommand(self):
        """--db 写在 rename-course 之后同样成功并持久化。"""
        self.assert_rename_success(1, NEW_TITLE_PADDED, db_first=False)

        self.assertEqual(self.get_course(1)["title"], NEW_TITLE)
        self.assertEqual(self.get_course(1)["chapters"], FIRST_CHAPTERS)
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(self.get_overview(), RENAMED_OVERVIEW)

    def test_bad_course_id_format(self):
        """编号 0、-3、abc：退出码 2，报编号必须为正整数，数据不变。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(raw_id, "新标题", 2, ERR_BAD_ID)

    def test_unknown_course_id(self):
        """未知正整数编号（含超出 int64 范围者）：退出码 1，报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(raw_id, "新标题", 1, ERR_NOT_FOUND)

    def test_blank_title_for_existing_course(self):
        """已有课程的新标题仅含空白：退出码 1，报标题不能为空，标题不变。"""
        self.assert_rename_failure("1", BLANK_TITLE, 1, ERR_EMPTY_TITLE)
        self.assertEqual(self.get_course(1), ORIGINAL_FIRST_COURSE)
        self.assertEqual(self.get_overview(), ORIGINAL_OVERVIEW)

    def test_bad_id_reported_before_blank_title(self):
        """非法编号同时配空标题：只报编号格式错误，不进入标题校验。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    raw_id, BLANK_TITLE, 2, ERR_BAD_ID
                )

    def test_unknown_id_reported_before_blank_title(self):
        """未知编号同时配空标题：只报课程不存在，不进入标题校验。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    raw_id, BLANK_TITLE, 1, ERR_NOT_FOUND
                )


if __name__ == "__main__":
    unittest.main()
