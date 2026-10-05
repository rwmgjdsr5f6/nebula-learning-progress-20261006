"""课程登记与详情查询的存储逻辑（SQLite，仅标准库）。"""

import sqlite3

ERR_EMPTY_TITLE = "课程标题不能为空"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_DUP_CHAPTER = "章节名重复"
ERR_NOT_FOUND = "课程不存在"
ERR_BAD_ID = "课程编号必须为正整数"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapters (
    course_id INTEGER NOT NULL REFERENCES courses (id),
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (course_id, position)
);
"""


class ValidationError(Exception):
    """登记内容校验失败，消息为面向用户的中文提示。"""


def connect(db_path):
    """打开（必要时创建）数据库并确保表结构存在。父目录需已存在。"""
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def add_course(conn, title, chapters):
    """登记一门课程及其有序章节，返回新课程编号。

    标题与章节名去除首尾空白后保存；校验失败抛出 ValidationError，
    且不在数据库中留下任何记录。
    """
    title = title.strip()
    names = [name.strip() for name in chapters]
    if not title:
        raise ValidationError(ERR_EMPTY_TITLE)
    if not names or any(not name for name in names):
        raise ValidationError(ERR_EMPTY_CHAPTER)
    if len(set(names)) != len(names):
        raise ValidationError(ERR_DUP_CHAPTER)
    with conn:
        cursor = conn.execute("INSERT INTO courses (title) VALUES (?)", (title,))
        course_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO chapters (course_id, position, name) VALUES (?, ?, ?)",
            [(course_id, position, name) for position, name in enumerate(names)],
        )
    return course_id


def get_course(conn, course_id):
    """按编号查询课程，返回 dict；不存在时返回 None。"""
    row = conn.execute(
        "SELECT title FROM courses WHERE id = ?", (course_id,)
    ).fetchone()
    if row is None:
        return None
    chapters = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM chapters WHERE course_id = ? ORDER BY position",
            (course_id,),
        )
    ]
    return {"course_id": course_id, "title": row[0], "chapters": chapters}
