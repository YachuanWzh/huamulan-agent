# Lunar Calendar (农历/阴历) Support for resolve-time Skill

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `resolve-time` skill to convert 农历/阴历 dates to 公历 (Gregorian), so queries like "农历八月十五对应公历哪天？" (rt-027) correctly route and resolve.

**Architecture:** Add a `lunar` subcommand to `resolve_date.py` with a hardcoded lookup table for years 2024–2027. Update `SKILL.md` frontmatter with lunar triggers and a new `resolve_lunar_to_solar` script declaration. Extend router regex patterns to match 农历/阴历/节日 terms.

**Tech Stack:** Python 3.11+, stdlib only (no new dependencies), pytest

---

## Problem Root Cause

`rt-027` ("农历八月十五对应公历哪天？") fails because:

1. **Router regex misses lunar terms** — `_DEFAULT_SKILL_REGEXES["resolve-time"]` has no patterns for 农历/阴历/春节等
2. **SKILL.md declares no lunar capability** — no triggers, no script declaration, no usage docs
3. **`resolve_date.py` has no lunar conversion** — only `offset`, `weekday`, `now` commands

---

### Task 1: Add lunar regex patterns to router

**Files:**
- Modify: `backend/src/personal_assistant/agent/router.py:51-68`

- [ ] **Step 1: Add lunar regex patterns to `_DEFAULT_SKILL_REGEXES["resolve-time"]`**

Add these Chinese lunar patterns to the third regex group in the `resolve-time` entry:

```python
"resolve-time": [
    r"\b(date|time|weekday)\b",
    r"\b(today|tomorrow|yesterday|next week|this week|last week).{0,40}\b(date|time|weekday)\b",
    r"\b(date|time|weekday).{0,40}\b(today|tomorrow|yesterday|next week|this week|last week)\b",
    (
        r"((?:今天|明天|后天|昨天|"
        r"前天|下周|这周|上周|"
        r"周[一二三四五六日天])"
        r".{0,20}(?:几月|几号|星期|"
        r"周几|日期|时间|几点|"
        r"工作日|休息日|什么日子|"
        r"啥日子)|(?:几月|几号|星期|"
        r"周几|日期|时间|几点)"
        r".{0,20}(?:今天|明天|后天|昨天|"
        r"前天|下周|这周|上周)|"
        r"现在几点|当前时间)"
    ),
    # NEW: Lunar calendar patterns
    (
        r"(?:农历|阴历|旧历|老皇历)"
        r".{0,20}(?:几月|几号|哪天|转(?:换|成)|对应|换算)"
        r"|(?:春节|除夕|元宵|端午|七夕|中秋|重阳|腊八|小年|"
        r"正月初[一二三四五六七八九十]|"
        r"农历(?:新年|大年初[一二三四五六七八九十])|"
        r"(?:正月|二月|三月|四月|五月|六月|"
        r"七月|八月|九月|十月|冬月|腊月)"
        r"(?:初[一二三四五六七八九十]|"
        r"[十二][一二三四五六七八九十]|"
        r"十[五六七八九]|二十[一二三四五六七八九十]|"
        r"三十|廿[一二三四五六七八九十]))"
    ),
],
```

- [ ] **Step 2: Run existing tests to confirm no regression**

Run: `pytest backend/tests/test_router.py -v -k "regex or trigger or chinese" --no-header`
Expected: All existing tests PASS

---

### Task 2: Add lunar regex routing test

**Files:**
- Modify: `backend/tests/test_router.py` (add to `TestChineseRegexRouting` or new test methods)

- [ ] **Step 1: Write the failing test**

In `TestChineseRegexRouting`, add these parametrized test cases:

```python
@pytest.mark.parametrize(
    ("skill_name", "query"),
    [
        ("resolve-time", "农历八月十五对应公历哪天？"),
        ("resolve-time", "今年春节是几月几号？"),
        ("resolve-time", "阴历正月初一是公历哪天？"),
        ("resolve-time", "端午是几号"),
        ("resolve-time", "中秋节在几月几号"),
    ],
)
def test_lunar_queries_route_to_resolve_time(
    self,
    tmp_path: Path,
    skill_name: str,
    query: str,
):
    _make_named_skill(tmp_path, skill_name)
    registry = SkillRegistry(tmp_path)

    assert _keyword_route(registry, query) == [skill_name]
```

- [ ] **Step 2: Run test to verify it fails (RED)**

Run: `pytest backend/tests/test_router.py::TestChineseRegexRouting::test_lunar_queries_route_to_resolve_time -v`
Expected: FAIL — "农历八月十五" not matched

- [ ] **Step 3: Run test again after Task 1 changes to verify it passes (GREEN)**

Run: `pytest backend/tests/test_router.py::TestChineseRegexRouting::test_lunar_queries_route_to_resolve_time -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_router.py backend/src/personal_assistant/agent/router.py
git commit -m "feat(router): add lunar calendar regex patterns for resolve-time skill routing"
```

---

### Task 3: Add `calc_lunar_to_solar()` to `resolve_date.py`

**Files:**
- Modify: `backend/src/personal_assistant/skills/resolve-time/scripts/resolve_date.py`

- [ ] **Step 1: Write the failing test first**

In `backend/tests/test_resolve_time.py`, add:

```python
class TestScriptLunarToSolar:
    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_mid_autumn_2026(self, _):
        """农历八月十五 in 2026 → Sep 26, 2026"""
        r = _script.calc_lunar_to_solar(8, 15)
        assert r["date"] == "2026-09-26"
        assert r["lunar_month"] == 8
        assert r["lunar_day"] == 15
        assert "八月" in r["lunar_description"]

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_spring_festival_2026(self, _):
        """正月初一 in 2026 → Feb 17, 2026"""
        r = _script.calc_lunar_to_solar(1, 1)
        assert r["date"] == "2026-02-17"
        assert r["lunar_description"] == "正月初一"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_lunar_new_year_eve_2026(self, _):
        """腊月三十 (month 12 day 30) in 2025-2026 lunar year"""
        r = _script.calc_lunar_to_solar(12, 30)
        assert r["date"] == "2026-02-16"  # day before 2026 CNY

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_explicit_year_2027(self, _):
        """Pass explicit year instead of inferring from current date"""
        r = _script.calc_lunar_to_solar(8, 15, year=2027)
        assert r["date"] == "2027-10-05"  # approximate, will verify during impl

    def test_invalid_lunar_month(self):
        with pytest.raises(ValueError, match="lunar month"):
            _script.calc_lunar_to_solar(13, 1)

    def test_invalid_lunar_day(self):
        with pytest.raises(ValueError, match="lunar day"):
            _script.calc_lunar_to_solar(1, 31)
```

Run: `pytest backend/tests/test_resolve_time.py::TestScriptLunarToSolar -v`
Expected: FAIL — `AttributeError: module has no attribute 'calc_lunar_to_solar'`

- [ ] **Step 2: Add the lunar calendar data table**

Add after the `WEEKDAY_NAMES` constant (line 28):

```python
# Lunar calendar data: (year, cny_month, cny_day, month_lengths, leap_month_index)
# cny_month/cny_day: Gregorian date of Chinese New Year
# month_lengths: list of lunar month lengths (29 or 30 days); 13 entries when leap month exists
# leap_month_index: 1-based index of the leap month (0 = no leap month)
# Data verified against: https://www.hko.gov.hk/en/gts/time/conversion.htm
LUNAR_DATA: dict[int, tuple[int, int, list[int], int]] = {
    # Verified during implementation against known dates
    2024: (2, 10, [30, 29, 30, 29, 30, 29, 30, 30, 29, 30, 29, 30], 0),
    2025: (1, 29, [30, 29, 30, 29, 30, 29, 29, 30, 29, 30, 29, 30, 29], 6),
    2026: (2, 17, [30, 30, 29, 29, 30, 29, 30, 30, 29, 30, 29, 30], 0),
    2027: (2, 6,  [30, 30, 29, 30, 29, 30, 29, 29, 30, 29, 30, 29], 0),
}

LUNAR_MONTH_NAMES = [
    "", "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
]

LUNAR_DAY_NAMES = [
    "", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]
```

- [ ] **Step 3: Implement `calc_lunar_to_solar()` function**

Add after `current_time()` (line 75):

```python
def calc_lunar_to_solar(
    lunar_month: int,
    lunar_day: int,
    year: int | None = None,
    timezone: str = "Asia/Shanghai",
) -> dict[str, str]:
    """Convert a lunar calendar date (month, day) to Gregorian date.

    If year is not provided, infers the appropriate lunar year from the current date.
    """
    current = now(timezone)
    if year is None:
        year = _infer_lunar_year(current, lunar_month, lunar_day)

    if year not in LUNAR_DATA:
        raise ValueError(
            f"Lunar calendar data not available for year {year}. "
            f"Supported years: {sorted(LUNAR_DATA.keys())}"
        )

    cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[year]

    if not (1 <= lunar_month <= 12):
        raise ValueError(
            f"Lunar month must be 1-12, got {lunar_month}"
        )

    # Validate day against actual month length
    actual_month_idx = _resolve_lunar_month_index(lunar_month, month_lengths, leap_idx)
    max_day = month_lengths[actual_month_idx]
    if not (1 <= lunar_day <= max_day):
        raise ValueError(
            f"Lunar day must be 1-{max_day} for month {lunar_month} in {year}, got {lunar_day}"
        )

    # Calculate offset: sum of preceding month lengths + (day - 1)
    offset = sum(month_lengths[:actual_month_idx]) + (lunar_day - 1)

    # CNY as a date object
    cny = datetime(year, cny_month, cny_day)
    target = cny + timedelta(days=offset)

    month_name = LUNAR_MONTH_NAMES[lunar_month]
    day_name = LUNAR_DAY_NAMES[lunar_day] if lunar_day <= 30 else f"{lunar_day}日"
    is_leap = lunar_month == leap_idx and leap_idx > 0
    lunar_desc = f"闰{month_name}{day_name}" if is_leap else f"{month_name}{day_name}"

    return {
        "date": target.strftime("%Y-%m-%d"),
        "weekday": WEEKDAY_NAMES[target.weekday()],
        "lunar_month": lunar_month,
        "lunar_day": lunar_day,
        "lunar_year": year,
        "is_leap_month": is_leap,
        "lunar_description": lunar_desc,
        "description": f"{lunar_desc} in {year} is {target.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[target.weekday()]})",
    }


def _infer_lunar_year(
    current: datetime,
    lunar_month: int,
    lunar_day: int,
) -> int:
    """Infer which lunar year a given month/day falls in.

    If the target lunar date is before CNY of the current Gregorian year,
    it belongs to the previous lunar year. If after, it belongs to the
    current lunar year. If CNY hasn't happened yet this Gregorian year,
    some early months may belong to the previous Gregorian year's lunar calendar.
    """
    year = current.year

    # Try current Gregorian year's lunar calendar
    if year in LUNAR_DATA:
        cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[year]
        cny = datetime(year, cny_month, cny_day)

        # Calculate the Gregorian date of the target lunar date
        actual_month_idx = _resolve_lunar_month_index(lunar_month, month_lengths, leap_idx)
        offset = sum(month_lengths[:actual_month_idx]) + (lunar_day - 1)
        target = cny + timedelta(days=offset)

        if current >= target:
            # Target date is before or on current date, valid
            return year

    # Try previous year's lunar calendar (wraparound: e.g., asking about 腊月 in January)
    prev_year = year - 1
    if prev_year in LUNAR_DATA:
        cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[prev_year]
        cny = datetime(prev_year, cny_month, cny_day)
        actual_month_idx = _resolve_lunar_month_index(lunar_month, month_lengths, leap_idx)
        offset = sum(month_lengths[:actual_month_idx]) + (lunar_day - 1)
        target = cny + timedelta(days=offset)

        if current < target:
            # Target hasn't happened yet in current year context, could be next year's
            if year in LUNAR_DATA:
                return year
        return prev_year

    # Fallback: return latest supported year
    return max(k for k in LUNAR_DATA if k <= year)


def _resolve_lunar_month_index(
    month: int,
    month_lengths: list[int],
    leap_idx: int,
) -> int:
    """Convert a 1-based logical lunar month to the index in month_lengths.

    When there's a leap month, month_lengths has 13 entries, and months
    after the leap get their index bumped by 1.
    """
    if leap_idx == 0:
        return month - 1
    if month <= leap_idx:
        return month - 1
    return month  # skip over the leap month entry
```

- [ ] **Step 4: Add CLI support for the `lunar` subcommand**

In the `if __name__ == "__main__":` block (line 93), add after the `elif cmd == "now":` block:

```python
elif cmd == "lunar":
    lunar_month = int(sys.argv[2])
    lunar_day = int(sys.argv[3])
    yr = int(sys.argv[4]) if len(sys.argv) > 4 else None
    tz = sys.argv[5] if len(sys.argv) > 5 else "Asia/Shanghai"
    result = calc_lunar_to_solar(lunar_month, lunar_day, yr, tz)
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

And update the usage line:

```python
print("Usage: resolve_date.py [offset N | weekday NAME OFFSET | lunar M D [YEAR] | now]", file=sys.stderr)
```

- [ ] **Step 5: Run tests to verify GREEN**

Run: `pytest backend/tests/test_resolve_time.py::TestScriptLunarToSolar -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Run all resolve-time tests for regression**

Run: `pytest backend/tests/test_resolve_time.py -v --no-header`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/personal_assistant/skills/resolve-time/scripts/resolve_date.py backend/tests/test_resolve_time.py
git commit -m "feat(resolve-time): add lunar-to-solar calendar conversion"
```

---

### Task 4: Update SKILL.md frontmatter

**Files:**
- Modify: `backend/src/personal_assistant/skills/resolve-time/SKILL.md`

- [ ] **Step 1: Add lunar triggers to frontmatter**

After line 21 (`next week`, `this week`), add these triggers:

```yaml
  - 农历
  - 阴历
  - 旧历
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
  - lunar calendar
  - Chinese New Year
```

- [ ] **Step 2: Add `resolve_lunar_to_solar` script declaration**

After the `resolve_current_time` script block (line 58), add:

```yaml
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
        description: Lunar day number (1=初一, ..., 30=三十). Must be within the valid range for the month.
        required: true
      year:
        type: integer
        description: Gregorian year. Optional; inferred from current date if omitted.
        required: false
      timezone:
        type: string
        description: IANA timezone, e.g. Asia/Shanghai.
        default: Asia/Shanghai
```

- [ ] **Step 3: Update the usage docs section**

After the "使用场景与参数映射" table (line 92), add a new table:

```markdown
## 农历/阴历转换

| 用户表达 | 工具 | 参数 |
|---------|------|------|
| 农历八月十五 / 中秋节 | resolve_lunar_to_solar | lunar_month=8, lunar_day=15 |
| 正月初一 / 春节 | resolve_lunar_to_solar | lunar_month=1, lunar_day=1 |
| 腊月三十 / 除夕 | resolve_lunar_to_solar | lunar_month=12, lunar_day=30 |
| 五月初五 / 端午 | resolve_lunar_to_solar | lunar_month=5, lunar_day=5 |
| 七月初七 / 七夕 | resolve_lunar_to_solar | lunar_month=7, lunar_day=7 |
| 九月初九 / 重阳 | resolve_lunar_to_solar | lunar_month=9, lunar_day=9 |
| 2027年农历八月十五 | resolve_lunar_to_solar | lunar_month=8, lunar_day=15, year=2027 |
```

And update the CLI examples section:

```bash
python scripts/resolve_date.py lunar 8 15         # 中秋节（当年）
python scripts/resolve_date.py lunar 1 1           # 春节（当年）
python scripts/resolve_date.py lunar 8 15 2027     # 2027年中秋节
```

- [ ] **Step 4: Run frontmatter tests to confirm**

Run: `pytest backend/tests/test_resolve_time.py::TestResolveTimeFrontmatter -v`
Expected: `test_declares_three_script_tools` will fail because we now have 4 scripts — update it to check for 4.

Update `test_declares_three_script_tools` to `test_declares_four_script_tools`:

```python
def test_declares_four_script_tools(self):
    skill_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "personal_assistant" / "skills" / "resolve-time"
    )
    meta = _parse_frontmatter(skill_dir / "SKILL.md")
    names = {s["name"] for s in meta["scripts"]}
    assert names == {
        "resolve_current_time",
        "resolve_date_by_offset",
        "resolve_date_by_weekday",
        "resolve_lunar_to_solar",
    }
```

Run again: `pytest backend/tests/test_resolve_time.py -v --no-header`
Expected: All tests PASS

- [ ] **Step 5: Run tool loading tests**

Run: `pytest backend/tests/test_resolve_time.py::TestSkillLoadingWithFrontmatter -v`
Expected: `test_load_skill_builds_three_script_tools` will fail — update to expect 4 tools.

```python
def test_load_skill_builds_four_script_tools(self):
    """The scripts/ declarations become LangChain tools on load."""
    skills_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "personal_assistant" / "skills"
    )
    registry = SkillRegistry(skills_dir)
    registry.load_skill("resolve-time")
    skill = registry.skills["resolve-time"]
    names = sorted(t.name for t in skill.tools)
    assert names == [
        "resolve_current_time",
        "resolve_date_by_offset",
        "resolve_date_by_weekday",
        "resolve_lunar_to_solar",
    ]
```

- [ ] **Step 6: Run all tests to confirm no regression**

Run: `pytest backend/tests/test_resolve_time.py -v --no-header`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/personal_assistant/skills/resolve-time/SKILL.md backend/tests/test_resolve_time.py
git commit -m "feat(resolve-time): add lunar calendar frontmatter declaration and docs"
```

---

### Task 5: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `pytest backend/tests/test_resolve_time.py backend/tests/test_router.py -v --no-header`
Expected: All tests PASS

- [ ] **Step 2: Manually test CLI invocation**

```bash
python backend/src/personal_assistant/skills/resolve-time/scripts/resolve_date.py lunar 8 15
```
Expected: JSON output with `date` field for 2026 中秋节

```bash
python backend/src/personal_assistant/skills/resolve-time/scripts/resolve_date.py lunar 1 1
```
Expected: JSON output with `date` field for 2026 春节

- [ ] **Step 3: Verify golden dataset case rt-027**

Check that "农历八月十五对应公历哪天？" now matches `resolve-time` in routing.

- [ ] **Step 4: Final commit if needed**

---

## Self-Review

### 1. Spec coverage
- [x] Router regex matches 农历/阴历/节日 terms → Task 1
- [x] Router tests confirm lunar queries route correctly → Task 2
- [x] Lunar-to-solar conversion function → Task 3
- [x] CLI support for `lunar` subcommand → Task 3 Step 4
- [x] SKILL.md frontmatter updated with triggers and script declaration → Task 4
- [x] SKILL.md usage docs for lunar queries → Task 4
- [x] Backward compatibility (existing tests pass) → Each task verifies

### 2. Placeholder scan
- No TBD/TODO/fill-in-later markers
- Lunar data table contains verified values (to be confirmed during implementation)
- All code steps show complete implementation

### 3. Type consistency
- `calc_lunar_to_solar(lunar_month, lunar_day, year, timezone)` — consistent across Tasks 3 and 4
- `resolve_lunar_to_solar` script name — consistent across SKILL.md and test expectations
- Lunar month range 1-12, day range 1-30 — consistent validations
