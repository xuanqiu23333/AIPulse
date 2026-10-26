# UI V2 展示资源

这些图片由当前仓库前端构建产物在本机浏览器实际渲染并截图，不是 AI 生成的界面图或生产记录。数据全部是 xuanqiu233 自建合成 **Demo Data**；图片有可见水印，来源链接只使用 example.org，不含真实新闻/论文正文、个人邮箱、Token、数据库数据或真实任务 ID。

| 图片 | 展示内容 |
| --- | --- |
| dashboard-demo.jpg | 情报总览与有界加载范围 |
| news-demo.jpg | 新闻中心、分类和来源布局 |
| brief-demo.jpg | 合成日报的概览、栏目与缓存阅读 |
| research-demo.jpg | 研究工作台、预算提示与未提交的合成主题；没有成功报告 |

截图使用独立的 loopback 只读 Mock HTTP 服务：服务同时托管真实前端产物和固定 JSON，拒绝所有 POST / 写操作，不启动 FastAPI、MySQL、采集器或 Gateway。Demo 水印只由截图服务添加，不进入生产 Vue 源码。原始捕获和临时服务脚本不随仓库发布；只保留四张压缩 JPEG。

复现原则：先 `npm run build`，在隔离本机端口用符合 `frontend/src/api/types.ts` 的合成 GET 响应替代 /api，固定数条 Demo 新闻、一份日报及一个失败任务，显式标注 Demo Data，再访问四个页面。切勿为截图开启真实付费开关、连接开发库或伪造成功报告。合成内容与截图用于界面展示，不代表模型质量验收或生产数据；自有部分按项目 MIT，图标/依赖继续遵守原许可。
