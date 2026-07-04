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
  - 农历
  - 阴历
  - 春节
  - 除夕
  - 元宵
  - 端午
  - 七夕
  - 中秋
  - 重阳
  - 腊八
  - 正月
  - lunar
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
  - name: resolve_lunar_to_solar
    description: Convert a 农历/阴历 date (month, day) to the Gregorian (公历) date. Supports years 2024-2027.
    command: ["python", "scripts/resolve_date.py", "lunar", "{lunar_month}", "{lunar_day}", "{year}", "{timezone}"]
    params:
      lunar_month:
        type: integer
        description: Lunar month number (1=正月, 2=二月, ..., 12=腊月).
        required: true
      lunar_day:
        type: integer
        description: Lunar day number (1=初一, 2=初二, ..., 30=三十). Must be within the valid range for the month.
        required: true
      year:
        type: integer
        description: Gregorian year. Optional; inferred from current date if omitted.
        required: false
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
- `resolve_lunar_to_solar(lunar_month, lunar_day, year=None, timezone="Asia/Shanghai")` — 农历/阴历日期转公历日期。

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

## 农历/阴历转换

| 用户表达 | 工具 | 参数 |
|---------|------|------|
| 农历八月十五 / 中秋节 | resolve_lunar_to_solar | lunar_month=8, lunar_day=15 |
| 正月初一 / 春节 | resolve_lunar_to_solar | lunar_month=1, lunar_day=1 |
| 腊月三十 / 除夕 | resolve_lunar_to_solar | lunar_month=12, lunar_day=30 |
| 腊月最后一天 | resolve_lunar_to_solar | lunar_month=12, lunar_day=29 |
| 五月初五 / 端午 | resolve_lunar_to_solar | lunar_month=5, lunar_day=5 |
| 七月初七 / 七夕 | resolve_lunar_to_solar | lunar_month=7, lunar_day=7 |
| 九月初九 / 重阳 | resolve_lunar_to_solar | lunar_month=9, lunar_day=9 |
| 2027年农历八月十五 | resolve_lunar_to_solar | lunar_month=8, lunar_day=15, year=2027 |

**注意**：农历月份天数（29或30天）因年而异。各月的实际天数由系统内置的农历数据表确定。
如果指定 `year` 参数，会直接使用该年份的农历数据；否则从当前日期推断年份。

## 脚本

核心计算逻辑在 `scripts/resolve_date.py`，可独立运行以便调试：

```bash
python scripts/resolve_date.py offset 1
python scripts/resolve_date.py weekday Tuesday 1
python scripts/resolve_date.py lunar 8 15         # 中秋节（当年）
python scripts/resolve_date.py lunar 1 1           # 春节（当年）
python scripts/resolve_date.py lunar 8 15 2027     # 2027年中秋节
python scripts/resolve_date.py now
```
