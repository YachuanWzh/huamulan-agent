---
name: weather
description: 天气查询——查询指定城市的当前天气和未来预报
triggers:
  - 天气
  - 气温
  - 温度
  - 下雨
  - 下雪
  - 刮风
  - 多云
  - 晴天
  - 预报
  - weather
scripts:
  - name: get_current_weather
    description: Query current weather for a city (temperature, humidity, wind, condition).
    parameters:
      city:
        type: string
        description: City name (Chinese, English, or pinyin)
        required: true
  - name: get_forecast
    description: Query 3-day weather forecast for a city (daily min/max temp, condition).
    parameters:
      city:
        type: string
        description: City name (Chinese, English, or pinyin)
        required: true
---

# Weather — 天气查询

当用户询问天气相关问题时，调用本技能的工具获取实时数据。数据来自 [wttr.in](https://wttr.in)。

## 可用工具

- `get_current_weather(city)` — 查询城市当前天气
- `get_forecast(city)` — 查询城市未来 3 天预报

## 使用场景

| 用户表达 | 工具 | 参数示例 |
|---------|------|---------|
| 北京今天天气怎么样 | get_current_weather | city="北京" |
| 明天上海会下雨吗 | get_forecast | city="上海" |
| 深圳这周天气预报 | get_forecast | city="深圳" |

核心逻辑在 `skill.py`，可独立调用：

```python
from skills.weather.skill import get_current_weather, get_forecast
print(get_current_weather("北京"))
print(get_forecast("Shanghai"))
```
