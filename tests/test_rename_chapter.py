"""单个已有章节改名（rename-chapter）公开语义的回归测试。

只覆盖 rename-chapter 这一条流程：成功改名后的跨进程持久化（仅目标章节
名称被替换、两个名称去首尾空白保存、课程编号/标题/章节数量与顺序不变、
其他课程不受影响、--db 写在子命令前后两种形式、改为当前名称仍成功且不
新增章节），以及编号格式错误、课程不存在、原章节名为空、章节不存在、
新名称为空、章节名重复等失败的退出码、标准错误消息、失败后的数据保持
与多错误并存时的报告顺序。

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

# 改名用的原名称与新名称首尾各带空白，保存时应去除；内部双空格保留。
OLD_CHAPTER_PADDED = " 学习 "
NEW_CHAPTER_PADDED = " 基础练习 "
NEW_CHAPTER = "基础练习"
INNER_SPACES_CHAPTER_PADDED = "  阶段  复习  "
INNER_SPACES_CHAPTER = "阶段  复习"

BAD_IDS = ["0", "-3", "abc"]
UNKNOWN_IDS = ["42", "9223372036854775808"]
BLANK_NAME = "   "

ERR_BAD_ID = "课程编号必须为正整数"
ERR_NOT_FOUND = "课程不存在"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_CHAPTER_NOT_FOUND = "章节不存在"
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


class RenameChapterTestCase(unittest.TestCase):
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

    def assert_rename_success(
        self, raw_id, chapter, name, expected_chapters, db_first=True
    ):
        """改名成功：退出码 0、标准错误为空、标准输出为一行课程详情 JSON。"""
        result = run_cli(
            self.db_path,
            "rename-chapter",
            str(raw_id),
            "--chapter",
            chapter,
            "--name",
            name,
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

    def assert_rename_failure(self, argv, exit_code, message):
        """改名失败：退出码、空标准输出、唯一标准错误消息，并保持数据。"""
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

    def test_rename_persists_with_names_stripped(self):
        """正常路径：改名后跨进程读取，仅目标章节名被去空白后替换。"""
        self.assert_rename_success(
            1,
            OLD_CHAPTER_PADDED,
            NEW_CHAPTER_PADDED,
            ["准备", NEW_CHAPTER, "回顾"],
        )

        # 改名进程已退出，新的调用确认编号 1 只有目标章节名变化
        self.assertEqual(
            self.get_course(1),
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": ["准备", NEW_CHAPTER, "回顾"],
            },
        )

        # 编号 2 内容完全不变
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

        # 概览中章节数不变
        self.assertEqual(self.get_overview(), ORIGINAL_OVERVIEW)

    def test_rename_preserves_inner_spaces_and_case(self):
        """新名称内部空白与大小写原样保留，仅去除首尾空白。"""
        self.assert_rename_success(
            1,
            "回顾",
            INNER_SPACES_CHAPTER_PADDED,
            ["准备", "学习", INNER_SPACES_CHAPTER],
        )
        self.assertEqual(
            self.get_course(1)["chapters"],
            ["准备", "学习", INNER_SPACES_CHAPTER],
        )

    def test_rename_to_same_name_succeeds_without_adding_chapter(self):
        """新名称与目标章节当前名称相同（含仅首尾空白差异）仍成功，不新增章节。"""
        self.assert_rename_success(1, "学习", " 学习 ", FIRST_CHAPTERS)
        self.assertEqual(self.get_course(1), ORIGINAL_FIRST_COURSE)
        self.assertEqual(self.get_overview(), ORIGINAL_OVERVIEW)

    def test_db_option_after_subcommand(self):
        """--db 写在 rename-chapter 之后同样成功并持久化。"""
        self.assert_rename_success(
            1,
            OLD_CHAPTER_PADDED,
            NEW_CHAPTER_PADDED,
            ["准备", NEW_CHAPTER, "回顾"],
            db_first=False,
        )
        self.assertEqual(
            self.get_course(1)["chapters"], ["准备", NEW_CHAPTER, "回顾"]
        )
        self.assertEqual(self.get_course(2), ORIGINAL_SECOND_COURSE)

    def test_same_name_in_other_course_does_not_conflict(self):
        """其他课程已有同名章节不构成冲突。"""
        # 编号 1 已有章节“准备”，编号 2 的章节也叫“准备”之外没有其他章节；
        # 把编号 1 的“学习”改为“进阶”后，编号 2 也可使用“进阶”
        self.assert_rename_success(
            1, "学习", "进阶", ["准备", "进阶", "回顾"]
        )
        result = run_cli(
            self.db_path,
            "rename-chapter",
            "2",
            "--chapter",
            "准备",
            "--name",
            "进阶",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {"course_id": 2, "title": SECOND_TITLE, "chapters": ["进阶"]},
        )

    def test_rename_then_duplicate_keeps_successful_result(self):
        """验收路径：改名成功后再改为已有章节名，报章节名重复且保留成功结果。"""
        self.assert_rename_success(
            1,
            OLD_CHAPTER_PADDED,
            NEW_CHAPTER_PADDED,
            ["准备", NEW_CHAPTER, "回顾"],
        )
        result = run_cli(
            self.db_path,
            "rename-chapter",
            "1",
            "--chapter",
            NEW_CHAPTER,
            "--name",
            "准备",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.rstrip("\r\n"), ERR_DUP_CHAPTER)
        # 首次改名的结果保持，章节数仍为 3
        self.assertEqual(
            self.get_course(1),
            {
                "course_id": 1,
                "title": FIRST_TITLE,
                "chapters": ["准备", NEW_CHAPTER, "回顾"],
            },
        )
        self.assertEqual(
            self.get_overview()["courses"][0]["chapter_count"], 3
        )

    def test_bad_course_id_format(self):
        """编号 0、-3、abc：退出码 2，报编号必须为正整数，数据不变。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    (
                        "rename-chapter",
                        raw_id,
                        "--chapter",
                        "学习",
                        "--name",
                        NEW_CHAPTER,
                    ),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_course_id(self):
        """未知正整数编号（含超出 int64 范围者）：退出码 1，报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    (
                        "rename-chapter",
                        raw_id,
                        "--chapter",
                        "学习",
                        "--name",
                        NEW_CHAPTER,
                    ),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_missing_chapter_option(self):
        """已有课程未提供 --chapter：退出码 1，报章节不能为空，数据不变。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--name", NEW_CHAPTER),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_blank_chapter_for_existing_course(self):
        """已有课程的原名称仅含空白：退出码 1，报章节不能为空，数据不变。"""
        self.assert_rename_failure(
            (
                "rename-chapter",
                "1",
                "--chapter",
                BLANK_NAME,
                "--name",
                NEW_CHAPTER,
            ),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_unknown_chapter(self):
        """原名称非空但该课程没有此章节：退出码 1，报章节不存在，数据不变。"""
        self.assert_rename_failure(
            (
                "rename-chapter",
                "1",
                "--chapter",
                "不存在的章节",
                "--name",
                NEW_CHAPTER,
            ),
            1,
            ERR_CHAPTER_NOT_FOUND,
        )

    def test_missing_name_option(self):
        """目标章节存在但未提供 --name：退出码 1，报章节不能为空，数据不变。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--chapter", "学习"),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_blank_name_for_existing_chapter(self):
        """目标章节存在但新名称仅含空白：退出码 1，报章节不能为空，数据不变。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--chapter", "学习", "--name", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_duplicate_name_rejected(self):
        """新名称与同课程其他章节重名（含仅首尾空白差异）：报章节名重复。"""
        for name in ("准备", " 回顾 "):
            with self.subTest(name=name):
                self.assert_rename_failure(
                    (
                        "rename-chapter",
                        "1",
                        "--chapter",
                        "学习",
                        "--name",
                        name,
                    ),
                    1,
                    ERR_DUP_CHAPTER,
                )

    def test_duplicate_comparison_is_case_sensitive(self):
        """重名比较大小写敏感：大小写不同的英文名可用。"""
        register_course(self.db_path, "英语", ["Reading", "Writing"])
        self.assertEqual(
            json.loads(
                run_cli(
                    self.db_path,
                    "rename-chapter",
                    "3",
                    "--chapter",
                    "Writing",
                    "--name",
                    "reading",
                ).stdout
            )["chapters"],
            ["Reading", "reading"],
        )

    def test_bad_id_reported_before_other_errors(self):
        """非法编号同时配缺失/空名称：只报编号格式错误。"""
        for raw_id in BAD_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    ("rename-chapter", raw_id), 2, ERR_BAD_ID
                )
                self.assert_rename_failure(
                    (
                        "rename-chapter",
                        raw_id,
                        "--chapter",
                        "学习",
                        "--name",
                        "准备",
                    ),
                    2,
                    ERR_BAD_ID,
                )

    def test_unknown_id_reported_before_chapter_errors(self):
        """未知编号同时配空/未知章节名：只报课程不存在。"""
        for raw_id in UNKNOWN_IDS:
            with self.subTest(course_id=raw_id):
                self.assert_rename_failure(
                    ("rename-chapter", raw_id), 1, ERR_NOT_FOUND
                )
                self.assert_rename_failure(
                    (
                        "rename-chapter",
                        raw_id,
                        "--chapter",
                        BLANK_NAME,
                        "--name",
                        BLANK_NAME,
                    ),
                    1,
                    ERR_NOT_FOUND,
                )

    def test_empty_chapter_reported_before_unknown_chapter_and_name(self):
        """原名称为空白时只报章节不能为空，不进入章节存在性与新名称判定。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--chapter", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )

    def test_unknown_chapter_reported_before_blank_name(self):
        """原名称非空但找不到章节时只报章节不存在，即使新名称为空。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--chapter", "不存在的章节"),
            1,
            ERR_CHAPTER_NOT_FOUND,
        )

    def test_blank_name_reported_before_duplicate(self):
        """新名称为空白时只报章节不能为空，不进入重名判定。"""
        self.assert_rename_failure(
            ("rename-chapter", "1", "--chapter", "学习", "--name", BLANK_NAME),
            1,
            ERR_EMPTY_CHAPTER,
        )


if __name__ == "__main__":
    unittest.main()
