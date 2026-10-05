"""SQLite 数据访问层：课程及其有序章节。"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    course_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (course_id, position),
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
);
"""


def connect(db_path):
    """打开（必要时创建）数据库并确保表结构就绪。"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def add_course(conn, title, chapters):
    """在单个事务中登记课程及章节，返回新课程编号。

    调用方需保证 title 已去除首尾空白且非空，chapters 为已去空白、
    非空且不重复的章节名列表。
    """
    try:
        cursor = conn.execute("INSERT INTO courses (title) VALUES (?)", (title,))
        course_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO chapters (course_id, position, name) VALUES (?, ?, ?)",
            [(course_id, position, name) for position, name in enumerate(chapters, start=1)],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return course_id


def get_course(conn, course_id):
    """按编号读取课程，返回 (title, chapters)；不存在时返回 None。"""
    row = conn.execute("SELECT title FROM courses WHERE id = ?", (course_id,)).fetchone()
    if row is None:
        return None
    chapters = [
        name
        for (name,) in conn.execute(
            "SELECT name FROM chapters WHERE course_id = ? ORDER BY position",
            (course_id,),
        )
    ]
    return row[0], chapters
