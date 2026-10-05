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
    connect,
    get_course,
    list_courses,
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
        course = get_course(conn, course_id)
        if course is None:
            print(ERR_NOT_FOUND, file=sys.stderr)
            return 1
        print(json.dumps(course, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
