# PROJECT

## TODO
- [x] 修复视频上传后顶部 status 徽标一直显示“素材已上传，正在识别标签”的问题
- [x] 修复素材卡片“识别中”判定漏掉 `probing` / `splitting` 两个中间态的问题
- [ ] 清理仓库根目录意外的 `services/api/logs/image-search.log.1`（47K 行，pull 进来那次没排除）
- [ ] 在 Windows 环境完成视频上传、识别状态和完成态回归测试

## DECISIONS
- 识别结束后清空上传提示，让顶部状态回落到默认“就绪”，不额外保留“识别完毕”文案。
- 素材卡片统一将 `probing`、`splitting`、`analyzing` 视为识别处理中。

## LOG
- 2026-07-14 用户报 bug：上传视频识别标签完成后，顶部 status 徽标仍一直显示“素材已上传，正在识别标签”。
- 2026-07-14 已 pull `5fa2e23..ce71135`，定位根因：上传提示写入 `message` 后没有完成态清理路径。
- 2026-07-14 已完成前端修复；`npm run build` 通过，Linux 临时预览 HTTP 200。
- 2026-07-14 用户确认推送，后续将在 Windows 本地环境回归测试。