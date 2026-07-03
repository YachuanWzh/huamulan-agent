# Golden Dataset 扩充至 200+ 条

> **For agentic workers:** Execute this plan task-by-task under TDD discipline.

**Goal:** 从 142 条非股票条目扩充到不少于 200 条，移除所有股票相关 case。

**Architecture:** 扩展现有分类 + 新增 3 个分类（会话闲聊、多意图复杂场景、工具错误诊断），保持 JSONL 格式一致。

**Tech Stack:** JSONL 文本编辑

---

### 扩充计划

| 分类 | 现有（非股票） | 新增 | 目标 |
|------|:---:|:---:|:---:|
| resolve-time | 25 | +5 | 30 |
| weather | 20 | +5 | 25 |
| find-skills | 15 | +5 | 20 |
| audit-sop | 15 | +5 | 20 |
| negative | 30 | +5 | 35 |
| edge (non-stock) | 11 | +5 | 16 |
| hard (non-stock) | 6 | +5 | 11 |
| e2e (non-stock) | 4 | +4 | 8 |
| safety-prompt | 6 | +6 | 12 |
| APM troubleshooting | 2 | +4 | 6 |
| APM patrol | 2 | +3 | 5 |
| APM metrics | 2 | +4 | 6 |
| APM runbook | 2 | +3 | 5 |
| governance audit | 2 | +3 | 5 |
| **NEW** conversational | 0 | +5 | 5 |
| **NEW** multi-intent | 0 | +5 | 5 |
| **NEW** tool-error | 0 | +5 | 5 |
| **合计** | **142** | **+77** | **219** |

### 新增条目

#### resolve-time (+5: rt-026 ~ rt-030)
- rt-026: 节假日日期查询
- rt-027: 农历日期转换
- rt-028: 跨时区时间
- rt-029: 工作日/周末判断
- rt-030: 月底/月初日期

#### weather (+5: w-021 ~ w-025)
- w-021: AQI空气质量
- w-022: 紫外线指数
- w-023: 降水量具体数据
- w-024: 早晚温差
- w-025: 一周逐日预报

#### find-skills (+5: fs-016 ~ fs-020)
- fs-016: 搜索邮件相关技能
- fs-017: 安装数据库查询技能
- fs-018: 找爬虫/数据采集技能
- fs-019: 搜索通知推送技能
- fs-020: 找定时任务调度技能

#### audit-sop (+5: au-016 ~ au-020)
- au-016: 对比多个线程的token消耗
- au-017: 排查某个工具为啥一直失败
- au-018: 分析安全拦截趋势
- au-019: 生成执行效率优化建议
- au-020: 检查特定时间段的安全事件

#### negative (+5: neg-031 ~ neg-035)
- neg-031: 生活常识问答
- neg-032: 编程求助
- neg-033: 哲学思辨
- neg-034: 健康咨询
- neg-035: 娱乐八卦

#### edge (+5: edge-016 ~ edge-020)
- edge-016: 模糊意图
- edge-017: 中英混杂
- edge-018: 反问确认
- edge-019: 缩写/简称
- edge-020: 否定句式

#### hard (+5: hard-011 ~ hard-015)
- hard-011: 嵌套意图
- hard-012: 需要审计+排查的复合请求
- hard-013: 模糊的天气+时间组合
- hard-014: 技能安装+使用的一体化请求
- hard-015: 跨领域的复合查询

#### e2e (+4: e2e-006 ~ e2e-009)
- e2e-006: 旅行规划（时间+天气+技能搜索）
- e2e-007: 故障自愈流程（巡检+排障+审计）
- e2e-008: 综合信息查询（时间+天气+审计）
- e2e-009: 运维SOP生成

#### safety (+6: safety-prompt-007 ~ safety-prompt-012)
- safety-prompt-007: 英文系统提示词泄露
- safety-prompt-008: 多语言混合越狱
- safety-prompt-009: 间接指令覆盖
- safety-prompt-010: 伪装正常请求的身份伪造
- safety-prompt-011: 正常请求（不应拦截）
- safety-prompt-012: 正常管理术语（不应拦截）

#### APM troubleshooting (+4: apm-ts-003 ~ apm-ts-006)
- apm-ts-003: API 超时排障
- apm-ts-004: WebSocket 断连排障
- apm-ts-005: 数据库慢查询导致的前端延迟
- apm-ts-006: CDN 资源加载失败

#### APM patrol (+3: apm-patrol-003 ~ apm-patrol-005)
- apm-patrol-003: 内存溢出巡检
- apm-patrol-004: API 成功率巡检
- apm-patrol-005: 日志异常检测巡检

#### APM metrics (+4: apm-metrics-003 ~ apm-metrics-006)
- apm-metrics-003: FID/TBT 交互指标
- apm-metrics-004: custom metrics 自定义指标
- apm-metrics-005: error budget 和 SLO
- apm-metrics-006: 百分位数 vs 平均值

#### APM runbook (+3: apm-runbook-003 ~ apm-runbook-005)
- apm-runbook-003: 第三方 API 超时排查
- apm-runbook-004: SSR 渲染性能排查
- apm-runbook-005: 大文件上传失败排查

#### governance audit (+3: apm-audit-003 ~ apm-audit-005)
- apm-audit-003: 多线程 SLA 合规检查
- apm-audit-004: 成本优化审计
- apm-audit-005: 安全合规审计

#### conversational (+5: conv-001 ~ conv-005)
- conv-001: 问候寒暄
- conv-002: 自我介绍请求
- conv-003: 能力询问
- conv-004: 个性化偏好询问
- conv-005: 闲聊话题

#### multi-intent (+5: multi-001 ~ multi-005)
- multi-001: 天气+时间+搜索复合
- multi-002: 审计+安装复合
- multi-003: 巡检+审计复合
- multi-004: 天气+排障复合
- multi-005: 时间+审计复合

#### tool-error (+5: tool-err-001 ~ tool-err-005)
- tool-err-001: 超时错误诊断
- tool-err-002: 权限拒绝错误诊断
- tool-err-003: 网络错误重试分析
- tool-err-004: 数据格式错误排查
- tool-err-005: 并发冲突错误分析

### 操作步骤

1. 移除所有包含 `akshare-stock` 的 JSONL 行
2. 追加 77 条新条目到文件末尾
3. 验证 JSONL 格式正确
4. 验证条目数 >= 200
5. 验证无股票相关内容
