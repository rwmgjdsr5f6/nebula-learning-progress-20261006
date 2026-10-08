"""命令行入口：python -m course_progress --db <文件> <子命令>。"""

import argparse
import json
import re
import sqlite3
import sys

from .core import (
    ERR_BAD_ID,
    ERR_BAD_LEARNER_ID,
    ERR_LEARNER_NOT_FOUND,
    ERR_NOT_FOUND,
    ValidationError,
    add_course,
    add_learner,
    append_chapter,
    connect,
    enroll_learner,
    get_course,
    get_learner,
    list_course_learners,
    list_courses,
    list_learner_courses,
    list_learners,
    remove_chapter,
    rename_chapter,
    rename_course,
    rename_learner,
    reorder_chapters,
    unenroll_learner,
)

_ID_PATTERN = re.compile(r"[+-]?\d+")

# 报名关系命令共用的存储函数：两个命令的参数解析与校验流程完全一致，
# 仅最终调用的存储操作不同。
_ENROLLMENT_ACTIONS = {
    "enroll-learner": enroll_learner,
    "unenroll-learner": unenroll_learner,
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="course_progress",
        description="课程学习进度台：登记课程并查询课程详情。",
    )
    parser.add_argument("--db", help="SQLite 数据库文件路径（父目录需已存在）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add-course", help="登记一门课程")
    add.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    add.add_argument("--title", required=True, help="课程标题")
    add.add_argument(
        "--chapter",
        action="append",
        default=[],
        help="章节名，可重复；出现顺序即章节顺序",
    )

    get = subparsers.add_parser("get-course", help="按编号查询课程详情")
    get.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    get.add_argument("course_id", help="课程编号（正整数）")

    rename = subparsers.add_parser(
        "rename-course", help="按编号修改课程标题（编号与章节不变）"
    )
    rename.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    rename.add_argument("course_id", help="课程编号（正整数）")
    rename.add_argument("--title", required=True, help="新标题")

    append = subparsers.add_parser(
        "append-chapter", help="向已有课程末尾追加一个章节"
    )
    append.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    append.add_argument("course_id", help="课程编号（正整数）")
    append.add_argument("--chapter", help="要追加的章节名")

    rename_chapter_parser = subparsers.add_parser(
        "rename-chapter", help="修改已有课程中单个章节的名称"
    )
    rename_chapter_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    rename_chapter_parser.add_argument("course_id", help="课程编号（正整数）")
    rename_chapter_parser.add_argument("--chapter", help="要改名的原章节名")
    rename_chapter_parser.add_argument("--name", help="新章节名")

    reorder = subparsers.add_parser(
        "reorder-chapters", help="重排已有课程的全部章节顺序"
    )
    reorder.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    reorder.add_argument("course_id", help="课程编号（正整数）")
    reorder.add_argument(
        "--chapter",
        action="append",
        default=[],
        help="章节名，可重复；出现顺序即新的章节顺序，须与现有章节一一对应",
    )

    remove = subparsers.add_parser(
        "remove-chapter", help="删除已有课程中的单个章节（至少保留一章）"
    )
    remove.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    remove.add_argument("course_id", help="课程编号（正整数）")
    remove.add_argument("--chapter", help="要删除的章节名")

    list_courses_parser = subparsers.add_parser(
        "list-courses", help="查询全部课程概览"
    )
    list_courses_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    list_courses_parser.add_argument(
        "--title-contains",
        help="按课程标题片段筛选：去除首尾空白后做大小写敏感的连续子串匹配",
    )

    add_learner_parser = subparsers.add_parser("add-learner", help="登记一个学员")
    add_learner_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    add_learner_parser.add_argument("--name", help="学员姓名")

    get_learner_parser = subparsers.add_parser(
        "get-learner", help="按编号查询学员"
    )
    get_learner_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    get_learner_parser.add_argument("learner_id", help="学员编号（正整数）")

    rename_learner_parser = subparsers.add_parser(
        "rename-learner", help="按编号修改学员姓名（编号不变）"
    )
    rename_learner_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    rename_learner_parser.add_argument("learner_id", help="学员编号（正整数）")
    rename_learner_parser.add_argument("--name", help="新姓名")

    list_learners_parser = subparsers.add_parser(
        "list-learners", help="查询全部学员名册"
    )
    list_learners_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )

    enroll = subparsers.add_parser(
        "enroll-learner", help="为一个学员报名一门课程"
    )
    enroll.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    enroll.add_argument("learner_id", help="学员编号（正整数）")
    enroll.add_argument("--course", required=True, help="课程编号（正整数）")

    unenroll = subparsers.add_parser(
        "unenroll-learner", help="取消一个学员对一门课程的报名"
    )
    unenroll.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    unenroll.add_argument("learner_id", help="学员编号（正整数）")
    unenroll.add_argument("--course", required=True, help="课程编号（正整数）")

    list_course_learners_parser = subparsers.add_parser(
        "list-course-learners", help="查询一门课程的报名名册"
    )
    list_course_learners_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    list_course_learners_parser.add_argument("course_id", help="课程编号（正整数）")
    list_course_learners_parser.add_argument(
        "--name-contains",
        help="按学员姓名片段筛选：去除首尾空白后做大小写敏感的连续子串匹配",
    )

    list_learner_courses_parser = subparsers.add_parser(
        "list-learner-courses", help="查询一个学员已报名的课程"
    )
    list_learner_courses_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    list_learner_courses_parser.add_argument(
        "learner_id", help="学员编号（正整数）"
    )
    list_learner_courses_parser.add_argument(
        "--title-contains",
        help="按课程标题片段筛选：去除首尾空白后做大小写敏感的连续子串匹配",
    )
    return parser


def parse_positive_int(raw):
    """解析正整数编号，非法时返回 None。"""
    text = raw.strip()
    if not _ID_PATTERN.fullmatch(text):
        return None
    value = int(text)
    return value if value > 0 else None


def resolve_enrollment_target(conn, learner_raw, course_raw):
    """报名关系命令的共用校验流程。

    按学员编号格式、课程编号格式、学员存在性、课程存在性的顺序检查，
    全部通过时返回 ((learner_id, course_id), 0)；任一环节失败时向标准
    错误打印首个错误消息，并返回 (None, 对应退出码)，不修改任何记录。
    """
    learner_id = parse_positive_int(learner_raw)
    if learner_id is None:
        print(ERR_BAD_LEARNER_ID, file=sys.stderr)
        return None, 2
    course_id = parse_positive_int(course_raw)
    if course_id is None:
        print(ERR_BAD_ID, file=sys.stderr)
        return None, 2
    if get_learner(conn, learner_id) is None:
        print(ERR_LEARNER_NOT_FOUND, file=sys.stderr)
        return None, 1
    if get_course(conn, course_id) is None:
        print(ERR_NOT_FOUND, file=sys.stderr)
        return None, 1
    return (learner_id, course_id), 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    db_path = getattr(args, "db", None)
    if not db_path:
        print("必须通过 --db 指定数据库文件", file=sys.stderr)
        return 2
    try:
        conn = connect(db_path)
    except sqlite3.Error as exc:
        print(f"无法打开数据库：{exc}", file=sys.stderr)
        return 1
    with conn:
        if args.command == "add-course":
            try:
                course_id = add_course(conn, args.title, args.chapter)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(json.dumps({"course_id": course_id}, ensure_ascii=False))
            return 0
        if args.command == "list-courses":
            try:
                courses = list_courses(
                    conn, getattr(args, "title_contains", None)
                )
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(
                json.dumps(
                    {"courses": courses}, ensure_ascii=False
                )
            )
            return 0
        if args.command == "add-learner":
            try:
                learner_id = add_learner(conn, args.name or "")
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(json.dumps({"learner_id": learner_id}, ensure_ascii=False))
            return 0
        if args.command == "list-learners":
            learners = list_learners(conn)
            print(json.dumps({"learners": learners}, ensure_ascii=False))
            return 0
        if args.command in _ENROLLMENT_ACTIONS:
            target, exit_code = resolve_enrollment_target(
                conn, args.learner_id, args.course
            )
            if target is None:
                return exit_code
            learner_id, course_id = target
            action = _ENROLLMENT_ACTIONS[args.command]
            result = action(conn, learner_id, course_id)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "list-course-learners":
            course_id = parse_positive_int(args.course_id)
            if course_id is None:
                print(ERR_BAD_ID, file=sys.stderr)
                return 2
            try:
                learners = list_course_learners(
                    conn, course_id, getattr(args, "name_contains", None)
                )
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if learners is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(
                json.dumps(
                    {"course_id": course_id, "learners": learners},
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "get-learner":
            learner_id = parse_positive_int(args.learner_id)
            if learner_id is None:
                print(ERR_BAD_LEARNER_ID, file=sys.stderr)
                return 2
            learner = get_learner(conn, learner_id)
            if learner is None:
                print(ERR_LEARNER_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(learner, ensure_ascii=False))
            return 0
        if args.command == "list-learner-courses":
            learner_id = parse_positive_int(args.learner_id)
            if learner_id is None:
                print(ERR_BAD_LEARNER_ID, file=sys.stderr)
                return 2
            try:
                courses = list_learner_courses(
                    conn, learner_id, getattr(args, "title_contains", None)
                )
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if courses is None:
                print(ERR_LEARNER_NOT_FOUND, file=sys.stderr)
                return 1
            print(
                json.dumps(
                    {"learner_id": learner_id, "courses": courses},
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "rename-learner":
            learner_id = parse_positive_int(args.learner_id)
            if learner_id is None:
                print(ERR_BAD_LEARNER_ID, file=sys.stderr)
                return 2
            try:
                renamed = rename_learner(conn, learner_id, args.name)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if renamed is None:
                print(ERR_LEARNER_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(renamed, ensure_ascii=False))
            return 0
        course_id = parse_positive_int(args.course_id)
        if course_id is None:
            print(ERR_BAD_ID, file=sys.stderr)
            return 2
        if args.command == "rename-course":
            try:
                renamed = rename_course(conn, course_id, args.title)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if renamed is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(renamed, ensure_ascii=False))
            return 0
        if args.command == "append-chapter":
            try:
                appended = append_chapter(conn, course_id, args.chapter)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if appended is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(appended, ensure_ascii=False))
            return 0
        if args.command == "rename-chapter":
            try:
                renamed = rename_chapter(
                    conn, course_id, args.chapter, args.name
                )
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if renamed is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(renamed, ensure_ascii=False))
            return 0
        if args.command == "reorder-chapters":
            try:
                reordered = reorder_chapters(conn, course_id, args.chapter)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if reordered is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(reordered, ensure_ascii=False))
            return 0
        if args.command == "remove-chapter":
            try:
                removed = remove_chapter(conn, course_id, args.chapter)
            except ValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if removed is None:
                print(ERR_NOT_FOUND, file=sys.stderr)
                return 1
            print(json.dumps(removed, ensure_ascii=False))
            return 0
        course = get_course(conn, course_id)
        if course is None:
            print(ERR_NOT_FOUND, file=sys.stderr)
            return 1
        print(json.dumps(course, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
