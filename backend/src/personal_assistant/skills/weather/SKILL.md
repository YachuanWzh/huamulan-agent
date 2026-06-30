---
name: weather
description: 天气查询——查询指定城市的当前天气和未来3天预报。数据来自 wttr.in（免费，无需 API key）。
triggers:
  - 天气
  - 气温
  - 温度
  - 下雨
  - 下雪
  - 刮风
  - 多云
  - 晴天
  - 雾霾
  - 预报
  - weather
  - 热
  - 冷
  - 降温
  - 升温
scripts:
  - name: get_current_weather
    description: Query current weather for a city (temperature, condition, humidity, wind).
    command: ["python", "scripts/weather.py", "current", "{city}"]
    params:
      city:
        type: string
        description: City name — Chinese (北京), English (Beijing), or pinyin. Supports wttr.in location syntax.
        required: true
  - name: get_forecast
    description: Query 3-day weather forecast for a city (daily min/max temp, condition).
    command: ["python", "scripts/weather.py", "forecast", "{city}"]
    params:
      city:
        type: string
        description: City name — Chinese (北京), English (Beijing), or pinyin.
        required: true
---

# Weather — 天气查询

当用户询问天气、气温、是否会下雨/下雪等问题时，调用本技能的工具获取实时数据。
数据来自 [wttr.in](https://wttr.in)，免费无需 API key。

## 可用工具

- `get_current_weather(city)` — 查询城市当前天气（温度、天气状况、湿度、风速风向、体感温度）。
- `get_forecast(city)` — 查询城市未来 3 天预报（每日最高/最低温度、天气状况）。

工具返回 JSON，包含结构化的天气数据。请用自然语言把结果转述给用户，不要直接贴 JSON。

## 使用场景

| 用户表达 | 工具 | 参数示例 |
|---------|------|---------|
| 北京今天天气怎么样 | get_current_weather | city="北京" |
| Shanghai 热不热 | get_current_weather | city="Shanghai" |
| 明天会下雨吗（上海） | get_forecast | city="上海" |
| 深圳这周天气预报 | get_forecast | city="深圳" |
| 杭州气温多少度 | get_current_weather | city="杭州" |
| 纽约会不会下雪 | get_forecast | city="New York" |

城市名支持中文、英文、拼音以及 wttr.in 的特殊语法（如 `~Beijing` 模糊搜索）。

## 脚本

核心逻辑在 `scripts/weather.py`，可独立运行：

```bash
python scripts/weather.py current 北京
python scripts/weather.py forecast Shanghai
```
