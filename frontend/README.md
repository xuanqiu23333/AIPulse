# AIPulse 前端 MVP

Vue 3 + TypeScript + Vite，读取真实 FastAPI 数据；没有 Mock 数据降级模式。

```powershell
Set-Location D:\Projects\AIPulse\frontend
npm ci
npm run dev
```

前端：http://127.0.0.1:5173 。先运行本机后端 8000 端口，关闭所有写入与调度开关。Node 使用 22.12+ 的 22.x 或 24+；本轮实际验证 Node 24.19.0。

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 仅绑定 127.0.0.1:5173，端口被占用时明确失败 |
| `npm run typecheck` | 严格 TypeScript / Vue 类型检查 |
| `npm test` | 离线定向测试，默认阻断未 Mock 的网络请求 |
| `npm run build` | 类型检查后构建到 dist/ |
| `npm run preview` | 仅预览构建产物，生产 API 仍需同源反向代理 |

完整的后端启动命令、当前机器没有 npm 时的替代命令、API 映射、真实 GET 验收与 Sprint 6 边界见 [运行文档](../docs/14-FRONTEND-MVP.md)。

付费生成与取消都需要主动确认；本轮仅验证已有数据 GET 与 Mock POST。不能用 HTTP 200 判断研究成功，必须检查报告 `status`。不要将服务端 .env、数据库信息或 DeepSeek Key 放入本目录。
