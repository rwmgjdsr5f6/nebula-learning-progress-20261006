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
ERR_EMPTY_LEARNER_NAME = "学员姓名不能为空"
ERR_EMPTY_LEARNER_NAME_FILTER = "学员姓名筛选词不能为空"
ERR_LEARNER_NOT_FOUND = "学员不存在"
ERR_BAD_LEARNER_ID = "学员编号必须为正整数"

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
CREATE TABLE IF NOT EXISTS learners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
    learner_id INTEGER NOT NULL REFERENCES learners (id),
    course_id INTEGER NOT NULL REFERENCES courses (id),
    PRIMARY KEY (learner_id, course_id)
);
"""


class ValidationError(Exception):
    """登记内容校验失败，消息为面向用户的中文提示。"""


# SQLite 整数为 64 位有符号；超出该范围的编号不可能存在于库中。
_SQLITE_INT64_MIN = -(2**63)
_SQLITE_INT64_MAX = 2**63 - 1

# 概览读取的共有规则：只取课程编号、标题与章节数（LEFT JOIN 保证没有
# 章节的课程章节数为 0），按课程编号聚合去重并升序排列。两种概览查询
# 的差异仅在课程范围（FROM/WHERE 子句）。
_ALL_COURSES_SCOPE = """
FROM courses AS c
LEFT JOIN chapters AS ch ON ch.course_id = c.id
"""
_LEARNER_COURSES_SCOPE = """
FROM enrollments AS e
JOIN courses AS c ON c.id = e.course_id
LEFT JOIN chapters AS ch ON ch.course_id = c.id
WHERE e.learner_id = ?
"""


def _list_course_overviews(conn, scope_sql, params=()):
    """按给定课程范围读取概览，每项整理为 course_id/title/chapter_count。

    scope_sql 提供 FROM/WHERE 部分，且都以课程表别名 c 关联章节表别名 ch；
    编号、标题、章节数的读取与结果整理在此集中维护，保证全部课程概览与
    学员课程概览始终一致。
    """
    rows = conn.execute(
        f"""
        SELECT c.id, c.title, COUNT(ch.position)
        {scope_sql}
        GROUP BY c.id
        ORDER BY c.id
        """,
        params,
    )
    return [
        {"course_id": course_id, "title": title, "chapter_count": chapter_count}
        for course_id, title, chapter_count in rows
    ]


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

    title_contains 省略或为 None 时返回全部课程；否则筛选词先去除首尾
    空白（保留内部空白与大小写），再与课程标题做大小写敏感的连续子串
    匹配，不匹配章节名称，百分号、下划线、引号等字符一律按普通字符
    处理。显式传入空字符串或仅含空白的筛选词时抛出
    ValidationError(ERR_EMPTY_TITLE_FILTER)。筛选为只读操作，不修改
    任何记录。
    """
    if title_contains is not None:
        title_contains = title_contains.strip()
        if not title_contains:
            raise ValidationError(ERR_EMPTY_TITLE_FILTER)
    courses = _list_course_overviews(conn, _ALL_COURSES_SCOPE)
    if title_contains is not None:
        courses = [
            course for course in courses if title_contains in course["title"]
        ]
    return courses


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


def _save_chapter_order(conn, course_id, chapter_count, ordered_names,
                        delete_position=None):
    """在调用方已开启的事务内保存课程章节的最终顺序（重排与删除共用）。

    两条写路径的落位规则完全相同，集中在此一处维护：先把该课程全部章节
    的 position 整体上移 chapter_count，避开 (course_id, position) 主键
    冲突；若给出 delete_position，则删掉平移到
    delete_position + chapter_count 的目标行（删除章节路径；重排路径不传，
    全部章节都参与落位）；随后按 ordered_names 从 0 开始逐章落位，落位后
    position 重新连续。

    chapter_count 为操作前的章节数（即平移量），ordered_names 为操作后
    保留章节按目标顺序排列的名称。调用方须先完成全部输入校验：本函数不做
    任何校验，也不自行开启或提交事务；写入中途的约束失败原样抛出
    sqlite3.IntegrityError，由调用方所在的事务整体回滚。
    """
    # 先整体平移 position 避开主键冲突，再按需删除目标章节并按新顺序落位
    conn.execute(
        "UPDATE chapters SET position = position + ? WHERE course_id = ?",
        (chapter_count, course_id),
    )
    if delete_position is not None:
        conn.execute(
            "DELETE FROM chapters WHERE course_id = ? AND position = ?",
            (course_id, delete_position + chapter_count),
        )
    for position, name in enumerate(ordered_names):
        conn.execute(
            "UPDATE chapters SET position = ?"
            " WHERE course_id = ? AND name = ?",
            (position, course_id, name),
        )


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
        _save_chapter_order(conn, course_id, len(existing), names)
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
        _save_chapter_order(
            conn,
            course_id,
            len(existing),
            remaining,
            delete_position=target_position,
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


def add_learner(conn, name):
    """登记一个学员，返回新学员编号。

    姓名去除首尾空白后保存（保留内部空白与大小写）；同名学员分别登记，
    不合并记录，且与课程编号互不影响。校验失败抛出 ValidationError，
    且不在数据库中留下任何记录。
    """
    name = name.strip()
    if not name:
        raise ValidationError(ERR_EMPTY_LEARNER_NAME)
    with conn:
        cursor = conn.execute("INSERT INTO learners (name) VALUES (?)", (name,))
        learner_id = cursor.lastrowid
    return learner_id


def rename_learner(conn, learner_id, name):
    """按编号修改学员姓名，返回 {"learner_id", "name"}；学员不存在返回 None。

    新姓名去除首尾空白后保存（保留内部空白与大小写）。按编号范围、学员
    存在性、姓名非空的顺序判定：学员不存在时返回 None，即使姓名为空也不
    抛出 ValidationError；学员存在但姓名去空白后为空时抛出
    ValidationError(ERR_EMPTY_LEARNER_NAME)。允许与其他学员同名，不合并
    记录；仅更新目标学员的姓名，编号与其他学员、课程数据均不变。
    """
    if not _SQLITE_INT64_MIN <= learner_id <= _SQLITE_INT64_MAX:
        return None
    name = (name or "").strip()
    with conn:
        row = conn.execute(
            "SELECT 1 FROM learners WHERE id = ?", (learner_id,)
        ).fetchone()
        if row is None:
            return None
        if not name:
            raise ValidationError(ERR_EMPTY_LEARNER_NAME)
        conn.execute(
            "UPDATE learners SET name = ? WHERE id = ?", (name, learner_id)
        )
    return {"learner_id": learner_id, "name": name}


def list_learners(conn):
    """查询全部学员名册，按学员编号升序返回。

    每项为 {"learner_id", "name"}，姓名使用数据库中已保存的值（保留内部
    空白与大小写）；同名学员分别返回。数据库中没有学员时返回空列表。
    查询为只读操作，不修改任何记录。
    """
    rows = conn.execute("SELECT id, name FROM learners ORDER BY id")
    return [
        {"learner_id": learner_id, "name": name}
        for learner_id, name in rows
    ]


def get_learner(conn, learner_id):
    """按编号查询学员，返回 {"learner_id", "name"}；不存在时返回 None。

    超出 SQLite 整数范围的编号不可能对应任何学员，直接返回 None，
    避免把 OverflowError 暴露给调用方。
    """
    if not _SQLITE_INT64_MIN <= learner_id <= _SQLITE_INT64_MAX:
        return None
    row = conn.execute(
        "SELECT name FROM learners WHERE id = ?", (learner_id,)
    ).fetchone()
    if row is None:
        return None
    return {"learner_id": learner_id, "name": row[0]}


def _resolve_enrollment_target(conn, learner_id, course_id):
    """报名与取消报名共用的目标校验，通过时返回 (learner_id, course_id)，
    否则返回 None。

    两种操作的共同目标判定——两个编号都落在 SQLite 64 位有符号整数范围
    内、学员存在、课程存在（按此顺序短路）——在此一处维护：命令行虽然已
    做过同一套前置校验，两个存储函数被直接调用时仍各自独立经过本判定，
    不依赖调用方预先检查。超出范围的编号按不存在处理，避免把
    OverflowError 暴露给调用方。本函数只读取证，不开启事务也不修改任何
    记录；是否写入、写入什么由调用方在自己的事务中决定。
    """
    if not _SQLITE_INT64_MIN <= learner_id <= _SQLITE_INT64_MAX:
        return None
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    if conn.execute(
        "SELECT 1 FROM learners WHERE id = ?", (learner_id,)
    ).fetchone() is None:
        return None
    if conn.execute(
        "SELECT 1 FROM courses WHERE id = ?", (course_id,)
    ).fetchone() is None:
        return None
    return learner_id, course_id


def enroll_learner(conn, learner_id, course_id):
    """登记学员与课程的报名关系，返回 {"learner_id", "course_id"}；
    学员或课程不存在时返回 None，且不写入任何记录。

    一个学员可报名多门课程，一门课程可接收多个学员；同一编号组合重复
    报名仍成功并返回相同内容，名册中只保留一条记录。目标是否有效的判定
    （编号范围与学员、课程存在性）与取消报名共用
    _resolve_enrollment_target，本函数被直接调用时同样独立校验。仅记录
    报名关系，不产生学习进度或结业结果。
    """
    with conn:
        target = _resolve_enrollment_target(conn, learner_id, course_id)
        if target is None:
            return None
        learner_id, course_id = target
        conn.execute(
            "INSERT OR IGNORE INTO enrollments (learner_id, course_id)"
            " VALUES (?, ?)",
            (learner_id, course_id),
        )
    return {"learner_id": learner_id, "course_id": course_id}


def unenroll_learner(conn, learner_id, course_id):
    """移除学员与课程的报名关系，返回 {"learner_id", "course_id"}；
    学员或课程不存在时返回 None，且不修改任何记录。

    从未报名或已经取消的编号组合同样成功并返回相同内容；只移除该编号
    组合的关系，课程、学员及其他报名关系不变。目标是否有效的判定
    （编号范围与学员、课程存在性）与报名共用 _resolve_enrollment_target，
    本函数被直接调用时同样独立校验。不产生学习进度或结业结果。
    """
    with conn:
        target = _resolve_enrollment_target(conn, learner_id, course_id)
        if target is None:
            return None
        learner_id, course_id = target
        conn.execute(
            "DELETE FROM enrollments WHERE learner_id = ? AND course_id = ?",
            (learner_id, course_id),
        )
    return {"learner_id": learner_id, "course_id": course_id}


def list_learner_courses(conn, learner_id):
    """查询一个学员已报名的课程，按课程编号升序返回；学员不存在返回 None。

    每项为 {"course_id", "title", "chapter_count"}，标题与章节数使用课程
    当前保存的值（课程改名或章节增删后随之变化），不含章节名称与学习
    完成状态；只列该学员实际报名的课程，同一课程至多出现一次。学员存在
    但尚未报名时返回空列表。超出 SQLite 整数范围的编号按不存在处理。
    查询为只读操作，不修改任何记录。
    """
    if not _SQLITE_INT64_MIN <= learner_id <= _SQLITE_INT64_MAX:
        return None
    row = conn.execute(
        "SELECT 1 FROM learners WHERE id = ?", (learner_id,)
    ).fetchone()
    if row is None:
        return None
    return _list_course_overviews(
        conn, _LEARNER_COURSES_SCOPE, (learner_id,)
    )


def list_course_learners(conn, course_id, name_contains=None):
    """查询一门课程的报名名册，按学员编号升序返回；课程不存在返回 None。

    每项为 {"learner_id", "name"}，姓名使用数据库中当前保存的值；只列
    已报名学员，课程存在但无人报名时返回空列表。超出 SQLite 整数范围的
    编号按不存在处理。查询为只读操作，不修改任何记录。

    name_contains 省略或为 None 时返回全部已报名学员；否则筛选词先去除
    首尾空白（保留内部空白与大小写），再与学员当前保存的完整姓名做大小写
    敏感的连续子串匹配，只在该课程已报名学员中筛选，不匹配其他课程或未
    报名学员，百分号、下划线、引号等字符一律按普通字符处理。课程存在且
    显式传入空字符串或仅含空白的筛选词时抛出
    ValidationError(ERR_EMPTY_LEARNER_NAME_FILTER)；课程不存在时即使
    筛选词为空也返回 None，不抛出 ValidationError。
    """
    if not _SQLITE_INT64_MIN <= course_id <= _SQLITE_INT64_MAX:
        return None
    row = conn.execute(
        "SELECT 1 FROM courses WHERE id = ?", (course_id,)
    ).fetchone()
    if row is None:
        return None
    if name_contains is not None:
        name_contains = name_contains.strip()
        if not name_contains:
            raise ValidationError(ERR_EMPTY_LEARNER_NAME_FILTER)
    rows = conn.execute(
        """
        SELECT l.id, l.name
        FROM enrollments AS e
        JOIN learners AS l ON l.id = e.learner_id
        WHERE e.course_id = ?
        ORDER BY l.id
        """,
        (course_id,),
    )
    learners = [
        {"learner_id": learner_id, "name": name}
        for learner_id, name in rows
    ]
    if name_contains is not None:
        learners = [
            learner for learner in learners
            if name_contains in learner["name"]
        ]
    return learners
