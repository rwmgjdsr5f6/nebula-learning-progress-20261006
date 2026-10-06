"""删除单个已有章节（remove-chapter）公开语义的回归测试。

只覆盖 remove-chapter 这一条流程：成功删除后的跨进程持久化（目标章节按
去首尾空白后的名称以大小写敏感精确匹配定位、剩余章节名称与相对顺序不
变、课程编号/标题不变、其他课程不受影响、--db 写在子命令前后两种形
式），删除后追加章节落在末尾，以及编号格式错误、课程不存在、章节名为
空、章节不存在、删除唯一章节等失败的退出码、标准错误消息、失败后的数
据保持与多错误并存时的报告顺序。

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

# 固定合成课程：编号 1 有三章，编号 2 只有一章且与编号 1 的中间章同名。
FIRST_TITLE = "入门培训"
FIRST_CHAPTERS = ["准备", "学习", "回顾"]
SECOND_TITLE = "复习培训"
SECOND_CHAPTERS = ["学习"]

# 删除用的章节名首尾各带空白，定位时应去除；内部双空格保留。
CHAPTER_PADDED = " 学习 "
INNER_SPACES_CHAPTER_PADDED = "  阶段  复习  "
INNER_SPACES_CHAPTER = "阶段  复习"

BAD_IDS = ["0", "-3", "abc"]
UNKNOWN_IDS = ["42", "9223372036854775808"]
BLANK_NAME = "   "

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_CHAPTER_NOT_FOUND = "章节不存在"
ERR_LAST_CHAPTER = "课程至少保留一个章节"

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


class RemoveChapterTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与两门固定课程，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "remove.db"
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

    def assert_remove_success(
        self, raw_id, chapter, expected_chapters, expected_title=None,
        db_first=True,
    ):
        """删除成功：退出码 0、标准错误为空、标准输出为一行课程详情 JSON。"""
        if expected_title is None:
            expected_title = FIRST_TITLE if int(raw_id) == 1 else SECOND_TITLE
        result = run_cli(
            self.db_path,
            "remove-chapter",
            str(raw_id),
            "--chapter",
            chapter,
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
                "title": expected_title,
                "chapters": expected_chapters,
            },
        )
        return payload

    def assert_remove_failure(self, argv, exit_code, message):
        """删除失败：退出码、空标准输出、唯一标准错误消息，并保持数据。"""
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

    def test_remove_middle_chapter_persists_with_relative_order(self):
        """验收路径：删除中间章后跨进程读取，剩余章节名称与相对顺序不变。"""
        self.assert_remove_success(1, CHAPTER_PADDED, ["准备", "回顾"])

        # 删除进程已退出，新的调用确认编号 1 只剩首尾两章且顺序不变
        self.assertEqual(
            self.get_course(1),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": ["准备", "回顾"]},
        )

        # 编号 2 虽然也有“学习”，但内容完全不变
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

        # 概览中编号 1 章节数变为 2，编号 2 仍为 1
        self.assertEqual(
            self.get_overview(),
            {
                "courses": [
                    {"course_id": 1, "title": FIRST_TITLE, "chapter_count": 2},
                    {"course_id": 2, "title": SECOND_TITLE, "chapter_count": 1},
                ]
            },
        )

    def test_remove_first_and_last_chapter_preserves_order(self):
        """连续删除首章与末章：每次剩余章节的相对顺序都保持。"""
        self.assert_remove_success(1, "准备", ["学习", "回顾"])
        self.assert_remove_success(1, "回顾", ["学习"])
        self.assertEqual(
            self.get_course(1),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": ["学习"]},
        )

    def test_remove_preserves_inner_spaces_and_is_case_sensitive(self):
        """定位按大小写敏感精确匹配；命中的章节名内部空白原样保留于剩余列表。"""
        register_course(
            self.db_path,
            "英语",
            ["Reading", INNER_SPACES_CHAPTER, "Writing"],
        )
        self.assert_remove_success(
            3,
            INNER_SPACES_CHAPTER_PADDED,
            ["Reading", "Writing"],
            expected_title="英语",
        )
        self.assertEqual(
            self.get_course(3)["chapters"], ["Reading", "Writing"]
        )
        # 大小写不同不视为同一章节：失败且课程 3 保持两章
        result = run_cli(
            self.db_path, "remove-chapter", "3", "--chapter", "reading"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.rstrip("\r\n"), ERR_CHAPTER_NOT_FOUND
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(
            self.get_course(3)["chapters"], ["Reading", "Writing"]
        )

    def test_db_option_after_subcommand(self):
        """--db 写在 remove-chapter 之后同样成功并持久化。"""
        self.assert_remove_success(
            1, CHAPTER_PADDED, ["准备", "回顾"], db_first=False
        )
        self.assertEqual(
            self.get_course(1)["chapters"], ["准备", "回顾"]
        )
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

    def test_append_after_remove_goes_after_last_remaining_chapter(self):
        """删除中间章后再追加，新章节位于剩余最后一章（回顾）之后。"""
        self.assert_remove_success(1, CHAPTER_PADDED, ["准备", "回顾"])
        result = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "测验"
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            self.get_course(1),
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": ["准备", "回顾", "测验"],
            },
        )

    def test_repeated_remove_same_name_fails_as_not_found(self):
        """删除成功后再次提交同名删除，按章节不存在处理，结果保持。"""
        self.assert_remove_success(1, CHAPTER_PADDED, ["准备", "回顾"])
        result = run_cli(
            self.db_path, "remove-chapter", "1", "--chapter", CHAPTER_PADDED
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_CHAPTER_NOT_FOUND)
        self.assertEqual(
            self.get_course(1),
            {"course_id": 1, "title": FIRST_TITLE, "chapters": ["准备", "回顾"]},
        )
        self.assertEqual(
            self.get_overview()["courses"][0]["chapter_count"], 2
        )

    def test_remove_only_chapter_rejected_and_kept(self):
        """删除课程 2 唯一的章节失败：退出码 1，报至少保留一章，章节保留。"""
        self.assert_remove_failure(
            ("remove-chapter", "2", "--chapter", "学习"),
            1,
            ERR_LAST_CHAPTER,
        )
        # 课程 2 仍是单章“学习”
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)
        self.assertEqual(
            self.get_overview()["courses"][1]["chapter_count"], 1
        )

    def test_bad_course_id_format(self):
        """编号 0、-3、abc：退出码 2，报编号必须为正整数，数据不变。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_remove_failure(
                    ("remove-chapter", raw_id, "--chapter", "学习"),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_course_id(self):
        """未知正整数编号（含超出 int64 范围者）：退出码 1，报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_remove_failure(
                    ("remove-chapter", raw_id, "--chapter", "学习"),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_missing_chapter_option(self):
        """已有课程未提供 --chapter：退出码 1，报章节不能为空，数据不变。"""
        self.assert_remove_failure(
            ("remove-chapter", "1"),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_blank_chapter_for_existing_course(self):
        """已有课程的章节名仅含空白：退出码 1，报章节不能为空，数据不变。"""
        self.assert_remove_failure(
            ("remove-chapter", "1", "--chapter", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_unknown_chapter(self):
        """名称非空但该课程没有此章节：退出码 1，报章节不存在，数据不变。"""
        self.assert_remove_failure(
            ("remove-chapter", "1", "--chapter", "不存在的章节"),
            1,
            ERR_CHAPTER_NOT_FOUND,
        )

    def test_other_courses_same_named_chapter_is_not_a_match(self):
        """其他课程的同名章节不算命中：课程 1 删完“学习”后课程 2 的仍在。"""
        self.assert_remove_success(1, "学习", ["准备", "回顾"])
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

    def test_bad_id_reported_before_other_errors(self):
        """非法编号同时配缺失/空章节：只报编号格式错误。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_remove_failure(
                    ("remove-chapter", raw_id), 2, ERR_BAD_ID
                )
                self.assert_remove_failure(
                    ("remove-chapter", raw_id, "--chapter", "学习"),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_id_reported_before_chapter_errors(self):
        """未知编号同时配缺失/空/唯一章节情形：只报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_remove_failure(
                    ("remove-chapter", raw_id), 1, ERR_NOT_FOUND
                )
                self.assert_remove_failure(
                    ("remove-chapter", raw_id, "--chapter", BLANK_NAME),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_empty_chapter_reported_before_unknown_and_last(self):
        """章节名为空白时只报章节不能为空，不进入存在性与唯一章节判定。"""
        self.assert_remove_failure(
            ("remove-chapter", "1", "--chapter", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )
        # 课程 2 只有一章且名称为空白：仍只报章节不能为空
        self.assert_remove_failure(
            ("remove-chapter", "2", "--chapter", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_unknown_chapter_reported_before_last_chapter_guard(self):
        """唯一章节课程给出未知名称：先报章节不存在，不报至少保留一章。"""
        self.assert_remove_failure(
            ("remove-chapter", "2", "--chapter", "不存在的章节"),
            1,
            ERR_CHAPTER_NOT_FOUND,
        )


if __name__ == "__main__":
    unittest.main()
