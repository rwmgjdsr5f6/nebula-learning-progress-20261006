"""课程登记与详情查询的存储逻辑（SQLite，仅标准库）。"""

import sqlite3

ERR_EMPTY_TITLE = "课程标题不能为空"
ERR_EMPTY_TITLE_FILTER = "课程标题筛选词不能为空"
ERR_EMPTY_CHAPTER = "章节不能为空"
ERR_CHAPTER_NOT_FOUND = "章节不存在"
ERR_DUP_CHAPTER = "章节名重复"
ERR_CHAPTER_LIST_MISMATCH = "章节列表与现有章节不一致"
ERR_LAST_CHAPTER = "课程至少保留一个章节"
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


def list_courses(conn, title_contains=None):
    """查询全部课程概览，按课程编号升序返回。

    每项为 {"course_id", "title", "chapter_count"}，不含章节名称；
    数据库中没有课程时返回空列表。

    title_contains 为 None（或省略）时返回全部课程；否则筛选词先去除
    首尾空白（保留内部空白），再与课程标题做大小写敏感的连续子串匹配，
    不匹配章节名称；百分号、下划线、引号等字符一律按普通字符处理，
    不具有通配含义。显式传入空字符串或仅含空白的筛选词时抛出
    ValidationError。匹配在 Python 侧完成，避免 SQLite LIKE 对 ASCII
    字符默认大小写不敏感。
    """
    needle = None
    if title_contains is not None:
        needle = title_contains.strip()
        if not needle:
            raise ValidationError(ERR_EMPTY_TITLE_FILTER)
    rows = conn.execute(
        """
        SELECT c.id, c.title, COUNT(ch.position)
        FROM courses AS c
        LEFT JOIN chapters AS ch ON ch.course_id = c.id
        GROUP BY c.id
        ORDER BY c.id
        """
    )
    if needle is not None:
        rows = (row for row in rows if needle in row[1])
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


def rename_chapter(conn, course_id, chapter, name):
    """修改已有课程中单个章节的名称，返回与 get_course 同结构的课程详情；
    课程不存在返回 None。

    原章节名与新名称均去除首尾空白（保留内部空白与大小写）；原名称按
    大小写敏感的精确匹配在该课程的章节中定位。按编号范围、课程存在性、
    原名称非空、章节存在、新名称非空、重名的顺序判定：课程不存在时返回
    None，即使名称为空也不抛出 ValidationError；原名称去首尾空白后为空
    时抛出 ValidationError(ERR_EMPTY_CHAPTER)；原名称非空但该课程中没有
    此章节时抛出 ValidationError(ERR_CHAPTER_NOT_FOUND)；新名称去首尾空白
    后为空时抛出 ValidationError(ERR_EMPTY_CHAPTER)；新名称与同课程其他
    章节重名（大小写敏感；与目标章节自身当前名称相同不算重复，其他课程
    的同名章节不影响）时抛出 ValidationError(ERR_DUP_CHAPTER)。
    仅替换目标章节的名称，课程编号、标题、章节数量与顺序不变。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    chapter = (chapter or "").strip()
    name = (name or "").strip()
    with conn:
        row = conn.execute(
            "SELECT 1 FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if row is None:
            return None
        if not chapter:
            raise ValidationError(ERR_EMPTY_CHAPTER)
        existing = [
            (position, chapter_name)
            for position, chapter_name in conn.execute(
                "SELECT position, name FROM chapters WHERE course_id = ?"
                " ORDER BY position",
                (course_id,),
            )
        ]
        positions = {chapter_name: position for position, chapter_name in existing}
        if chapter not in positions:
            raise ValidationError(ERR_CHAPTER_NOT_FOUND)
        if not name:
            raise ValidationError(ERR_EMPTY_CHAPTER)
        target_position = positions[chapter]
        if any(
            chapter_name == name
            for position, chapter_name in existing
            if position != target_position
        ):
            raise ValidationError(ERR_DUP_CHAPTER)
        conn.execute(
            "UPDATE chapters SET name = ? WHERE course_id = ? AND position = ?",
            (name, course_id, target_position),
        )
    return get_course(conn, course_id)


def reorder_chapters(conn, course_id, chapters):
    """重排已有课程的全部章节，返回与 get_course 同结构的课程详情；
    课程不存在返回 None。

    每个章节名去除首尾空白（保留内部空白与大小写），按大小写敏感的
    精确匹配与现有章节对应；给出的顺序即新的章节顺序。按编号范围、
    课程存在性、章节非空、重名、列表一致性的顺序判定：课程不存在时
    返回 None，即使章节列表为空也不抛出 ValidationError；列表为空或
    任一名称去首尾空白后为空时抛出 ValidationError(ERR_EMPTY_CHAPTER)；
    去空白后名称重复时抛出 ValidationError(ERR_DUP_CHAPTER)；其余情况
    下遗漏现有章节或包含未知名称（数量不符、名称增删或改名）时抛出
    ValidationError(ERR_CHAPTER_LIST_MISMATCH)。仅调整章节顺序，课程
    编号、标题、章节名称与数量不变；校验失败时不修改任何数据。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    names = [name.strip() for name in chapters]
    with conn:
        row = conn.execute(
            "SELECT 1 FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if row is None:
            return None
        if not names or any(not name for name in names):
            raise ValidationError(ERR_EMPTY_CHAPTER)
        if len(set(names)) != len(names):
            raise ValidationError(ERR_DUP_CHAPTER)
        existing = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM chapters WHERE course_id = ?"
                " ORDER BY position",
                (course_id,),
            )
        ]
        if set(names) != set(existing):
            raise ValidationError(ERR_CHAPTER_LIST_MISMATCH)
        # 先整体平移 position 避开主键冲突，再按新顺序落位
        conn.execute(
            "UPDATE chapters SET position = position + ? WHERE course_id = ?",
            (len(existing), course_id),
        )
        for position, name in enumerate(names):
            conn.execute(
                "UPDATE chapters SET position = ?"
                " WHERE course_id = ? AND name = ?",
                (position, course_id, name),
            )
    return get_course(conn, course_id)


def remove_chapter(conn, course_id, chapter):
    """删除已有课程中的单个章节，返回与 get_course 同结构的课程详情；
    课程不存在返回 None。

    章节名去除首尾空白（保留内部空白与大小写），按大小写敏感的精确匹配
    在该课程的章节中定位；删除后其余章节相对顺序不变、position 重新连续。
    按编号范围、课程存在性、名称非空、章节存在、是否唯一章节的顺序判定：
    课程不存在时返回 None，即使名称为空也不抛出 ValidationError；名称去
    首尾空白后为空时抛出 ValidationError(ERR_EMPTY_CHAPTER)；名称非空但
    该课程中没有此章节时抛出 ValidationError(ERR_CHAPTER_NOT_FOUND)；该
    章节是课程唯一章节时抛出 ValidationError(ERR_LAST_CHAPTER)。仅删除
    目标章节，课程编号、标题、其他课程均不变；校验失败时不修改任何数据。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    chapter = (chapter or "").strip()
    with conn:
        row = conn.execute(
            "SELECT 1 FROM courses WHERE id = ?", (course_id,)
        ).fetchone()
        if row is None:
            return None
        if not chapter:
            raise ValidationError(ERR_EMPTY_CHAPTER)
        existing = [
            (position, chapter_name)
            for position, chapter_name in conn.execute(
                "SELECT position, name FROM chapters WHERE course_id = ?"
                " ORDER BY position",
                (course_id,),
            )
        ]
        target_position = next(
            (
                position
                for position, chapter_name in existing
                if chapter_name == chapter
            ),
            None,
        )
        if target_position is None:
            raise ValidationError(ERR_CHAPTER_NOT_FOUND)
        if len(existing) == 1:
            raise ValidationError(ERR_LAST_CHAPTER)
        remaining = [
            chapter_name
            for position, chapter_name in existing
            if position != target_position
        ]
        # 先整体平移 position 避开主键冲突，再删掉目标章节并按剩余顺序落位
        conn.execute(
            "UPDATE chapters SET position = position + ? WHERE course_id = ?",
            (len(existing), course_id),
        )
        conn.execute(
            "DELETE FROM chapters WHERE course_id = ? AND position = ?",
            (course_id, target_position + len(existing)),
        )
        for position, name in enumerate(remaining):
            conn.execute(
                "UPDATE chapters SET position = ?"
                " WHERE course_id = ? AND name = ?",
                (position, course_id, name),
            )
    return get_course(conn, course_id)


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
