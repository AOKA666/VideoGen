# VideoGen 生产部署

## 架构

- `web`：Vite 生产构建 + Nginx，同域提供前端、`/api/` 与 `/storage/` 反向代理。
- `api`：FastAPI + FFmpeg，固定单 worker，避免 `storage/db.json` 多进程并发写入。
- `storage/`：宿主机持久化目录，不打进镜像；部署前自动备份。
- `tunnel`：可选 Cloudflare Tunnel 叠加配置。公网开放前必须配置 Cloudflare Access。

Nginx 会拒绝访问 `/storage/db.json`、内部日志和隐藏文件。API 容器不发布宿主机端口。

## 服务器前置条件

- Linux x86_64/arm64
- Docker Engine 与 Docker Compose v2
- Git、curl、tar
- 能访问 GitHub、npm、PyPI 及业务使用的第三方 API
- 建议至少预留足够的 CPU、内存和临时磁盘用于 FFmpeg 与图片生成结果；实际规格按项目规模压测后确定

## 首次部署

```bash
git clone <REPOSITORY_SSH_URL> VideoGen
cd VideoGen
cp .env.production.example .env.production
chmod 600 .env.production
```

填写 `.env.production` 中的真实业务密钥。不要修改或提交示例文件为真实值。

本机端口验证：

```bash
chmod +x deploy/*.sh
./deploy/deploy.sh
curl --fail http://127.0.0.1:8080/api/health
```

浏览器访问 `http://SERVER_IP:8080` 前，需将 `VIDEOGEN_BIND_ADDRESS` 改为服务器内网地址或 `0.0.0.0`，并通过防火墙限制来源。正式环境优先使用 Cloudflare Tunnel，不直接暴露 8080。

## Cloudflare Tunnel 与域名

1. 在 Cloudflare Zero Trust 创建 named tunnel。
2. 将公开 hostname 的服务指向 `http://web:80`。
3. 在 Access 中创建 Self-hosted application，只允许指定账号访问。
4. 创建 `.env.deploy`：

```bash
cp deploy/tunnel.env.example .env.deploy
chmod 600 .env.deploy
```

5. 填入 tunnel token 后启动：

```bash
docker compose --env-file .env.production -f compose.production.yml -f compose.tunnel.yml up -d
```

没有 Access 保护时，不得把 VideoGen 暴露到公网；当前应用本身没有登录鉴权。

## 发布

```bash
git fetch origin
git checkout main
git pull --ff-only
./deploy/deploy.sh
```

`deploy.sh` 会校验 Compose、在 storage 非空时先备份（无论 API 当时是否运行）、复检容器写入权限、构建镜像、更新服务并等待健康检查。它不会修改 Git、清空持久化数据，也不会删除通过 `compose.tunnel.yml` 启动的 Tunnel。

## 验收

至少验证：

1. 首页、`/api/health` 正常。
2. `/storage/db.json` 和 `/api/system/logs` 从公网返回 404。
3. 项目列表、AI 图片和背景音乐在容器重启后仍保留。
4. 完成一次文案改写、AI 提示词/出图、配音、字幕、封面、MP4 导出与下载。
5. 中文标题和字幕字体正常。
6. Cloudflare Access 未登录时无法进入站点。

## 备份

手动备份：

```bash
./deploy/backup.sh
```

备份默认保留在 `backups/`，默认删除 14 天前的归档。为保证 JSON 一致性，备份时 API 会短暂停止并自动恢复。建议通过 cron 每日执行，并把备份复制到另一台机器或对象存储。

## 回滚

```bash
git fetch --tags
git checkout <LAST_GOOD_TAG>
./deploy/deploy.sh
```

回滚代码不会回滚 `storage/`。如需恢复数据，应先停止 API，单独审查并恢复相应备份，避免覆盖新数据。
