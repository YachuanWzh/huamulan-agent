---
name: weather
description: 天气查询——查询指定城市的当前天气和未来预报。数据来自 wttr.in（免费，无需 API key）。
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
---

# Weather — 天气查询

当用户询问天气、气温、是否会下雨/下雪等问题时，使用本技能获取实时数据。
数据来自 [wttr.in](https://wttr.in)，免费无需 API key。

## 使用方式

```
https://wttr.in/{city}?format=j1    # JSON 格式
https://wttr.in/{city}              # 终端友好格式
```

城市名支持中文、英文、拼音（如 北京, Beijing, beijing）。

## 使用场景

| 用户表达 | 工具 | 参数示例 |
|---------|------|---------|
| 北京今天天气怎么样 | get_current_weather | city="北京" |
| Shanghai 热不热 | get_current_weather | city="Shanghai" |
| 深圳这周天气预报 | get_forecast | city="深圳" |
| 纽约会不会下雪 | get_forecast | city="New York" |

## 注意事项

- 返回 JSON 数据后，请用自然语言转述给用户，不要直接贴原始 JSON。
- wttr.in 免费服务，无需 API key，但请求频率不宜过高。
