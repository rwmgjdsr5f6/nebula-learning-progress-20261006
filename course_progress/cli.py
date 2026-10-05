"""命令行入口：add-course 与 get-course 子命令。"""

import argparse
import json
import re
import sqlite3
import sys

from . import storage

POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="course_progress",
        description="课程学习进度台：登记课程并按编号查询详情。",
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径（父目录需已存在）")

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add-course", help="登记一门课程")
    add_parser.add_argument("--title", help="课程标题")
    add_parser.add_argument(
        "--chapter",
        action="append",
        help="章节名，可重复传入；出现顺序即章节顺序",
    )

    get_parser = subparsers.add_parser("get-course", help="按编号查询课程详情")
    get_parser.add_argument("course_id", help="课程编号（正整数）")

    return parser


def fail(message, code=1):
    """向标准错误输出消息并返回指定退出码（默认 1）。"""
    print(message, file=sys.stderr)
    return code


def open_storage(db_path):
    """打开数据库；父目录缺失等环境问题转为中文提示。"""
    try:
        return storage.connect(db_path)
    except sqlite3.Error:
        print("无法打开数据库文件，请确认父目录存在且可写", file=sys.stderr)
        return None


def command_add_course(args):
    # 校验顺序：标题 -> 空章节 -> 重复章节，只返回首个错误。
    title = (args.title or "").strip()
    if not title:
        return fail("课程标题不能为空")

    chapters = [name.strip() for name in (args.chapter or [])]
    if not chapters or any(name == "" for name in chapters):
        return fail("章节不能为空")

    seen = set()
    for name in chapters:  # 去除首尾空白后按大小写敏感方式比较
        if name in seen:
            return fail("章节名重复")
        seen.add(name)

    conn = open_storage(args.db)
    if conn is None:
        return 1
    try:
        course_id = storage.add_course(conn, title, chapters)
    finally:
        conn.close()
    print(json.dumps({"course_id": course_id}, ensure_ascii=False))
    return 0


def command_get_course(args):
    identifier = args.course_id
    if not POSITIVE_INTEGER.fullmatch(identifier):
        return fail("课程编号必须为正整数", code=2)

    conn = open_storage(args.db)
    if conn is None:
        return 1
    try:
        result = storage.get_course(conn, int(identifier))
    finally:
        conn.close()
    if result is None:
        return fail("课程不存在")

    title, chapters = result
    print(
        json.dumps(
            {"course_id": int(identifier), "title": title, "chapters": chapters},
            ensure_ascii=False,
        )
    )
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "add-course":
        return command_add_course(args)
    if args.command == "get-course":
        return command_get_course(args)
    parser.error(f"未知子命令: {args.command}")
    return 2
