"""向课程末尾追加章节（append-chapter）公开语义的回归测试。

只覆盖 append-chapter 这一条流程：成功追加后的跨进程持久化（新章节排在
最后、章节名去首尾空白保存、标题/编号/此前章节不变、其他课程不受影响、
--db 写在子命令前后两种形式），以及编号格式错误、课程不存在、章节名为
空、章节名重复等失败的退出码、标准错误消息、失败后的数据保持与多错误
并存时的报告顺序。

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

# 固定合成课程：编号 1 有两章，编号 2 有一章且与编号 1 的首章同名。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习"]
SECOND_TITLE = "Python  基础"
SECOND_CHAPTERS = ["准备"]

# 追加的章节名首尾各带一个空格，保存时应去除；内部双空格保留。
NEW_CHAPTER_PADDED = " 回顾 "
NEW_CHAPTER = "回顾"
INNER_SPACES_CHAPTER_PADDED = "  阶段  复习  "
INNER_SPACES_CHAPTER = "阶段  复习"

BAD_IDS = ["0", "-3", "abc"]
UNKNOWN_IDS = ["42", "9223372036854775808"]
BLANK_CHAPTER = "   "

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_DUP_CHAPTER = "章节名重复"

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
        {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 2},
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


class AppendChapterTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与两门固定课程，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "append.db"
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

    def assert_append_success(self, raw_id, chapter, expected_count, db_first=True):
        """追加成功：退出码 0、标准错误为空、标准输出为一行指定 JSON。"""
        result = run_cli(
            self.db_path,
            "append-chapter",
            str(raw_id),
            "--chapter",
            chapter,
            db_first=db_first,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出只有一行 JSON，按解析后的内容比较，不限定键序或空格
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload,
            {"course_id": int(raw_id), "chapter_count": expected_count},
        )
        return payload

    def assert_append_failure(self, argv, exit_code, message):
        """追加失败：退出码、空标准输出、唯一标准错误消息，并保持数据。"""
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

    def test_append_persists_at_end_with_name_stripped(self):
        """正常路径：追加后跨进程读取，新章节去空白后排在最后，其余不变。"""
        self.assert_append_success(1, NEW_CHAPTER_PADDED, 3)

        # 追加进程已退出，新的调用确认编号 1 只在末尾多了去空白后的章节
        first = self.get_course(1)
        self.assertEqual(
            first,
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": [*FIRST_CHAPTERS, NEW_CHAPTER],
            },
        )

        # 编号 2 内容完全不变（即使其章节与新章节无关）
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

        # 概览中编号 1 章节数变为 3，编号 2 仍为 1
        overview = self.get_overview()
        self.assertEqual(
            overview,
            {
                "courses": [
                    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 3},
                    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
                ]
            },
        )

    def test_append_preserves_inner_spaces_and_case(self):
        """章节名内部空白与大小写原样保留，仅去除首尾空白。"""
        self.assert_append_success(1, INNER_SPACES_CHAPTER_PADDED, 3)
        self.assertEqual(
            self.get_course(1)["chapters"],
            [*FIRST_CHAPTERS, INNER_SPACES_CHAPTER],
        )

    def test_append_successively_appends_in_order(self):
        """连续追加两个章节，按追加顺序排在原列表之后。"""
        self.assert_append_success(1, NEW_CHAPTER_PADDED, 3)
        self.assert_append_success(1, INNER_SPACES_CHAPTER_PADDED, 4)
        self.assertEqual(
            self.get_course(1)["chapters"],
            [*FIRST_CHAPTERS, NEW_CHAPTER, INNER_SPACES_CHAPTER],
        )
        self.assertEqual(
            self.get_overview()["courses"][0]["chapter_count"], 4
        )

    def test_db_option_after_subcommand(self):
        """--db 写在 append-chapter 之后同样成功并持久化。"""
        self.assert_append_success(1, NEW_CHAPTER_PADDED, 3, db_first=False)
        self.assertEqual(
            self.get_course(1)["chapters"], [*FIRST_CHAPTERS, NEW_CHAPTER]
        )
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

    def test_same_name_in_other_course_does_not_conflict(self):
        """其他课程已有同名章节不影响追加。"""
        # 编号 1 已有章节“学习”，编号 2 没有，向编号 2 追加“学习”仍应成功
        self.assert_append_success(2, "学习", 2)
        self.assertEqual(
            self.get_course(2)["chapters"], [*SECOND_CHAPTERS, "学习"]
        )
        self.assertEqual(self.get_course(1), ORIGINAL_FIRST_COURSE)

    def test_bad_course_id_format(self):
        """编号 0、-3、abc：退出码 2，报编号必须为正整数，数据不变。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_append_failure(
                    ("append-chapter", raw_id, "--chapter", NEW_CHAPTER),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_course_id(self):
        """未知正整数编号（含超出 int64 范围者）：退出码 1，报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_append_failure(
                    ("append-chapter", raw_id, "--chapter", NEW_CHAPTER),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_missing_chapter_option(self):
        """已有课程未提供 --chapter：退出码 1，报章节不能为空，数据不变。"""
        self.assert_append_failure(
            ("append-chapter", "1"), 1, ERR_EMPTY_CHAPTER
        )

    def test_blank_chapter_for_existing_course(self):
        """已有课程的章节名仅含空白：退出码 1，报章节不能为空，数据不变。"""
        self.assert_append_failure(
            ("append-chapter", "1", "--chapter", BLANK_CHAPTER),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_duplicate_chapter_rejected(self):
        """与已有章节重名（含仅首尾空白差异）：退出码 1，报章节名重复。"""
        for chapter in ("准备", " 准备 ", "学习"):
            with self.subTest(chapter=chapter):
                self.assert_append_failure(
                    ("append-chapter", "1", "--chapter", chapter),
                    1,
                    ERR_DUP_CHAPTER,
                )

    def test_duplicate_comparison_is_case_sensitive(self):
        """重名比较大小写敏感：大小写不同的英文名可追加。"""
        register_course(self.db_path, "英语", ["Reading"])
        self.assert_append_success(3, "reading", 2)
        self.assertEqual(
            self.get_course(3)["chapters"], ["Reading", "reading"]
        )

    def test_repeated_submission_fails_as_duplicate(self):
        """成功追加后重复提交同一名称，按重名失败处理。"""
        self.assert_append_success(1, NEW_CHAPTER_PADDED, 3)
        result = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", NEW_CHAPTER
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_DUP_CHAPTER)
        # 首次追加的结果保持，未再追加
        self.assertEqual(
            self.get_course(1)["chapters"], [*FIRST_CHAPTERS, NEW_CHAPTER]
        )

    def test_bad_id_reported_before_other_errors(self):
        """非法编号同时配缺失/重复章节：只报编号格式错误。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_append_failure(
                    ("append-chapter", raw_id), 2, ERR_BAD_ID
                )
                self.assert_append_failure(
                    ("append-chapter", raw_id, "--chapter", "准备"),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_id_reported_before_blank_or_duplicate_chapter(self):
        """未知编号同时配空/重复章节名：只报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_append_failure(
                    ("append-chapter", raw_id), 1, ERR_NOT_FOUND
                )
                self.assert_append_failure(
                    ("append-chapter", raw_id, "--chapter", BLANK_CHAPTER),
                    1,
                    ERR_NOT_FOUND,
                )
                self.assert_append_failure(
                    ("append-chapter", raw_id, "--chapter", "准备"),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_empty_chapter_reported_before_duplicate(self):
        """章节名为空白时只报章节不能为空，不进入重名判定。"""
        self.assert_append_failure(
            ("append-chapter", "1", "--chapter", BLANK_CHAPTER),
            1,
            ERR_EMPTY_CHAPTER,
        )


if __name__ == "__main__":
    unittest.main()
