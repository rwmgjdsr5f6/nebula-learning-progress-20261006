"""按学员查询已报名课程（list-learner-courses）的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
验收样例流程与跨进程读取、报名顺序与输出排序无关、重复报名不产生
重复项、空列表、同名学员与同标题课程按编号区分且其他学员的报名不
混入、课程改名与章节增删后反映当前值、编号解析与失败退出码、数据库
文件不存在时自动建库建表。每个用例使用独立的临时数据库，结束后自动
清理。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SQLITE_INT64_MAX = 2**63 - 1


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


class ListLearnerCoursesTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"

    def assert_failure(self, result, exit_code, message):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), message)
        self.assertNotIn("Traceback", result.stderr)

    def add_course(self, title, *chapters):
        args = ["add-course", "--title", title]
        for chapter in chapters:
            args.extend(["--chapter", chapter])
        result = run_cli(self.db_path, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name="学员甲"):
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
        return result

    def list_learner_courses(self, learner_id, db_first=True):
        return run_cli(
            self.db_path,
            "list-learner-courses",
            str(learner_id),
            db_first=db_first,
        )


class TestListLearnerCourses(ListLearnerCoursesTestCase):
    def test_fixed_acceptance_sample(self):
        # 编号 1、2 的课程，章节数分别为 2 和 1；编号 1 的学员
        self.assertEqual(
            self.add_course("入门培训", "第一章", "第二章"), 1
        )
        self.assertEqual(self.add_course("进阶培训", "总览"), 2)
        self.assertEqual(self.add_learner("学员甲"), 1)

        # 先报名课程 2 再报名课程 1，输出仍按课程编号升序
        self.enroll(1, 2)
        self.enroll(1, 1)

        result = self.list_learner_courses(1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "入门培训",
                        "chapter_count": 2,
                    },
                    {
                        "course_id": 2,
                        "title": "进阶培训",
                        "chapter_count": 1,
                    },
                ],
            },
        )

        # 退出程序后再次查询结果一致
        again = self.list_learner_courses(1)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(again.stdout, result.stdout)

    def test_db_option_after_subcommand(self):
        self.add_course("入门培训", "准备")
        self.add_learner()
        self.enroll(1, 1)
        result = self.list_learner_courses(1, db_first=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "入门培训",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_empty_courses_for_learner_without_enrollments(self):
        self.add_course("入门培训", "准备")
        self.add_learner()
        result = self.list_learner_courses(1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "courses": []}
        )

    def test_duplicate_enrollment_appears_once(self):
        self.add_course("入门培训", "准备")
        self.add_learner()
        self.enroll(1, 1)
        self.enroll(1, 1)
        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout)["courses"],
            [{"course_id": 1, "title": "入门培训", "chapter_count": 1}],
        )

    def test_other_learners_enrollments_do_not_mix_in(self):
        self.add_course("入门培训", "准备")
        self.add_course("进阶培训", "总览")
        self.add_learner("学员甲")
        self.add_learner("学员乙")

        self.enroll(2, 1)
        self.enroll(2, 2)
        self.enroll(1, 2)

        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 2,
                        "title": "进阶培训",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_same_titles_and_names_distinguished_by_id(self):
        self.add_course("同名课程", "准备")
        self.add_course("同名课程", "总览")
        self.add_learner("学员甲")
        self.add_learner("学员甲")
        self.enroll(2, 1)
        self.enroll(1, 2)

        first = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(first.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 2,
                        "title": "同名课程",
                        "chapter_count": 1,
                    }
                ],
            },
        )
        second = self.list_learner_courses(2)
        self.assertEqual(
            json.loads(second.stdout),
            {
                "learner_id": 2,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "同名课程",
                        "chapter_count": 1,
                    }
                ],
            },
        )

    def test_output_uses_current_title_and_chapter_count(self):
        self.add_course("旧标题", "第一章", "第二章")
        self.add_learner()
        self.enroll(1, 1)

        renamed = run_cli(
            self.db_path, "rename-course", "1", "--title", "新标题"
        )
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        appended = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "第三章"
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)

        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {
                        "course_id": 1,
                        "title": "新标题",
                        "chapter_count": 3,
                    }
                ],
            },
        )

        removed = run_cli(
            self.db_path, "remove-chapter", "1", "--chapter", "第三章"
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout)["courses"][0]["chapter_count"], 2
        )

    def test_id_affixes_follow_get_learner_semantics(self):
        self.add_course("入门培训", "准备")
        self.add_learner()
        self.enroll(1, 1)
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(learner_id=raw):
                result = self.list_learner_courses(raw)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout)["learner_id"], 1
                )
                self.assertEqual(
                    json.loads(result.stdout)["courses"],
                    [
                        {
                            "course_id": 1,
                            "title": "入门培训",
                            "chapter_count": 1,
                        }
                    ],
                )

    def test_query_is_read_only(self):
        self.add_course("入门培训", "准备")
        self.add_learner()
        self.enroll(1, 1)
        before = self.list_learner_courses(1).stdout
        self.list_learner_courses(1)
        self.list_learner_courses(2)
        self.assertEqual(self.list_learner_courses(1).stdout, before)
        learners = run_cli(self.db_path, "list-learners")
        self.assertEqual(
            json.loads(learners.stdout),
            {"learners": [{"learner_id": 1, "name": "学员甲"}]},
        )


class TestListLearnerCoursesFailures(ListLearnerCoursesTestCase):
    def test_bad_learner_id_format(self):
        self.add_learner()
        for raw in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    self.list_learner_courses(raw),
                    2,
                    "学员编号必须为正整数",
                )

    def test_unknown_learner(self):
        self.add_learner()
        self.assert_failure(
            self.list_learner_courses(2), 1, "学员不存在"
        )

    def test_overflow_id_treated_as_unknown(self):
        self.add_learner()
        self.assert_failure(
            self.list_learner_courses(str(SQLITE_INT64_MAX + 1)),
            1,
            "学员不存在",
        )

    def test_missing_argument_uses_argparse_error(self):
        result = run_cli(self.db_path, "list-learner-courses")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_db_file_auto_created_then_reports_unknown(self):
        fresh_db = Path(self._tmp.name) / "fresh.db"
        self.assertFalse(fresh_db.exists())
        result = run_cli(fresh_db, "list-learner-courses", "1")
        self.assert_failure(result, 1, "学员不存在")
        self.assertTrue(fresh_db.exists())


if __name__ == "__main__":
    unittest.main()
