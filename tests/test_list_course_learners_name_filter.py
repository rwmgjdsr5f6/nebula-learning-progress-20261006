"""按姓名片段筛选课程报名名册（list-course-learners --name-contains）。

通过子进程调用 ``python -m course_progress`` 验证公开 CLI 行为，并直接
调用公开函数 ``list_course_learners`` 验证函数层面的匹配与校验规则。
每个用例使用独立临时目录，结束后自动清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from course_progress import (
    add_course,
    add_learner,
    connect,
    enroll_learner,
    list_course_learners,
)
from course_progress.core import (
    ERR_BAD_ID,
    ERR_EMPTY_LEARNER_NAME_FILTER,
    ERR_NOT_FOUND,
    ValidationError,
    rename_learner,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 验收样例：一门课程；四名学员依次为 学员甲、学员乙、学员甲、学员甲；
# 仅学员 3、2、1 报名课程 1（报名顺序 3、2、1）。
LEARNER_NAMES = ["学员甲", "学员乙", "学员甲", "学员甲"]
ENROLLED = [3, 2, 1]
SQLITE_INT64_MAX = 2**63 - 1


def run_cli(db_path, *args, db_first=True):
    """以独立进程运行 CLI，返回 CompletedProcess。

    db_first 为 True 时把 --db 写在子命令前，否则写在子命令后。
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


def register_sample(db_path):
    """登记一门课程、四名学员，并按 3、2、1 的顺序报名课程 1。"""
    result = run_cli(
        db_path, "add-course", "--title", "课程一", "--chapter", "第一章"
    )
    assert result.returncode == 0, result.stderr
    for name in LEARNER_NAMES:
        result = run_cli(db_path, "add-learner", "--name", name)
        assert result.returncode == 0, result.stderr
    for learner_id in ENROLLED:
        result = run_cli(db_path, "enroll-learner", str(learner_id), "--course", "1")
        assert result.returncode == 0, result.stderr


class TestListCourseLearnersNameFilter(unittest.TestCase):
    """每个用例一个独立临时目录，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "sample.db"

    def assert_success(self, result, expected):
        """成功筛选：退出码 0、标准错误为空、单行 JSON 且内容符合预期。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_fixed_acceptance_sample(self):
        """筛选词 " 甲 " 去首尾空白后只返回编号 1、3，按编号升序。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", " 甲 "
        )

        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 3, "name": "学员甲"},
                ],
            },
        )

    def test_db_option_after_subcommand(self):
        """--db 写在子命令后语义相同。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path,
            "list-course-learners",
            "1",
            "--name-contains",
            "乙",
            db_first=False,
        )

        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [{"learner_id": 2, "name": "学员乙"}],
            },
        )

    def test_no_match_returns_empty_list(self):
        """不存在的姓名片段返回空数组，退出码 0。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "不存在"
        )

        self.assert_success(result, {"course_id": 1, "learners": []})

    def test_filter_without_argument_keeps_full_roster(self):
        """省略筛选参数时行为与原先完全一致，返回全部已报名学员。"""
        register_sample(self.db_path)

        result = run_cli(self.db_path, "list-course-learners", "1")
        self.assert_success(
            result,
            {
                "course_id": 1,
                "learners": [
                    {"learner_id": 1, "name": "学员甲"},
                    {"learner_id": 2, "name": "学员乙"},
                    {"learner_id": 3, "name": "学员甲"},
                ],
            },
        )

    def test_only_enrolled_learners_are_searched(self):
        """未报名的同名学员（编号 4）不出现在结果中。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "甲"
        )
        learners = json.loads(result.stdout)["learners"]
        self.assertEqual([l["learner_id"] for l in learners], [1, 3])

    def test_other_courses_enrollments_are_not_searched(self):
        """其他课程的报名学员不在本课程筛选范围内。"""
        register_sample(self.db_path)
        other = run_cli(
            self.db_path, "add-course", "--title", "课程二", "--chapter", "章节"
        )
        self.assertEqual(other.returncode, 0)
        enrolled = run_cli(self.db_path, "enroll-learner", "4", "--course", "2")
        self.assertEqual(enrolled.returncode, 0)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "甲"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(result.stdout)["learners"]],
            [1, 3],
        )

    def test_empty_course_with_filter_returns_empty_list(self):
        """课程存在但无人报名时，即使给筛选词也返回空数组。"""
        register_sample(self.db_path)
        other = run_cli(
            self.db_path, "add-course", "--title", "课程二", "--chapter", "章节"
        )
        self.assertEqual(other.returncode, 0)

        result = run_cli(
            self.db_path, "list-course-learners", "2", "--name-contains", "甲"
        )
        self.assert_success(result, {"course_id": 2, "learners": []})

    def test_duplicate_enrollment_appears_once(self):
        """重复报名不会让同一学员出现两次。"""
        register_sample(self.db_path)
        again = run_cli(self.db_path, "enroll-learner", "1", "--course", "1")
        self.assertEqual(again.returncode, 0)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "甲"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(result.stdout)["learners"]],
            [1, 3],
        )

    def test_rename_uses_current_name(self):
        """学员改名后按新姓名判断，旧姓名不再匹配。"""
        register_sample(self.db_path)
        renamed = run_cli(
            self.db_path, "rename-learner", "1", "--name", "学员丙"
        )
        self.assertEqual(renamed.returncode, 0)

        old = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "甲"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(old.stdout)["learners"]], [3]
        )
        new = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "丙"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(new.stdout)["learners"]], [1]
        )

    def test_case_sensitive_and_literal_special_characters(self):
        """大小写敏感；百分号、下划线按普通字符匹配。"""
        register_sample(self.db_path)
        run_cli(self.db_path, "rename-learner", "3", "--name", "Tom 100%_x")

        lower = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "tom"
        )
        self.assert_success(lower, {"course_id": 1, "learners": []})

        upper = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "Tom"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(upper.stdout)["learners"]], [3]
        )

        literal = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "%_"
        )
        self.assertEqual(
            [l["learner_id"] for l in json.loads(literal.stdout)["learners"]], [3]
        )

        wildcard_attempt = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "%"
        )
        # 学员 1、2 姓名中没有百分号，"%" 不充当通配符
        names = [l["name"] for l in json.loads(wildcard_attempt.stdout)["learners"]]
        self.assertTrue(all("%" in name for name in names))

    def test_internal_whitespace_is_preserved_in_filter(self):
        """内部空白参与匹配：姓名中没有 "学员 甲"（中间带空格）时无结果。"""
        register_sample(self.db_path)

        with_space = run_cli(
            self.db_path, "list-course-learners", "1",
            "--name-contains", "学员 甲",
        )
        self.assert_success(with_space, {"course_id": 1, "learners": []})

        without_space = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "学员甲"
        )
        self.assertEqual(
            len(json.loads(without_space.stdout)["learners"]), 2
        )

    def test_empty_filter_exits_1_with_message_and_no_traceback(self):
        """显式空字符串：退出码 1，stdout 为空，stderr 仅有中文提示。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", ""
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_LEARNER_NAME_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_whitespace_only_filter_exits_1_with_message(self):
        """仅含空白的筛选词同样失败：退出码 1，输出为空，提示到 stderr。"""
        register_sample(self.db_path)

        result = run_cli(
            self.db_path, "list-course-learners", "1", "--name-contains", "  \t "
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_EMPTY_LEARNER_NAME_FILTER)
        self.assertNotIn("Traceback", result.stderr)

    def test_error_order_bad_id_before_empty_filter(self):
        """编号格式错误优先于筛选词为空：退出码 2，报课程编号错误。"""
        register_sample(self.db_path)

        for bad_id in ("0", "-3", "abc", ""):
            result = run_cli(
                self.db_path,
                "list-course-learners",
                bad_id,
                "--name-contains",
                "",
            )
            self.assertEqual(result.returncode, 2, bad_id)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), ERR_BAD_ID, bad_id)

    def test_error_order_missing_course_before_empty_filter(self):
        """未知正整数与超出 int64 编号优先报课程不存在，退出码 1。"""
        register_sample(self.db_path)

        for course_id in ("999", str(SQLITE_INT64_MAX + 1)):
            result = run_cli(
                self.db_path,
                "list-course-learners",
                course_id,
                "--name-contains",
                "",
            )
            self.assertEqual(result.returncode, 1, course_id)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), ERR_NOT_FOUND, course_id)
            self.assertNotIn("Traceback", result.stderr)

    def test_filter_is_read_only(self):
        """筛选不修改任何课程、学员与报名记录。"""
        register_sample(self.db_path)
        before = run_cli(self.db_path, "list-course-learners", "1")

        for word in ("甲", "乙", "不存在", "%", "_"):
            filtered = run_cli(
                self.db_path, "list-course-learners", "1",
                "--name-contains", word,
            )
            self.assertEqual(filtered.returncode, 0)

        after = run_cli(self.db_path, "list-course-learners", "1")
        self.assertEqual(before.stdout, after.stdout)

        # 重新打开同一文件，结果仍然一致
        once_more = run_cli(self.db_path, "list-course-learners", "1")
        self.assertEqual(after.stdout, once_more.stdout)

    def test_missing_db_takes_priority_over_empty_filter(self):
        """缺少 --db 时仍优先返回原提示，退出码 2。"""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "course_progress",
                "list-course-learners",
                "1",
                "--name-contains",
                "",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), "必须通过 --db 指定数据库文件")


class TestListCourseLearnersFunctionFilter(unittest.TestCase):
    """直接调用公开函数 list_course_learners，规则与命令行一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(str(Path(self._tmp.name) / "func.db"))
        self.addCleanup(self.conn.close)
        add_course(self.conn, "课程一", ["第一章"])
        add_course(self.conn, "课程二", ["章节"])
        for name in LEARNER_NAMES:
            add_learner(self.conn, name)
        for learner_id in ENROLLED:
            enroll_learner(self.conn, learner_id, 1)

    def test_none_and_omitted_return_full_roster(self):
        """省略参数或显式 None 返回全部已报名学员，按编号升序。"""
        self.assertEqual(
            [l["learner_id"] for l in list_course_learners(self.conn, 1)],
            [1, 2, 3],
        )
        self.assertEqual(
            [l["learner_id"] for l in list_course_learners(self.conn, 1, None)],
            [1, 2, 3],
        )

    def test_function_filter_matches_substring(self):
        """函数筛选与命令行同规则：去首尾空白、大小写敏感、字面字符。"""
        result = list_course_learners(self.conn, 1, " 甲 ")
        self.assertEqual([l["learner_id"] for l in result], [1, 3])

        self.assertEqual(
            [l["learner_id"] for l in list_course_learners(self.conn, 1, "乙")],
            [2],
        )
        self.assertEqual(list_course_learners(self.conn, 1, "没有的姓名"), [])

    def test_missing_course_returns_none_even_with_empty_filter(self):
        """课程不存在返回 None，即使筛选词为空也不抛 ValidationError。"""
        self.assertIsNone(list_course_learners(self.conn, 999, ""))
        self.assertIsNone(
            list_course_learners(self.conn, SQLITE_INT64_MAX + 1, "   ")
        )

    def test_empty_or_whitespace_filter_raises_validation_error(self):
        """课程存在但筛选词为空时抛出 ValidationError，消息与命令行一致。"""
        for bad in ("", "   ", "\t\n  "):
            with self.assertRaises(ValidationError) as ctx:
                list_course_learners(self.conn, 1, bad)
            self.assertEqual(str(ctx.exception), ERR_EMPTY_LEARNER_NAME_FILTER)

    def test_function_rename_uses_current_name(self):
        """改名后按新姓名匹配。"""
        rename_learner(self.conn, 2, "学员丙")
        self.assertEqual(
            [l["learner_id"] for l in list_course_learners(self.conn, 1, "乙")],
            [],
        )
        self.assertEqual(
            [l["learner_id"] for l in list_course_learners(self.conn, 1, "丙")],
            [2],
        )


if __name__ == "__main__":
    unittest.main()
