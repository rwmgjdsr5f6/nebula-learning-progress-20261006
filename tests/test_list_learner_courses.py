"""按学员查询已报名课程的回归测试。

通过子进程调用 ``python -m course_progress``，验证公开 CLI 行为：
按课程编号升序列出该学员实际报名的课程（编号、标题、章节数），
重复报名不产生重复项，其他学员的报名不混入，课程改名与章节增删
在后续查询中反映，未报名学员返回空数组，编号格式与存在性错误的
消息、退出码与 get-learner 一致，数据库文件之间互相隔离。
每个用例使用独立的临时数据库，结束后自动清理。
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

    def add_course(self, title, chapters):
        args = ["add-course", "--title", title]
        for chapter in chapters:
            args += ["--chapter", chapter]
        result = run_cli(self.db_path, *args)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["course_id"]

    def add_learner(self, name="学员甲"):
        result = run_cli(self.db_path, "add-learner", "--name", name)
        self.assertEqual(result.returncode, 0)
        return json.loads(result.stdout)["learner_id"]

    def enroll(self, learner_id, course_id):
        result = run_cli(
            self.db_path,
            "enroll-learner",
            str(learner_id),
            "--course",
            str(course_id),
        )
        self.assertEqual(result.returncode, 0)

    def list_learner_courses(self, learner_id, db_first=True):
        return run_cli(
            self.db_path,
            "list-learner-courses",
            str(learner_id),
            db_first=db_first,
        )


class TestListLearnerCourses(ListLearnerCoursesTestCase):
    def test_acceptance_flow_sorted_by_course_id(self):
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_course("入门培训", ["第一章", "第二章"]), 1)
        self.assertEqual(self.add_course("进阶培训", ["第三章"]), 2)

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
                    {"course_id": 1, "title": "入门培训", "chapter_count": 2},
                    {"course_id": 2, "title": "进阶培训", "chapter_count": 1},
                ],
            },
        )
        # 退出程序后再次查询结果一致
        again = self.list_learner_courses(1)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(again.stdout, result.stdout)

    def test_output_is_single_json_line(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)
        result = self.list_learner_courses(1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            set(json.loads(lines[0]).keys()), {"learner_id", "courses"}
        )
        self.assertEqual(
            set(json.loads(lines[0])["courses"][0].keys()),
            {"course_id", "title", "chapter_count"},
        )

    def test_db_option_after_subcommand(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)
        result = self.list_learner_courses(1, db_first=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {"course_id": 1, "title": "入门培训", "chapter_count": 1}
                ],
            },
        )

    def test_learner_without_enrollments_returns_empty_courses(self):
        self.add_learner("学员甲")
        self.add_course("入门培训", ["第一章"])
        result = self.list_learner_courses(1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "courses": []}
        )

    def test_duplicate_enroll_listed_once(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)
        self.enroll(1, 1)
        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout)["courses"],
            [{"course_id": 1, "title": "入门培训", "chapter_count": 1}],
        )

    def test_other_learners_enrollments_not_mixed(self):
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员乙"), 2)
        self.assertEqual(self.add_course("入门培训", ["第一章"]), 1)
        self.assertEqual(self.add_course("进阶培训", ["第二章"]), 2)
        self.enroll(1, 1)
        self.enroll(2, 2)

        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 1,
                "courses": [
                    {"course_id": 1, "title": "入门培训", "chapter_count": 1}
                ],
            },
        )
        other = self.list_learner_courses(2)
        self.assertEqual(
            json.loads(other.stdout),
            {
                "learner_id": 2,
                "courses": [
                    {"course_id": 2, "title": "进阶培训", "chapter_count": 1}
                ],
            },
        )

    def test_same_names_distinguished_by_id(self):
        self.assertEqual(self.add_learner("学员甲"), 1)
        self.assertEqual(self.add_learner("学员甲"), 2)
        self.assertEqual(self.add_course("入门培训", ["第一章"]), 1)
        self.assertEqual(self.add_course("入门培训", ["第二章"]), 2)
        self.enroll(2, 2)

        result = self.list_learner_courses(2)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "learner_id": 2,
                "courses": [
                    {"course_id": 2, "title": "入门培训", "chapter_count": 1}
                ],
            },
        )
        empty = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(empty.stdout), {"learner_id": 1, "courses": []}
        )

    def test_reflects_course_rename_and_chapter_changes(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章", "第二章"])
        self.enroll(1, 1)

        renamed = run_cli(
            self.db_path, "rename-course", "1", "--title", "基础培训"
        )
        self.assertEqual(renamed.returncode, 0)
        removed = run_cli(
            self.db_path, "remove-chapter", "1", "--chapter", "第二章"
        )
        self.assertEqual(removed.returncode, 0)
        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout)["courses"],
            [{"course_id": 1, "title": "基础培训", "chapter_count": 1}],
        )
        appended = run_cli(
            self.db_path, "append-chapter", "1", "--chapter", "第三章"
        )
        self.assertEqual(appended.returncode, 0)
        result = self.list_learner_courses(1)
        self.assertEqual(
            json.loads(result.stdout)["courses"],
            [{"course_id": 1, "title": "基础培训", "chapter_count": 2}],
        )

    def test_query_does_not_modify_records(self):
        self.add_learner("学员甲")
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)
        self.list_learner_courses(1)
        self.list_learner_courses(1)

        learner = run_cli(self.db_path, "get-learner", "1")
        self.assertEqual(
            json.loads(learner.stdout), {"learner_id": 1, "name": "学员甲"}
        )
        course = run_cli(self.db_path, "get-course", "1")
        self.assertEqual(
            json.loads(course.stdout),
            {"course_id": 1, "title": "入门培训", "chapters": ["第一章"]},
        )
        roster = run_cli(self.db_path, "list-course-learners", "1")
        self.assertEqual(
            json.loads(roster.stdout),
            {"course_id": 1, "learners": [{"learner_id": 1, "name": "学员甲"}]},
        )

    def test_id_affixes_follow_get_learner_semantics(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)
        for raw in ["+1", "01", " 1 ", "\t1\n"]:
            with self.subTest(learner_id=raw):
                result = self.list_learner_courses(raw)
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

    def test_enrollments_isolated_between_db_files(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.enroll(1, 1)

        other_db = Path(self._tmp.name) / "other.db"
        run_cli(other_db, "add-learner", "--name", "学员甲")
        run_cli(
            other_db, "add-course", "--title", "入门培训", "--chapter", "第一章"
        )
        result = run_cli(other_db, "list-learner-courses", "1")
        self.assertEqual(
            json.loads(result.stdout), {"learner_id": 1, "courses": []}
        )


class TestListLearnerCoursesFailures(ListLearnerCoursesTestCase):
    def test_bad_learner_id_format(self):
        self.add_learner()
        for raw in ["0", "-1", "abc", "1.5", ""]:
            with self.subTest(learner_id=raw):
                self.assert_failure(
                    self.list_learner_courses(raw), 2, "学员编号必须为正整数"
                )

    def test_unknown_learner(self):
        self.add_learner()
        self.assert_failure(self.list_learner_courses(2), 1, "学员不存在")

    def test_overflow_id_treated_as_unknown(self):
        self.add_learner()
        self.assert_failure(
            self.list_learner_courses(str(SQLITE_INT64_MAX + 1)),
            1,
            "学员不存在",
        )
        self.assert_failure(
            self.list_learner_courses(str(SQLITE_INT64_MAX)),
            1,
            "学员不存在",
        )

    def test_missing_db_file_auto_created_then_unknown_learner(self):
        self.assertFalse(self.db_path.exists())
        self.assert_failure(self.list_learner_courses(1), 1, "学员不存在")
        self.assertTrue(self.db_path.exists())

    def test_missing_argument_uses_argparse_error(self):
        result = run_cli(self.db_path, "list-learner-courses")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_failure_leaves_no_records(self):
        self.add_learner()
        self.add_course("入门培训", ["第一章"])
        self.list_learner_courses(2)
        self.list_learner_courses("abc")
        learners = run_cli(self.db_path, "list-learners")
        self.assertEqual(
            json.loads(learners.stdout),
            {"learners": [{"learner_id": 1, "name": "学员甲"}]},
        )
        roster = run_cli(self.db_path, "list-course-learners", "1")
        self.assertEqual(
            json.loads(roster.stdout), {"course_id": 1, "learners": []}
        )


if __name__ == "__main__":
    unittest.main()
