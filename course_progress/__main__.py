"""命令行入口：python -m course_progress --db <文件> <子命令>。"""

import argparse
import json
import re
import sqlite3
import sys

from .core import (
    ERR_BAD_ID,
    ERR_NOT_FOUND,
    ValidationError,
    add_course,
    append_chapter,
    connect,
    get_course,
    list_courses,
    rename_chapter,
    rename_course,
    reorder_chapters,
)

_ID_PATTERN = re.compile(r"[+-]?\d+")


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
        "reorder-chapters", help="重排已有课程的全部章节"
    )
    reorder.add_argument("--db", default=argparse.SUPPRESS, help="同全局 --db")
    reorder.add_argument("course_id", help="课程编号（正整数）")
    reorder.add_argument(
        "--chapter",
        action="append",
        default=[],
        help="章节名，可重复；出现顺序即新的章节顺序，须与现有章节一一对应",
    )

    list_courses_parser = subparsers.add_parser(
        "list-courses", help="查询全部课程概览"
    )
    list_courses_parser.add_argument(
        "--db", default=argparse.SUPPRESS, help="同全局 --db"
    )
    return parser


def parse_positive_int(raw):
    """解析正整数编号，非法时返回 None。"""
    text = raw.strip()
    if not _ID_PATTERN.fullmatch(text):
        return None
    value = int(text)
    return value if value > 0 else None


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
            print(
                json.dumps(
                    {"courses": list_courses(conn)}, ensure_ascii=False
                )
            )
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
        course = get_course(conn, course_id)
        if course is None:
            print(ERR_NOT_FOUND, file=sys.stderr)
            return 1
        print(json.dumps(course, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
