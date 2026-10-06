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


# SQLite 整数为 64 位有符号；超出该范围的编号不可能存在于库中。
_SQLITE_INT64_MIN = -(2**63)
_SQLITE_INT64_MAX = 2**63 - 1


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


def list_courses(conn):
    """查询全部课程概览，按课程编号升序返回。

    每项为 {"course_id", "title", "chapter_count"}，不含章节名称；
    数据库中没有课程时返回空列表。
    """
    rows = conn.execute(
        """
        SELECT c.id, c.title, COUNT(ch.position)
        FROM courses AS c
        LEFT JOIN chapters AS ch ON ch.course_id = c.id
        GROUP BY c.id
        ORDER BY c.id
        """
    )
    return [
        {"course_id": course_id, "title": title, "chapter_count": chapter_count}
        for course_id, title, chapter_count in rows
    ]


def rename_course(conn, course_id, title):
    """按编号修改课程标题，返回 {"course_id", "title"}；课程不存在返回 None。

    新标题去除首尾空白后保存（保留内部空白与大小写）。按编号范围、课程
    存在性、标题非空的顺序判定：课程不存在时返回 None，即使标题为空也不
    抛出 ValidationError；课程存在但标题去空白后为空时抛出 ValidationError。
    仅更新标题，课程编号与章节的名称、数量、顺序不变。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    title = title.strip()
    with conn:
        row = conn.execute(
            "SELECT 1 FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if row is None:
            return None
        if not title:
            raise ValidationError(ERR_EMPTY_TITLE)
        conn.execute(
            "UPDATE courses SET title = ? WHERE id = ?", (title, course_id)
        )
    return {"course_id": course_id, "title": title}


def append_chapter(conn, course_id, name):
    """向已有课程末尾追加一个章节，返回 {"course_id", "chapter_count"}；
    课程不存在返回 None。

    章节名去除首尾空白后保存（保留内部空白与大小写），排在原章节列表
    最后。按编号范围、课程存在性、章节非空、重名的顺序判定：课程不存在
    时返回 None，即使章节名为空也不抛出 ValidationError；课程存在但章节
    名去首尾空白后为空时抛出 ValidationError(ERR_EMPTY_CHAPTER)；与该课程
    已有章节重名（按去首尾空白后的名称比较，大小写敏感；其他课程的同名
    章节不影响）时抛出 ValidationError(ERR_DUP_CHAPTER)。课程标题、编号与
    此前章节的名称、顺序不变。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    name = (name or "").strip()
    with conn:
        row = conn.execute(
            "SELECT 1 FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if row is None:
            return None
        if not name:
            raise ValidationError(ERR_EMPTY_CHAPTER)
        existing = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM chapters WHERE course_id = ?"
                " ORDER BY position",
                (course_id,),
            )
        ]
        if name in existing:
            raise ValidationError(ERR_DUP_CHAPTER)
        position = len(existing)
        conn.execute(
            "INSERT INTO chapters (course_id, position, name) VALUES (?, ?, ?)",
            (course_id, position, name),
        )
    return {"course_id": course_id, "chapter_count": position + 1}


def get_course(conn, course_id):
    """按编号查询课程，返回 dict；不存在时返回 None。

    超出 SQLite 整数范围的编号不可能对应任何课程，直接返回 None，
    避免把 OverflowError 暴露给调用方。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
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
