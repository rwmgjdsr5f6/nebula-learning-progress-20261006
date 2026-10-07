"""学员名册（list_learners / list-learners）公开行为的回归测试。

同时核对包导出的 ``list_learners`` 返回值与 ``python -m course_progress
list-learners`` 的命令输出，确认两条入口遵循相同约定：空库返回空名册、
按编号升序返回且同名学员不合并、姓名内部空白与大小写原样保留、
查询为只读操作（不改动学员/课程记录、不占用编号）、跨连接与跨进程读取一致、
不同数据库文件相互隔离、``--db`` 写在子命令前后等价、缺少 ``--db`` 时失败退出。
每个用例使用独立临时目录，结束后关闭连接并自动清理，不依赖仓库已有数据库。
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
    get_course,
    get_learner,
    list_learners,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定合成学员：登记顺序刻意为 Z、A、A；首尾空白应被去除，
# 内部双空格与大小写原样保留，用于证明名册按编号排列而非按姓名重排。
RAW_NAMES = [" Z学员 ", "A  学员", "A  学员"]
SAVED_NAMES = ["Z学员", "A  学员", "A  学员"]

EXPECTED_LEARNERS = [
    {"learner_id": 1, "name": "Z学员"},
    {"learner_id": 2, "name": "A  学员"},
    {"learner_id": 3, "name": "A  学员"},
]

LEARNER_FIELDS = {"learner_id", "name"}

ERR_MISSING_DB = "必须通过 --db 指定数据库文件"


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


class ListLearnersTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与空数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.conn = self.open_connection()

    def open_connection(self):
        """打开当前临时数据库并注册关闭清理（清理按注册逆序执行，
        因此连接先于临时目录关闭）。"""
        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def reopen_connection(self):
        """关闭当前连接并重新连接同一文件，模拟跨连接持久化读取。"""
        self.conn.close()
        self.conn = self.open_connection()
        return self.conn

    def register_sample_learners(self):
        """在当前连接中依次登记三位固定合成学员，返回编号列表。"""
        return [add_learner(self.conn, name) for name in RAW_NAMES]

    def assert_roster_success(self, result):
        """成功名册查询：退出码 0、标准错误为空、输出一行可解析 JSON，
        顶层只有 learners，返回解析后的名册数组。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出为单行 JSON：去掉行尾换行后不应再含换行
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload.keys()), {"learners"})
        return payload["learners"]


class TestEmptyRoster(ListLearnersTestCase):
    def test_function_returns_empty_list_on_empty_database(self):
        """空库的函数结果为 []，且每项约定不随数据量变化。"""
        self.assertEqual(list_learners(self.conn), [])

class TestAutoCreateDatabase(unittest.TestCase):
    """文件尚不存在的场景：setUp 只准备临时目录，不预先打开连接，
    以保证命令首次运行前数据库文件确实不存在。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "auto.db"

    def assert_roster_success(self, result):
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload.keys()), {"learners"})
        return payload["learners"]

    def test_cli_creates_missing_database_and_returns_empty_roster(self):
        """父目录已存在而数据库文件尚不存在时自动建库，返回空名册。"""
        self.assertFalse(self.db_path.exists())

        first = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(first), [])
        self.assertTrue(self.db_path.exists())

        # 再次查询仍为空，自动建库不产生任何学员记录
        second = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(second), [])

    def test_function_still_empty_after_cli_created_database(self):
        """CLI 在尚无文件时建库查询后，函数侧与新连接读取仍为空名册。"""
        result = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(result), [])

        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        self.assertEqual(list_learners(conn), [])


class TestRosterContents(ListLearnersTestCase):
    def test_function_returns_three_dicts_ordered_by_id(self):
        """函数返回按编号升序的三个字典，编号 1/2/3，姓名保留保存值。"""
        self.assertEqual(self.register_sample_learners(), [1, 2, 3])

        learners = list_learners(self.conn)
        self.assertEqual(learners, EXPECTED_LEARNERS)
        self.assertEqual([item["learner_id"] for item in learners], [1, 2, 3])
        self.assertEqual([item["name"] for item in learners], SAVED_NAMES)
        # 每项只含 learner_id 与 name
        for item in learners:
            self.assertEqual(set(item.keys()), LEARNER_FIELDS)

    def test_same_names_are_not_merged_or_resorted(self):
        """同名学员分别返回，名册不按姓名重排（Z 在两个 A 之前）。"""
        self.register_sample_learners()

        learners = list_learners(self.conn)
        self.assertEqual(
            [item["name"] for item in learners],
            ["Z学员", "A  学员", "A  学员"],
        )
        # 两个同名条目是相互独立的字典，编号不同
        self.assertEqual(learners[1]["name"], learners[2]["name"])
        self.assertNotEqual(
            learners[1]["learner_id"], learners[2]["learner_id"]
        )
        self.assertEqual(learners[1], {"learner_id": 2, "name": "A  学员"})
        self.assertEqual(learners[2], {"learner_id": 3, "name": "A  学员"})

    def test_saved_whitespace_and_case_preserved(self):
        """已保存姓名的首尾空白被去除，内部双空格原样保留。"""
        self.register_sample_learners()

        learners = {item["learner_id"]: item["name"] for item in
                    list_learners(self.conn)}
        self.assertEqual(learners[1], "Z学员")
        self.assertNotIn(" ", learners[1])
        self.assertEqual(learners[2], "A  学员")
        self.assertIn("  ", learners[2])
        self.assertEqual(learners[2], learners[3])

    def test_cli_roster_matches_function_result(self):
        """CLI 的名册数组与函数结果按解析后的内容完全一致。"""
        self.register_sample_learners()

        function_result = list_learners(self.conn)
        cli_result = run_cli(self.db_path, "list-learners")
        roster = self.assert_roster_success(cli_result)

        # 比较解析后的内容，不限定键顺序与分隔空格
        self.assertEqual(roster, function_result)
        self.assertEqual(roster, EXPECTED_LEARNERS)
        for item in roster:
            self.assertEqual(set(item.keys()), LEARNER_FIELDS)


class TestRosterIsReadOnly(ListLearnersTestCase):
    def test_query_does_not_change_learner_details_or_consume_ids(self):
        """查询前后三位学员详情相同，随后登记第四位学员得到编号 4。"""
        self.register_sample_learners()
        before = [get_learner(self.conn, learner_id) for learner_id in (1, 2, 3)]

        self.assertEqual(list_learners(self.conn), EXPECTED_LEARNERS)
        # 再来一次只读查询，结果应稳定
        self.assertEqual(list_learners(self.conn), EXPECTED_LEARNERS)

        after = [get_learner(self.conn, learner_id) for learner_id in (1, 2, 3)]
        self.assertEqual(after, before)
        self.assertEqual(after, EXPECTED_LEARNERS)

        # 查询不占用编号：第四位学员编号为 4，名册随之追加在末尾
        self.assertEqual(add_learner(self.conn, "学员丁"), 4)
        learners = list_learners(self.conn)
        self.assertEqual([item["learner_id"] for item in learners], [1, 2, 3, 4])
        self.assertEqual(learners[-1], {"learner_id": 4, "name": "学员丁"})

    def test_query_preserves_preexisting_course_and_chapter_order(self):
        """名册查询不改动同库课程的标题、编号与章节顺序。"""
        course_id = add_course(
            self.conn, "样例课程", ["第一章", "第二章"]
        )
        self.assertEqual(course_id, 1)
        course_before = get_course(self.conn, course_id)
        self.assertEqual(
            course_before,
            {
                "course_id": 1,
                "title": "样例课程",
                "chapters": ["第一章", "第二章"],
            },
        )

        self.register_sample_learners()
        function_roster = list_learners(self.conn)
        cli_result = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(cli_result), function_roster)

        # 课程编号、标题与章节顺序均保持不变
        self.assertEqual(get_course(self.conn, course_id), course_before)


class TestRosterPersistence(ListLearnersTestCase):
    def test_roster_unchanged_after_closing_and_reopening_connection(self):
        """关闭连接后重新打开同一文件，名册保持一致。"""
        self.register_sample_learners()
        self.assertEqual(list_learners(self.conn), EXPECTED_LEARNERS)

        conn = self.reopen_connection()
        self.assertEqual(list_learners(conn), EXPECTED_LEARNERS)

    def test_roster_unchanged_when_queried_by_another_process(self):
        """另一个进程两次查询同一文件，名册与函数结果一致。"""
        self.register_sample_learners()
        function_result = list_learners(self.conn)

        first = run_cli(self.db_path, "list-learners")
        second = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(first), function_result)
        self.assertEqual(self.assert_roster_success(second), function_result)

        # 跨进程读取后，本连接内的记录仍保持原样
        self.assertEqual(list_learners(self.conn), EXPECTED_LEARNERS)

    def test_reopened_connection_and_other_process_agree(self):
        """重连后的函数结果与另一进程的命令输出彼此一致。"""
        self.register_sample_learners()

        conn = self.reopen_connection()
        reopened_result = list_learners(conn)

        cli_result = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(cli_result), reopened_result)


class TestDatabaseIsolation(ListLearnersTestCase):
    def test_separate_empty_database_returns_empty_roster(self):
        """另一份独立空库返回空名册，不混入原库学员。"""
        self.register_sample_learners()
        other_path = Path(self._tmp.name) / "other.db"
        self.assertFalse(other_path.exists())

        other = run_cli(other_path, "list-learners")
        self.assertEqual(self.assert_roster_success(other), [])

        other_conn = connect(other_path)
        self.addCleanup(other_conn.close)
        self.assertEqual(list_learners(other_conn), [])

        # 查询空库不影响原库，原库名册仍是三位学员
        self.assertEqual(list_learners(self.conn), EXPECTED_LEARNERS)
        original = run_cli(self.db_path, "list-learners")
        self.assertEqual(
            self.assert_roster_success(original), EXPECTED_LEARNERS
        )


class TestCliConventions(ListLearnersTestCase):
    def test_db_option_before_and_after_subcommand_are_equivalent(self):
        """--db 写在 list-learners 前后解析结果一致，且与函数结果一致。"""
        self.register_sample_learners()
        function_result = list_learners(self.conn)

        before = run_cli(
            self.db_path, "list-learners", db_first=True
        )
        after = run_cli(
            self.db_path, "list-learners", db_first=False
        )

        self.assertEqual(self.assert_roster_success(before), function_result)
        self.assertEqual(self.assert_roster_success(after), function_result)
        self.assertEqual(
            json.loads(before.stdout), json.loads(after.stdout)
        )

    def test_missing_db_option_exits_with_code_2(self):
        """缺少 --db：退出码 2，标准输出为空，标准错误仅为指定提示。"""
        result = subprocess.run(
            [sys.executable, "-m", "course_progress", "list-learners"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), ERR_MISSING_DB)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
