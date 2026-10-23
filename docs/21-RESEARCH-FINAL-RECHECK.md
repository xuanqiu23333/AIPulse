# 研究真实验收的公开边界

历史上已执行有界研究并持久化任务、证据及节点日志，结果包含 failed 和 partial；不把它们宣称为通过质量验收的报告。

最近一次历史失败为 report_schema_mismatch，未发生输出截断，当时没有保存具体违规字段或原始模型 JSON，因此无法恢复哪一字段不符合契约。v6 新增完整草稿 Schema 提示及字段级脱敏诊断，只通过离线固定样例验证，尚未真实模型复验。

本公开快照不包含历史任务 JSON、公开资料全文、真实模型响应、本机日志或任务截图。测试旧版 failed/partial 读取兼容使用自建合成 payload，不是历史数据副本。技术规则见 [证据质量](20-DEEP-RESEARCH-QUALITY.md) 与 [v6 契约](22-RESEARCH-REPORT-CONTRACT.md)。
