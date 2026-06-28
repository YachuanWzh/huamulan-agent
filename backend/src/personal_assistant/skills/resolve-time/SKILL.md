---
name: resolve-time
description: 当用户提到时间、日期、今天、明天、后天、昨天、下周、这周、星期几、周几等时间相关词时触发。支持中英文相对日期表达式计算精确日期。
triggers:
  - 今天
  - 明天
  - 后天
  - 大后天
  - 昨天
  - 前天
  - 下周
  - 下下周
  - 这周
  - 上周
  - 星期
  - 周
  - today
  - tomorrow
  - yesterday
  - next week
  - this week
scripts:
  - name: resolve_date_by_offset
    description: Calculate a date by day offset from today (e.g. tomorrow=1, yesterday=-1, N days from today).
    command: ["python", "scripts/resolve_date.py", "offset", "{day_offset}", "{timezone}"]
    params:
      day_offset:
        type: integer
        description: Days from today. positive=future, negative=past. today=0, tomorrow=1, yesterday=-1.
        required: true
      timezone:
        type: string
        description: IANA timezone, e.g. Asia/Shanghai.
        default: Asia/Shanghai
  - name: resolve_date_by_weekday
    description: Calculate a date by target weekday and week offset (this/next/last <weekday>).
    command: ["python", "scripts/resolve_date.py", "weekday", "{weekday}", "{week_offset}", "{timezone}"]
    params:
      weekday:
        type: string
        description: Target weekday — English (Monday), Chinese (星期一 / 周一), or abbreviation (Mon).
        required: true
      week_offset:
        type: integer
        description: Week offset from the current week. 0=this week, 1=next, -1=last.
        default: 1
      timezone:
        type: string
        description: IANA timezone, e.g. Asia/Shanghai.
        default: Asia/Shanghai
  - name: resolve_current_time
    description: Return the current ISO-8601 date/time in the requested timezone.
    command: ["python", "scripts/resolve_date.py", "now", "{timezone}"]
    params:
      timezone:
        type: string
        description: IANA timezone, e.g. Asia/Shanghai.
        default: Asia/Shanghai
---

# Resolve Time — 日期时间解析

当用户输入涉及时间或日期的内容时，调用本技能提供的工具计算精确日期。工具会执行
`scripts/resolve_date.py` 并把结果（JSON 或 ISO-8601 字符串）返回给你；你需要基于该结果
组织回答再回复用户。

## 可用工具

- `resolve_current_time(timezone="Asia/Shanghai")` — 返回指定时区的当前 ISO-8601 时间。
- `resolve_date_by_offset(day_offset, timezone="Asia/Shanghai")` — 按天数偏移计算日期（今天 ±N 天）。
- `resolve_date_by_weekday(weekday, week_offset=1, timezone="Asia/Shanghai")` — 按星期几 + 周偏移计算日期。

工具返回的 JSON 包含 `date`、`weekday`、偏移量与 `description` 字段。请用人类语言把结果
转述给用户，而不是直接贴 JSON。

## 使用场景与参数映射

| 用户表达 | 工具 | 参数 |
|---------|------|------|
| 今天 / today | resolve_date_by_offset | day_offset=0 |
| 明天 / tomorrow | resolve_date_by_offset | day_offset=1 |
| 后天 | resolve_date_by_offset | day_offset=2 |
| 大后天 | resolve_date_by_offset | day_offset=3 |
| 昨天 / yesterday | resolve_date_by_offset | day_offset=-1 |
| 前天 | resolve_date_by_offset | day_offset=-2 |
| N天后 | resolve_date_by_offset | day_offset=N |
| N天前 | resolve_date_by_offset | day_offset=-N |
| 这周一 / this Monday | resolve_date_by_weekday | weekday="Monday", week_offset=0 |
| 下周二 / next Tuesday | resolve_date_by_weekday | weekday="Tuesday", week_offset=1 |
| 下下周五 / the Friday after next | resolve_date_by_weekday | weekday="Friday", week_offset=2 |
| 上周五 / last Friday | resolve_date_by_weekday | weekday="Friday", week_offset=-1 |
| 现在几点 | resolve_current_time | timezone="Asia/Shanghai" |

`weekday` 支持：英文（Monday）、中文星期（星期一）、中文周（周一）、缩写（Mon）。

## 脚本

核心计算逻辑在 `scripts/resolve_date.py`，可独立运行以便调试：

```bash
python scripts/resolve_date.py offset 1
python scripts/resolve_date.py weekday Tuesday 1
python scripts/resolve_date.py now
```
