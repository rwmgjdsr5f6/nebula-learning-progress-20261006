"""学员名册（list-learners）公开行为的回归测试。

同时核对包导出的 ``list_learners`` 返回值与
``python -m course_progress list-learners`` 的命令输出，确认两条入口
遵循相同约定：按编号升序、同名学员不合并、已保存姓名的大小写与内部
空白原样保留、每项只含 learner_id 与 name、空库返回空名册、查询为
只读操作、``--db`` 写在子命令前后等价、缺少 ``--db`` 时失败退出。
每个用例使用独立临时目录，结束后关闭连接并自动清理，不依赖仓库中
已有数据库或联网。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import course_progress

# 固定合成学员：刻意按 Z、A、A 的顺序登记，且姓名含首尾空白与内部
# 双空格，用于证明名册按编号排列而非按姓名重排、同名学员不被合并、
# 已保存姓名（去首尾空白后）的大小写与内部空白原样保留。
SAMPLE_NAMES = [" Z学员 ", "A  学员", "A  学员"]

EXPECTED_LEARNERS = [
    {"learner_id": 1, "name": "Z学员"},
    {"learner_id": 2, "name": "A  学员"},
    {"learner_id": 3, "name": "A  学员"},
]

LEARNER_FIELDS = {"learner_id", "name"}


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


def list_learners_cli(db_path, db_first=True):
    """通过 CLI 查询名册，返回解析后的 {"learners": [...]} 字典。"""
    result = run_cli(db_path, "list-learners", db_first=db_first)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class ListLearnersTestCase(unittest.TestCase):
    """每个用例一个独立临时目录与数据库，互不影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "learners.db"

    def open_db(self):
        """打开（必要时创建）临时数据库，用例结束时自动关闭连接。"""
        conn = course_progress.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def register_sample_learners(self, conn):
        """在同一连接中依次登记三名固定合成学员，返回各自编号。"""
        learner_ids = [
            course_progress.add_learner(conn, name) for name in SAMPLE_NAMES
        ]
        self.assertEqual(learner_ids, [1, 2, 3])
        return learner_ids

    def assert_roster_success(self, result):
        """成功名册查询：退出码 0、标准错误为空、输出一行可解析 JSON。"""
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 标准输出为单行 JSON：去掉行尾换行后不应再含换行
        self.assertEqual(len(result.stdout.splitlines()), 1)
        return json.loads(result.stdout)


class TestListLearnersContent(ListLearnersTestCase):
    def test_function_returns_roster_ordered_by_id(self):
        """函数返回按编号升序的三个字典，同名不合并、姓名原样保留。"""
        conn = self.open_db()
        self.register_sample_learners(conn)

        learners = course_progress.list_learners(conn)

        self.assertEqual(learners, EXPECTED_LEARNERS)
        self.assertEqual([l["learner_id"] for l in learners], [1, 2, 3])
        self.assertEqual(
            [l["name"] for l in learners], ["Z学员", "A  学员", "A  学员"]
        )
        # 每项只含 learner_id 与 name，内部双空格与大小写原样保留
        for learner in learners:
            self.assertEqual(set(learner.keys()), LEARNER_FIELDS)

    def test_cli_output_matches_function_result(self):
        """CLI 输出的 learners 数组与函数返回值一致，顶层只有 learners。"""
        conn = self.open_db()
        self.register_sample_learners(conn)
        expected = course_progress.list_learners(conn)
        conn.close()

        result = run_cli(self.db_path, "list-learners")
        payload = self.assert_roster_success(result)

        # 按解析后的内容比较，不限定键顺序和分隔空格
        self.assertEqual(set(payload.keys()), {"learners"})
        self.assertEqual(payload["learners"], expected)
        self.assertEqual(payload, {"learners": EXPECTED_LEARNERS})


class TestEmptyDatabase(ListLearnersTestCase):
    def test_function_returns_empty_list_on_empty_db(self):
        """空库的函数结果为 []。"""
        conn = self.open_db()
        self.assertEqual(course_progress.list_learners(conn), [])

    def test_cli_creates_missing_database_and_returns_empty_roster(self):
        """父目录已存在但数据库文件尚不存在时，命令自动建库并返回空名册。"""
        self.assertFalse(self.db_path.exists())

        result = run_cli(self.db_path, "list-learners")
        payload = self.assert_roster_success(result)
        self.assertEqual(payload, {"learners": []})
        self.assertTrue(self.db_path.exists())

        # 再次查询仍为空
        again = run_cli(self.db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(again), {"learners": []})

        # 空库经 CLI 建库后，函数结果同样为空
        conn = self.open_db()
        self.assertEqual(course_progress.list_learners(conn), [])


class TestListIsReadOnly(ListLearnersTestCase):
    def test_query_does_not_modify_records_or_consume_ids(self):
        """查询前后学员详情相同，随后登记的学员得到下一个编号。"""
        conn = self.open_db()
        self.register_sample_learners(conn)

        before = [
            course_progress.get_learner(conn, learner_id)
            for learner_id in (1, 2, 3)
        ]
        self.assertEqual(before, EXPECTED_LEARNERS)

        # 函数与 CLI 各查询一次，均不应改动已保存记录
        self.assertEqual(course_progress.list_learners(conn), before)
        conn.close()
        self.assertEqual(
            list_learners_cli(self.db_path), {"learners": EXPECTED_LEARNERS}
        )

        conn = self.open_db()
        after = [
            course_progress.get_learner(conn, learner_id)
            for learner_id in (1, 2, 3)
        ]
        self.assertEqual(after, before)

        # 查询不占用编号：第四位学员编号为 4
        self.assertEqual(course_progress.add_learner(conn, "新学员"), 4)
        self.assertEqual(
            course_progress.list_learners(conn),
            EXPECTED_LEARNERS + [{"learner_id": 4, "name": "新学员"}],
        )

    def test_query_leaves_courses_untouched(self):
        """同库预先登记的课程，其标题、编号和章节顺序在查询后保持不变。"""
        conn = self.open_db()
        course_id = course_progress.add_course(conn, "入门培训", ["准备", "学习"])
        self.assertEqual(course_id, 1)
        self.register_sample_learners(conn)
        expected_course = {
            "course_id": 1,
            "title": "入门培训",
            "chapters": ["准备", "学习"],
        }
        self.assertEqual(course_progress.get_course(conn, 1), expected_course)

        self.assertEqual(
            course_progress.list_learners(conn), EXPECTED_LEARNERS
        )
        conn.close()
        list_learners_cli(self.db_path)

        conn = self.open_db()
        self.assertEqual(course_progress.get_course(conn, 1), expected_course)
        # 课程概览也不受名册查询影响
        self.assertEqual(
            course_progress.list_courses(conn),
            [{"course_id": 1, "title": "入门培训", "chapter_count": 2}],
        )


class TestPersistenceAndIsolation(ListLearnersTestCase):
    def test_reopen_and_other_process_see_same_roster(self):
        """关闭连接后重新打开同一文件，以及另一个进程查询，名册一致。"""
        conn = self.open_db()
        self.register_sample_learners(conn)
        expected = course_progress.list_learners(conn)
        conn.close()

        reopened = self.open_db()
        self.assertEqual(course_progress.list_learners(reopened), expected)
        reopened.close()

        # 另一个进程（CLI）再次查询，得到相同名册
        cli_payload = list_learners_cli(self.db_path)
        self.assertEqual(cli_payload, {"learners": expected})

    def test_separate_empty_database_stays_empty(self):
        """另一份独立空库返回空名册，不会混入原库学员。"""
        conn = self.open_db()
        self.register_sample_learners(conn)
        conn.close()

        other_db_path = Path(self._tmp.name) / "other.db"
        self.assertFalse(other_db_path.exists())

        other = run_cli(other_db_path, "list-learners")
        self.assertEqual(self.assert_roster_success(other), {"learners": []})

        other_conn = course_progress.connect(other_db_path)
        self.addCleanup(other_conn.close)
        self.assertEqual(course_progress.list_learners(other_conn), [])

        # 查询空库不影响原库，原库名册仍为三名学员
        original = run_cli(self.db_path, "list-learners")
        self.assertEqual(
            self.assert_roster_success(original),
            {"learners": EXPECTED_LEARNERS},
        )


class TestDbOptionPlacement(ListLearnersTestCase):
    def test_db_option_before_and_after_subcommand_are_equivalent(self):
        """--db 写在 list-learners 前后结果相同。"""
        conn = self.open_db()
        self.register_sample_learners(conn)
        conn.close()

        before = run_cli(self.db_path, "list-learners", db_first=True)
        after = run_cli(self.db_path, "list-learners", db_first=False)

        self.assertEqual(
            self.assert_roster_success(before), {"learners": EXPECTED_LEARNERS}
        )
        self.assertEqual(
            self.assert_roster_success(after), {"learners": EXPECTED_LEARNERS}
        )
        # 两种写法的解析结果彼此一致
        self.assertEqual(json.loads(before.stdout), json.loads(after.stdout))

    def test_missing_db_option_exits_with_code_2(self):
        """缺少 --db：退出码 2，标准输出为空，标准错误仅为提示语。"""
        result = subprocess.run(
            [sys.executable, "-m", "course_progress", "list-learners"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(), "必须通过 --db 指定数据库文件"
        )
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
