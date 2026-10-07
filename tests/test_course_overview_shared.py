"""课程概览读取流程重构的回归测试。

list_courses 与 list_learner_courses 的概览读取（课程编号、标题、
章节数及结果整理）已集中到 core 内部的同一流程；本文件证明公开行为
保持不变。通过子进程调用 ``python -m course_progress`` 验证 CLI 行为，
并直接调用公开函数验证函数入口约定：验收样例（两门同标题课程、两名
学员、重复报名）、课程修订后的当前值、空结果边界、编号与筛选词校验、
失败时的退出码与输出约定、查询只读。每个用例使用独立临时目录，结束
后自动清理。
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
    list_courses,
    list_learner_courses,
)
from course_progress.core import (
    ERR_EMPTY_TITLE_FILTER,
    ValidationError,
    append_chapter,
    remove_chapter,
    rename_course,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SQLITE_INT64_MAX = 2**63 - 1
SQLITE_INT64_MIN = -(2**63)

# 验收样例：两门同标题课程，编号 1、2，章节数 2、1；两名学员都报名
# 课程 2，学员 1 重复报名一次。
SAMPLE_TITLE = "同名培训"
EXPECTED_ALL = [
    {"course_id": 1, "title": SAMPLE_TITLE, "chapter_count": 2},
    {"course_id": 2, "title": SAMPLE_TITLE, "chapter_count": 1},
]
EXPECTED_ENROLLED = [
    {"course_id": 2, "title": SAMPLE_TITLE, "chapter_count": 1},
]
OVERVIEW_FIELDS = {"course_id", "title", "chapter_count"}


def run_cli(db_path, *args):
    """以独立进程运行 CLI，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, "-m", "course_progress", "--db", str(db_path), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def prepare_sample_core(conn):
    """用公开函数在同一连接上登记验收样例数据。"""
    assert add_course(conn, SAMPLE_TITLE, ["第一章", "第二章"]) == 1
    assert add_course(conn, SAMPLE_TITLE, ["总览"]) == 2
    assert add_learner(conn, "学员甲") == 1
    assert add_learner(conn, "学员乙") == 2
    enroll_learner(conn, 1, 2)
    enroll_learner(conn, 2, 2)
    enroll_learner(conn, 1, 2)  # 重复报名


class TestOverviewSharedCore(unittest.TestCase):
    """函数层面：两种查询共用同一概览读取流程后的公开行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "shared.db"
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)

    def test_acceptance_sample_at_function_level(self):
        """全部课程为编号 1、2（章节数 2、1）；学员 1 只列课程 2。"""
        prepare_sample_core(self.conn)

        self.assertEqual(list_courses(self.conn), EXPECTED_ALL)
        self.assertEqual(list_learner_courses(self.conn, 1), EXPECTED_ENROLLED)
        # 学员 2 同样报名课程 2，结果与学员 1 一致
        self.assertEqual(list_learner_courses(self.conn, 2), EXPECTED_ENROLLED)
        for course in list_courses(self.conn) + list_learner_courses(self.conn, 1):
            self.assertEqual(set(course.keys()), OVERVIEW_FIELDS)

    def test_both_queries_reflect_current_values_after_revision(self):
        """课程改名与章节增删后，两种查询中的同一课程显示相同的最新信息。"""
        prepare_sample_core(self.conn)

        rename_course(self.conn, 2, "改名培训")
        append_chapter(self.conn, 2, "练习")
        expected = [
            {"course_id": 2, "title": "改名培训", "chapter_count": 2}
        ]
        self.assertEqual(
            [c for c in list_courses(self.conn) if c["course_id"] == 2],
            expected,
        )
        self.assertEqual(list_learner_courses(self.conn, 1), expected)
        self.assertEqual(list_learner_courses(self.conn, 2), expected)

        remove_chapter(self.conn, 2, "练习")
        expected = [
            {"course_id": 2, "title": "改名培训", "chapter_count": 1}
        ]
        self.assertEqual(
            [c for c in list_courses(self.conn) if c["course_id"] == 2],
            expected,
        )
        self.assertEqual(list_learner_courses(self.conn, 1), expected)

    def test_learner_without_enrollments_returns_empty_list(self):
        """学员存在但未报名时返回空列表，而不是 None。"""
        prepare_sample_core(self.conn)
        learner_id = add_learner(self.conn, "学员丙")
        self.assertEqual(list_learner_courses(self.conn, learner_id), [])

    def test_unknown_and_overflow_learner_id_return_none(self):
        """未知学员及超出 SQLite 整数范围的编号在函数入口返回 None。"""
        prepare_sample_core(self.conn)
        for learner_id in (
            3,
            SQLITE_INT64_MAX + 1,
            SQLITE_INT64_MIN - 1,
        ):
            with self.subTest(learner_id=learner_id):
                self.assertIsNone(list_learner_courses(self.conn, learner_id))

    def test_blank_title_filter_raises_validation_error(self):
        """空字符串或仅含空白的筛选词在函数入口抛出 ValidationError。"""
        prepare_sample_core(self.conn)
        for raw in ("", "   ", "\t\n "):
            with self.subTest(title_contains=raw):
                with self.assertRaises(ValidationError) as ctx:
                    list_courses(self.conn, raw)
                self.assertEqual(str(ctx.exception), ERR_EMPTY_TITLE_FILTER)
                self.assertEqual(str(ctx.exception), "课程标题筛选词不能为空")

    def test_title_filter_no_match_returns_empty_list(self):
        """筛选词未匹配任何标题时返回空列表。"""
        prepare_sample_core(self.conn)
        self.assertEqual(list_courses(self.conn, "不存在的标题"), [])

    def test_queries_are_read_only_and_do_not_consume_ids(self):
        """查询不改变业务记录，也不占用课程或学员编号。"""
        prepare_sample_core(self.conn)
        before_all = list_courses(self.conn)
        before_learner = list_learner_courses(self.conn, 1)

        list_courses(self.conn)
        list_courses(self.conn, SAMPLE_TITLE)
        list_learner_courses(self.conn, 1)
        list_learner_courses(self.conn, 3)

        self.assertEqual(list_courses(self.conn), before_all)
        self.assertEqual(list_learner_courses(self.conn, 1), before_learner)
        # 之后的登记仍拿到连续的下一个编号，说明查询未写入任何记录
        self.assertEqual(add_course(self.conn, "后续课程", ["导学"]), 3)
        self.assertEqual(add_learner(self.conn, "学员丙"), 3)


class TestOverviewSharedCli(unittest.TestCase):
    """CLI 层面：外层 JSON 结构与失败约定和 README 一致。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "shared.db"

    def prepare_sample_cli(self):
        """通过 CLI 在当前临时库中登记验收样例数据。"""
        for chapters in (["第一章", "第二章"], ["总览"]):
            args = ["add-course", "--title", SAMPLE_TITLE]
            for name in chapters:
                args.extend(["--chapter", name])
            result = run_cli(self.db_path, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("学员甲", "学员乙"):
            result = run_cli(self.db_path, "add-learner", "--name", name)
            self.assertEqual(result.returncode, 0, result.stderr)
        for learner_id in ("1", "2", "1"):
            result = run_cli(
                self.db_path,
                "enroll-learner",
                learner_id,
                "--course",
                "2",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def assert_success_json(self, result):
        """成功查询：退出码 0、标准错误为空、输出一行可解析 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)

    def assert_failure(self, result, exit_code, message):
        """失败调用：标准输出为空、标准错误只有一行提示、无异常堆栈。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def test_acceptance_sample_via_cli(self):
        """全部课程为编号 1、2（章节数 2、1）；学员 1 只列课程 2。"""
        self.prepare_sample_cli()

        all_courses = self.assert_success_json(
            run_cli(self.db_path, "list-courses")
        )
        self.assertEqual(all_courses, {"courses": EXPECTED_ALL})

        learner1 = self.assert_success_json(
            run_cli(self.db_path, "list-learner-courses", "1")
        )
        self.assertEqual(
            learner1, {"learner_id": 1, "courses": EXPECTED_ENROLLED}
        )
        learner2 = self.assert_success_json(
            run_cli(self.db_path, "list-learner-courses", "2")
        )
        self.assertEqual(
            learner2, {"learner_id": 2, "courses": EXPECTED_ENROLLED}
        )

        for course in all_courses["courses"] + learner1["courses"]:
            self.assertEqual(set(course.keys()), OVERVIEW_FIELDS)

    def test_cli_reflects_current_values_after_revision(self):
        """课程改名与章节增删后，两种 CLI 查询显示相同的最新信息。"""
        self.prepare_sample_cli()
        renamed = run_cli(
            self.db_path, "rename-course", "2", "--title", "改名培训"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        appended = run_cli(
            self.db_path, "append-chapter", "2", "--chapter", "练习"
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)

        expected = {"course_id": 2, "title": "改名培训", "chapter_count": 2}
        all_courses = self.assert_success_json(
            run_cli(self.db_path, "list-courses")
        )
        self.assertEqual(
            [c for c in all_courses["courses"] if c["course_id"] == 2],
            [expected],
        )
        learner1 = self.assert_success_json(
            run_cli(self.db_path, "list-learner-courses", "1")
        )
        self.assertEqual(learner1["courses"], [expected])

    def test_cli_empty_result_boundaries(self):
        """未报名的学员得到空数组；筛选词未匹配时得到空数组。"""
        self.prepare_sample_cli()
        result = run_cli(self.db_path, "add-learner", "--name", "学员丙")
        self.assertEqual(result.returncode, 0, result.stderr)

        learner3 = self.assert_success_json(
            run_cli(self.db_path, "list-learner-courses", "3")
        )
        self.assertEqual(learner3, {"learner_id": 3, "courses": []})

        no_match = self.assert_success_json(
            run_cli(
                self.db_path,
                "list-courses",
                "--title-contains",
                "不存在的标题",
            )
        )
        self.assertEqual(no_match, {"courses": []})

    def test_cli_rejects_non_positive_learner_id_with_exit_2(self):
        """非正整数学员编号：提示学员编号必须为正整数并退出 2。"""
        self.prepare_sample_cli()
        for raw in ("0", "-3", "abc", "1.5"):
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    run_cli(self.db_path, "list-learner-courses", raw),
                    2,
                    "学员编号必须为正整数",
                )

    def test_cli_unknown_learner_exits_1(self):
        """正整数但学员不存在：提示学员不存在并退出 1。"""
        self.prepare_sample_cli()
        self.assert_failure(
            run_cli(self.db_path, "list-learner-courses", "99"),
            1,
            "学员不存在",
        )

    def test_cli_blank_title_filter_exits_1(self):
        """仅含空白的标题筛选词：提示筛选词不能为空并退出 1。"""
        self.prepare_sample_cli()
        self.assert_failure(
            run_cli(self.db_path, "list-courses", "--title-contains", "   "),
            1,
            "课程标题筛选词不能为空",
        )

    def test_cli_queries_are_read_only(self):
        """CLI 查询后业务记录与后续编号分配均不变。"""
        self.prepare_sample_cli()
        before = self.assert_success_json(
            run_cli(self.db_path, "list-courses")
        )
        run_cli(self.db_path, "list-courses", "--title-contains", SAMPLE_TITLE)
        run_cli(self.db_path, "list-learner-courses", "1")
        run_cli(self.db_path, "list-learner-courses", "99")
        self.assertEqual(
            self.assert_success_json(run_cli(self.db_path, "list-courses")),
            before,
        )
        added = run_cli(
            self.db_path,
            "add-course",
            "--title",
            "后续课程",
            "--chapter",
            "导学",
        )
        self.assertEqual(json.loads(added.stdout), {"course_id": 3})


if __name__ == "__main__":
    unittest.main()
