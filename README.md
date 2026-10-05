# 课程学习进度台

计划管理课程、学习记录和完成进度。面向本地单机使用，采用 Python 3 标准库与 SQLite。

当前已实现：课程登记（标题 + 有序章节列表）与按编号查询课程详情。数据保存在 `--db`
指定的 SQLite 文件中，退出程序后仍可按原顺序读取；文件不存在时自动创建
（父目录需由使用者预先准备）。

## 运行环境

- Python 3（仅使用标准库，无需安装第三方依赖）
- 本机 SQLite（Python 自带 `sqlite3` 模块）

在仓库根目录下执行命令：

```bash
python -m course_progress --db <数据库文件路径> <子命令> ...
```

若系统中命令名为 `python3`，将下例中的 `python` 替换为 `python3` 即可。

## 登记课程：add-course

- `--title`：课程标题，去除首尾空白后保存（中间空白和大小写保留）。
- `--chapter`：章节名，可重复传入，参数出现顺序即章节顺序；至少一个，
  去除首尾空白后不能为空，同一课程内按大小写敏感方式比较且不允许重复。
- 允许不同课程使用相同标题。

```bash
python -m course_progress --db ./course.sqlite add-course \
  --title "Python 入门" \
  --chapter "安装环境" \
  --chapter "第一段程序"
```

成功时标准输出一个 JSON 对象，退出码 0。新数据库的首个编号为 1：

```json
{"course_id": 1}
```

## 查询课程：get-course

参数为课程编号（正整数）：

```bash
python -m course_progress --db ./course.sqlite get-course 1
```

成功时标准输出一个 JSON 对象，章节按登记顺序排列，退出码 0：

```json
{"course_id": 1, "title": "Python 入门", "chapters": ["安装环境", "第一段程序"]}
```

即使程序已经退出，另起一个进程再次查询，结果（标题与章节顺序）完全相同。

## 失败情况

登记失败时消息写入标准错误、退出码 1、标准输出为空，数据库不留下该课程或任何章节；
多种问题同时存在时，按“标题 → 空章节 → 重复章节”的顺序只报首个错误：

| 情形 | 标准错误消息 | 退出码 |
| --- | --- | --- |
| 标题去除首尾空白后为空 | `课程标题不能为空` | 1 |
| 未提供章节，或某个章节名为空白 | `章节不能为空` | 1 |
| 章节名去除首尾空白后重复（大小写敏感） | `章节名重复` | 1 |

查询失败时标准输出同样为空：

| 情形 | 标准错误消息 | 退出码 |
| --- | --- | --- |
| 编号是正整数但课程不存在，如 `get-course 99` | `课程不存在` | 1 |
| 编号不是正整数，如 `0`、`-1`、`abc`、`1.5` | `课程编号必须为正整数` | 2 |

## 离线核对清单

以下命令可在无网络环境中直接核对“保存、重启读取、错误处理”：

```bash
# 1) 全新数据库登记
python -m course_progress --db ./demo.sqlite add-course \
  --title "Python 入门" --chapter "安装环境" --chapter "第一段程序"
# -> {"course_id": 1}   (退出码 0)

# 2) 新进程查询编号 1，标题与章节顺序一致
python -m course_progress --db ./demo.sqlite get-course 1
# -> {"course_id": 1, "title": "Python 入门", "chapters": ["安装环境", "第一段程序"]}   (退出码 0)

# 3) 查询不存在的正整数编号
python -m course_progress --db ./demo.sqlite get-course 99
# 标准错误: 课程不存在   (退出码 1，标准输出为空)

# 4) 编号不是正整数
python -m course_progress --db ./demo.sqlite get-course abc
# 标准错误: 课程编号必须为正整数   (退出码 2，标准输出为空)

# 5) 登记校验失败（示例：章节重复），不影响已保存的课程 1
python -m course_progress --db ./demo.sqlite add-course --title 坏例子 --chapter a --chapter a
# 标准错误: 章节名重复   (退出码 1，标准输出为空)
python -m course_progress --db ./demo.sqlite get-course 1
# -> 课程 1 详情保持不变

# 6) 不同 --db 文件彼此独立
python -m course_progress --db ./other.sqlite add-course --title "Python 入门" --chapter 其它章节
# -> {"course_id": 1}   (另一文件从 1 开始编号，demo.sqlite 中的内容不变)
```
