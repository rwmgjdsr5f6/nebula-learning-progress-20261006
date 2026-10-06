"""课程学习进度台：课程登记与课程详情查询。"""

from .core import (
    ERR_BAD_ID,
    ERR_DUP_CHAPTER,
    ERR_EMPTY_CHAPTER,
    ERR_EMPTY_TITLE,
    ERR_EMPTY_TITLE_FILTER,
    ERR_NOT_FOUND,
    add_course,
    connect,
    get_course,
    list_courses,
    rename_course,
)

__all__ = [
    "ERR_BAD_ID",
    "ERR_DUP_CHAPTER",
    "ERR_EMPTY_CHAPTER",
    "ERR_EMPTY_TITLE",
    "ERR_EMPTY_TITLE_FILTER",
    "ERR_NOT_FOUND",
    "add_course",
    "connect",
    "get_course",
    "list_courses",
    "rename_course",
]
