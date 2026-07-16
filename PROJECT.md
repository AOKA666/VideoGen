# PROJECT

## TODO
- [x] 修复视频上传后顶部 status 徽标一直显示“素材已上传，正在识别标签”的问题
- [x] 修复素材卡片“识别中”判定漏掉 `probing` / `splitting` 两个中间态的问题
- [x] 补齐 Docker Compose、前后端镜像、Nginx 代理和生产环境模板
- [x] 增加同域 API、Linux 中文字体、持久化、健康检查和备份脚本
- [ ] 清理仓库根目录意外的 `services/api/logs/image-search.log.1`（47K 行，pull 进来那次没排除）
- [ ] 在 Windows 环境完成视频上传、识别状态和完成态回归测试
- [ ] 获取目标服务器 SSH、正式域名和 Cloudflare Tunnel/Access 配置后执行首次部署
- [ ] 在正式环境完成 R2、配音、字幕、封面和 MP4 全链路验收

## DECISIONS
- 识别结束后清空上传提示，让顶部状态回落到默认“就绪”，不额外保留“识别完毕”文案。
- 素材卡片统一将 `probing`、`splitting`、`analyzing` 视为识别处理中。
- 正式部署采用 Docker Compose：Nginx 静态前端与同域代理、FastAPI 单 worker、宿主机持久化 `storage/`。
- 由于应用当前没有内置登录鉴权，公网入口必须使用 Cloudflare Access；API 容器不直接发布端口。
- Nginx 禁止公开 `storage/db.json`、内部日志和隐藏文件；发布前自动备份 storage。
- Linux 容器安装 FFmpeg 与 Noto CJK，并通过 `VIDEOGEN_FONT_FILE` 指定中文字体；Windows 本地默认行为保持不变。

## LOG
- 2026-07-14 用户报 bug：上传视频识别标签完成后，顶部 status 徽标仍一直显示“素材已上传，正在识别标签”。
- 2026-07-14 已 pull `5fa2e23..ce71135`，定位根因：上传提示写入 `message` 后没有完成态清理路径。
- 2026-07-14 已完成前端修复；`npm run build` 通过，Linux 临时预览 HTTP 200。
- 2026-07-14 用户确认推送，后续将在 Windows 本地环境回归测试。
- 2026-07-16 完成生产部署配置、本地兼容改造、部署文档和回归测试；前端生产构建、39 项后端测试、Compose YAML、Shell 语法与本地 HTTP 健康检查均通过。当前主机未安装 Docker，容器构建/启动与正式全链路验收待目标服务器完成。
