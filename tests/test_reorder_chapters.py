"""章节重排（reorder-chapters）公开语义的回归测试。

只覆盖 reorder-chapters 这一条流程：成功重排后的跨进程持久化（仅章节
顺序变化、名称去首尾空白匹配、课程编号/标题/章节名称与数量不变、其他
课程不受影响、--db 写在子命令前后两种形式、按原顺序重复提交仍成功、
后续追加置于新顺序末尾），以及编号格式错误、课程不存在、章节为空、
章节名重复、列表与现有章节不一致等失败的退出码、标准错误消息、失败
后的数据保持与多错误并存时的报告顺序。

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

# 固定合成课程：编号 1 有三章，编号 2 有一章且与编号 1 的首章同名。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "Python  基础"
SECOND_CHAPTERS = ["准备"]

BAD_IDS = ["0", "-3", "abc"]
UNKNOWN_IDS = ["42", "9223372036854775808"]
BLANK_NAME = "   "

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_DUP_CHAPTER = "章节名重复"
ERR_MISMATCH = "章节列表与现有章节不一致"

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


def chapter_args(chapters):
    """把章节名列表展开为成对的 --chapter 参数。"""
    return [arg for name in chapters for arg in ("--chapter", name)]


class ReorderChaptersTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与两门固定课程，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "reorder.db"
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

    def assert_reorder_success(
        self, raw_id, chapters, expected_chapters, db_first=True
    ):
        """重排成功：退出码 0、标准错误为空、标准输出为一行课程详情 JSON。"""
        result = run_cli(
            self.db_path,
            "reorder-chapters",
            str(raw_id),
            *chapter_args(chapters),
            db_first=db_first,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出只有一行 JSON，结构与 get-course 相同
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload,
            {
                "course_id": int(raw_id),
                "title": FIRST_TITLE,
                "chapters": expected_chapters,
            },
        )
        return payload

    def assert_reorder_failure(self, argv, exit_code, message):
        """重排失败：退出码、空标准输出、唯一标准错误消息，并保持数据。"""
        result = run_cli(self.db_path, *argv)
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

    def test_reorder_persists_across_processes(self):
        """验收路径：重排后跨进程读取，仅章节顺序变化。"""
        new_order = ["回顾", "准备", "学习"]
        self.assert_reorder_success(1, new_order, new_order)

        # 重排进程已退出，新的调用确认编号 1 的章节为新顺序
        self.assertEqual(
            self.get_course(1),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": new_order},
        )

        # 编号 2 内容完全不变
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

        # 概览中章节数不变
        self.assertEqual(self.get_overview(), ORIGINAL_OVERVIEW)

    def test_reorder_strips_surrounding_whitespace(self):
        """章节名去除首尾空白后匹配，保存的仍是原名称，内部空白保留。"""
        register_course(self.db_path, "空白课程", ["阶段  复习", "总结"])
        self.assertEqual(
            json.loads(
                run_cli(
                    self.db_path,
                    "reorder-chapters",
                    "3",
                    "--chapter",
                    " 总结 ",
                    "--chapter",
                    "  阶段  复习  ",
                ).stdout
            )["chapters"],
            ["总结", "阶段  复习"],
        )

    def test_reorder_matching_is_case_sensitive(self):
        """名称匹配大小写敏感：大小写不同的名称视为未知名称。"""
        register_course(self.db_path, "英语", ["Reading", "Writing"])
        result = run_cli(
            self.db_path,
            "reorder-chapters",
            "3",
            "--chapter",
            "reading",
            "--chapter",
            "Writing",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_MISMATCH)

    def test_same_order_submission_succeeds(self):
        """按原顺序重复提交也成功，内容不变。"""
        self.assert_reorder_success(1, FIRST_CHAPTERS, FIRST_CHAPTERS)
        self.assert_state_unchanged()

    def test_db_option_after_subcommand(self):
        """--db 写在 reorder-chapters 之后同样成功并持久化。"""
        new_order = ["学习", "回顾", "准备"]
        self.assert_reorder_success(1, new_order, new_order, db_first=False)
        self.assertEqual(self.get_course(1)["chapters"], new_order)
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

    def test_append_after_reorder_goes_to_new_end(self):
        """重排后追加的章节置于新顺序末尾。"""
        new_order = ["回顾", "准备", "学习"]
        self.assert_reorder_success(1, new_order, new_order)
        result = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "测验"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            self.get_course(1)["chapters"], [*new_order, "测验"]
        )

    def test_bad_course_id_format(self):
        """编号 0、-3、abc：退出码 2，报编号必须为正整数，数据不变。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_reorder_failure(
                    (
                        "reorder-chapters",
                        raw_id,
                        *chapter_args(FIRST_CHAPTERS),
                    ),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_course_id(self):
        """未知正整数编号（含超出 int64 范围者）：退出码 1，报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_reorder_failure(
                    (
                        "reorder-chapters",
                        raw_id,
                        *chapter_args(FIRST_CHAPTERS),
                    ),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_missing_chapter_option(self):
        """已有课程未提供 --chapter：退出码 1，报章节不能为空，数据不变。"""
        self.assert_reorder_failure(
            ("reorder-chapters", "1"), 1, ERR_EMPTY_CHAPTER
        )

    def test_blank_chapter_for_existing_course(self):
        """任一名称仅含空白：退出码 1，报章节不能为空，数据不变。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                "准备",
                "--chapter",
                BLANK_NAME,
                "--chapter",
                "回顾",
            ),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_duplicate_chapter_rejected(self):
        """去空白后名称重复（含仅首尾空白差异）：报章节名重复。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                "准备",
                "--chapter",
                " 准备 ",
                "--chapter",
                "回顾",
            ),
            1,
            ERR_DUP_CHAPTER,
        )

    def test_missing_existing_chapter(self):
        """遗漏现有章节：报章节列表与现有章节不一致。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                "准备",
                "--chapter",
                "学习",
            ),
            1,
            ERR_MISMATCH,
        )

    def test_unknown_chapter_name(self):
        """包含未知名称（数量相同但改名）：报章节列表与现有章节不一致。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                "准备",
                "--chapter",
                "学习",
                "--chapter",
                "预习",
            ),
            1,
            ERR_MISMATCH,
        )

    def test_extra_chapter_name(self):
        """数量多于现有章节：报章节列表与现有章节不一致。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                *chapter_args(FIRST_CHAPTERS),
                "--chapter",
                "测验",
            ),
            1,
            ERR_MISMATCH,
        )

    def test_bad_id_reported_before_other_errors(self):
        """非法编号同时配空/重复章节：只报编号格式错误。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_reorder_failure(
                    ("reorder-chapters", raw_id), 2, ERR_BAD_ID
                )
                self.assert_reorder_failure(
                    (
                        "reorder-chapters",
                        raw_id,
                        "--chapter",
                        BLANK_NAME,
                        "--chapter",
                        BLANK_NAME,
                    ),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_id_reported_before_chapter_errors(self):
        """未知编号同时配空/重复章节：只报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_reorder_failure(
                    ("reorder-chapters", raw_id), 1, ERR_NOT_FOUND
                )
                self.assert_reorder_failure(
                    (
                        "reorder-chapters",
                        raw_id,
                        "--chapter",
                        BLANK_NAME,
                        "--chapter",
                        BLANK_NAME,
                    ),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_empty_chapter_reported_before_duplicate(self):
        """空名称与重复名称并存：只报章节不能为空。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                BLANK_NAME,
                "--chapter",
                "准备",
                "--chapter",
                "准备",
            ),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_duplicate_reported_before_mismatch(self):
        """重复名称与未知名称并存：只报章节名重复。"""
        self.assert_reorder_failure(
            (
                "reorder-chapters",
                "1",
                "--chapter",
                "准备",
                "--chapter",
                "准备",
                "--chapter",
                "预习",
            ),
            1,
            ERR_DUP_CHAPTER,
        )


if __name__ == "__main__":
    unittest.main()
