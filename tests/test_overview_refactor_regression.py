"""概览读取流程重构的回归测试。

集中维护两处共有规则后，验收以下公开行为保持不变：
- 全新数据库中两门同标题课程（编号 1、2，章节数 2、1）与两名学员；
  两名学员都报名课程 2，学员 1 重复报名一次：全部课程概览按编号
  返回两项，学员 1 的概览只含课程 2 一项。
- 课程改名与章节增删后，两种概览查询中的同一课程显示相同的最新值。
- 空结果边界：未报名学员返回空列表；未知学员与超出 SQLite 整数范围
  的编号在函数入口返回 None；命令行非正整数学员编号退出 2，正整数
  对应学员不存在退出 1；空/空白标题筛选词在函数入口抛出
  ValidationError，命令行退出 1。
- 上述失败均标准输出为空、无异常堆栈，且查询不改变业务记录或占用编号。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from course_progress import connect, list_courses, list_learner_courses
from course_progress.core import (
    ERR_BAD_LEARNER_ID,
    ERR_EMPTY_TITLE_FILTER,
    ERR_LEARNER_NOT_FOUND,
    ValidationError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SQLITE_INT64_MAX = 2**63 - 1

SAME_TITLE = "同名课程"


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


class OverviewRefactorRegressionTest(unittest.TestCase):
    """每个用例使用全新数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "regression.db"

    def add_course(self, title, *chapters):
        args = ["add-course", "--title", title]
        for chapter in chapters:
            args.extend(["--chapter", chapter])
        result = run_cli(self.db_path, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id):
        result = run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def overview(self):
        result = run_cli(self.db_path, "list-courses")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["courses"]

    def learner_overview(self, learner_id):
        result = run_cli(
            self.db_path, "list-learner-courses", str(learner_id)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def build_acceptance_sample(self):
        """两门同标题课程（章节数 2、1）与两名学员，两人均报名课程 2，
        学员 1 重复报名一次。"""
        self.assertEqual(
            self.add_course(SAME_TITLE, "第一章", "第二章"), 1
        )
        self.assertEqual(self.add_course(SAME_TITLE, "总览"), 2)
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员乙"), 2)
        self.enroll(1, 2)
        self.enroll(2, 2)
        self.enroll(1, 2)  # 重复报名不增加结果项

    def test_acceptance_sample_all_courses_overview(self):
        """全部课程概览：编号 1、2 升序，章节数 2、1，同标题分别列出。"""
        self.build_acceptance_sample()

        courses = self.overview()
        self.assertEqual(
            courses,
            [
                {"course_id": 1, "title": SAME_TITLE, "chapter_count": 2},
                {"course_id": 2, "title": SAME_TITLE, "chapter_count": 1},
            ],
        )
        for course in courses:
            self.assertEqual(
                set(course.keys()),
                {"course_id", "title", "chapter_count"},
            )

    def test_acceptance_sample_learner_overview(self):
        """学员 1 只得到课程 2、章节数 1；其他学员的报名不影响章节数。"""
        self.build_acceptance_sample()

        overview_1 = self.learner_overview(1)
        self.assertEqual(
            overview_1,
            {
                "learner_id": 1,
                "courses": [
                    {"course_id": 2, "title": SAME_TITLE, "chapter_count": 1}
                ],
            },
        )

        # 学员 2 同样只报了课程 2，且章节数不被学员 1 的重复报名改变
        self.assertEqual(
            self.learner_overview(2)["courses"],
            [{"course_id": 2, "title": SAME_TITLE, "chapter_count": 1}],
        )

    def test_both_overviews_show_same_current_values_after_revision(self):
        """课程改名并增删章节后，两种查询显示相同的最新标题与章节数。"""
        self.build_acceptance_sample()

        renamed = run_cli(
            self.db_path, "rename-course", "2", "--title", "修订后标题"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        appended = run_cli(
            self.db_path, "append-chapter", "2", "--chapter", "新增章节"
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)

        all_courses = self.overview()
        self.assertEqual(
            all_courses[1],
            {"course_id": 2, "title": "修订后标题", "chapter_count": 2},
        )
        # 学员概览中的同一课程与全部课程概览完全一致
        self.assertEqual(
            self.learner_overview(1)["courses"][0], all_courses[1]
        )
        self.assertEqual(
            self.learner_overview(2)["courses"][0], all_courses[1]
        )
        # 未修订的课程 1 保持原值
        self.assertEqual(
            all_courses[0],
            {"course_id": 1, "title": SAME_TITLE, "chapter_count": 2},
        )

        removed = run_cli(
            self.db_path, "remove-chapter", "2", "--chapter", "新增章节"
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        all_courses = self.overview()
        self.assertEqual(all_courses[1]["chapter_count"], 1)
        self.assertEqual(
            self.learner_overview(1)["courses"][0], all_courses[1]
        )

    def test_learner_without_enrollments_returns_empty_list(self):
        """已有学员未报名任何课程时返回空列表。"""
        self.build_acceptance_sample()
        # 再登记一名不报名的学员
        self.assertEqual(self.add_learner("学员丙"), 3)
        result = run_cli(self.db_path, "list-learner-courses", "3")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 3, "courses": []}
        )

    def test_function_entry_unknown_and_overflow_learner_return_none(self):
        """未知学员与超出 SQLite 整数范围的编号在函数入口返回 None。"""
        self.build_acceptance_sample()
        conn = connect(str(self.db_path))
        try:
            self.assertIsNone(list_learner_courses(conn, 3))
            self.assertIsNone(
                list_learner_courses(conn, SQLITE_INT64_MAX + 1)
            )
            self.assertIsNone(
                list_learner_courses(conn, -(SQLITE_INT64_MAX + 2))
            )
            # 存在的学员仍正常返回，证明入口判断未误伤
            self.assertEqual(
                [c["course_id"] for c in list_learner_courses(conn, 1)], [2]
            )
        finally:
            conn.close()

    def assert_cli_failure(self, result, exit_code, message):
        """失败：退出码、空标准输出、标准错误提示且无异常堆栈。"""
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_non_positive_learner_id_exits_2(self):
        """非正整数学员编号：输出“学员编号必须为正整数”并退出 2。"""
        self.build_acceptance_sample()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_cli_failure(
                    run_cli(self.db_path, "list-learner-courses", raw),
                    2,
                    ERR_BAD_LEARNER_ID,
                )

    def test_cli_unknown_positive_learner_exits_1(self):
        """正整数对应学员不存在：输出“学员不存在”并退出 1。"""
        self.build_acceptance_sample()
        self.assert_cli_failure(
            run_cli(self.db_path, "list-learner-courses", "9"),
            1,
            ERR_LEARNER_NOT_FOUND,
        )
        self.assert_cli_failure(
            run_cli(
                self.db_path,
                "list-learner-courses",
                str(SQLITE_INT64_MAX + 1),
            ),
            1,
            ERR_LEARNER_NOT_FOUND,
        )

    def test_empty_title_filter_function_and_cli(self):
        """空或仅空白的筛选词：函数入口抛 ValidationError，CLI 退出 1。"""
        self.build_acceptance_sample()
        conn = connect(str(self.db_path))
        try:
            for raw in ("", "   ", "\t\n "):
                with self.subTest(filter=raw):
                    with self.assertRaises(ValidationError) as ctx:
                        list_courses(conn, raw)
                    self.assertEqual(
                        str(ctx.exception), ERR_EMPTY_TITLE_FILTER
                    )
        finally:
            conn.close()

        for raw in ("", "   \t "):
            with self.subTest(filter=raw):
                result = run_cli(
                    self.db_path, "list-courses", "--title-contains", raw
                )
                self.assert_cli_failure(result, 1, ERR_EMPTY_TITLE_FILTER)

    def test_failed_queries_are_read_only_and_do_not_consume_ids(self):
        """失败查询不改变业务记录，也不占用课程或学员编号。"""
        self.build_acceptance_sample()

        # 各种失败入口
        run_cli(self.db_path, "list-learner-courses", "0")
        run_cli(self.db_path, "list-learner-courses", "9")
        run_cli(
            self.db_path,
            "list-learner-courses",
            str(SQLITE_INT64_MAX + 1),
        )
        run_cli(self.db_path, "list-courses", "--title-contains", " ")

        # 业务记录原样：全部概览与学员 1 概览不变
        self.assertEqual(
            self.overview(),
            [
                {"course_id": 1, "title": SAME_TITLE, "chapter_count": 2},
                {"course_id": 2, "title": SAME_TITLE, "chapter_count": 1},
            ],
        )
        self.assertEqual(
            self.learner_overview(1)["courses"],
            [{"course_id": 2, "title": SAME_TITLE, "chapter_count": 1}],
        )

        # 编号未被占用：新课程为 3，新学员为 3
        self.assertEqual(
            self.add_course("再一门课", "唯一章节"), 3
        )
        self.assertEqual(self.add_learner("学员丁"), 3)

    def test_successful_filter_still_scoped_to_titles_and_returns_empty(self):
        """筛选仍只匹配标题、按编号升序，未匹配返回空数组。"""
        self.build_acceptance_sample()

        matched = run_cli(
            self.db_path, "list-courses", "--title-contains", SAME_TITLE
        )
        self.assertEqual(matched.returncode, 0, matched.stderr)
        self.assertEqual(
            [c["course_id"] for c in json.loads(matched.stdout)["courses"]],
            [1, 2],
        )

        # 章节名不作为匹配对象
        chapter_only = run_cli(
            self.db_path, "list-courses", "--title-contains", "总览"
        )
        self.assertEqual(
            json.loads(chapter_only.stdout), {"courses": []}
        )
        no_match = run_cli(
            self.db_path, "list-courses", "--title-contains", "不存在的标题"
        )
        self.assertEqual(json.loads(no_match.stdout), {"courses": []})


if __name__ == "__main__":
    unittest.main()
