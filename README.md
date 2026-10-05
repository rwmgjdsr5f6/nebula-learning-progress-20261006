# 课程学习进度台

计划管理课程、学习记录和完成进度。面向本地单机使用，采用 Python 3 标准库与 SQLite。

## 使用说明

通过 `python -m course_progress` 调用，每次用 `--db` 指定数据库文件（父目录需预先创建，文件不存在时自动创建）。`--db` 写在子命令前后均可。

### 登记课程

```bash
python -m course_progress --db data/course.db add-course \
    --title "Python 入门" --chapter "安装环境" --chapter "第一段程序"
```

`--chapter` 可重复，出现顺序即章节顺序。成功时标准输出：

```json
{"course_id": 1}
```

退出码 0。全新数据库的首个编号为 1，之后递增。

### 查询课程详情

```bash
python -m course_progress --db data/course.db get-course 1
```

成功时标准输出（chapters 按登记顺序排列）：

```json
{"course_id": 1, "title": "Python 入门", "chapters": ["安装环境", "第一段程序"]}
```

退出码 0。数据库文件落盘保存，退出程序后再次查询结果一致。

### 查询课程概览

```bash
python -m course_progress --db data/course.db list-courses
```

成功时标准输出为一行 JSON，`courses` 按课程编号升序排列，每项只含 `course_id`、`title`、`chapter_count`，不含章节名称：

```json
{"courses": [{"course_id": 1, "title": "Python 入门", "chapter_count": 2}]}
```

全新数据库尚未登记课程时自动建库建表，返回 `{"courses": []}`，退出码 0。同标题的课程分别列出。

### 失败结果

登记失败向标准错误输出中文消息，退出码 1，标准输出为空，数据库不留下任何记录：

| 情形 | 标准错误 |
| --- | --- |
| 标题去除首尾空白后为空 | `课程标题不能为空` |
| 未提供 `--chapter` 或存在空章节名 | `章节不能为空` |
| 同一课程内章节名重复（去首尾空白后按大小写敏感比较） | `章节名重复` |

多种问题同时存在时，按标题、空章节、重复章节的顺序只返回首个错误。

查询失败：

| 情形 | 标准错误 | 退出码 |
| --- | --- | --- |
| 编号不存在（正整数） | `课程不存在` | 1 |
| 编号不是正整数（如 `0`、`-3`、`abc`） | `课程编号必须为正整数` | 2 |

不同的 `--db` 文件中的课程彼此独立。
