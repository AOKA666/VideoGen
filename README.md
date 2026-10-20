# VideoGen 短视频草稿生成器

面向口播和带货短视频的本地工作台。系统完成文案输入、配音字幕、两行标题、背景音乐、封面合成、带货画中画和剪映草稿导出。

## 主要功能

- 上传并管理普通视频素材与带货素材
- 使用豆包语音合成模型 2.0 生成完整配音与字幕，或上传现成音频
- 使用 MiniMax 生成两行式封面标题候选
- 使用上传的人物图片合成 9:16 视频封面
- 上传背景音乐并设置起始位置、音量与循环淡入淡出
- 在文案指定位置叠加带货画中画
- 导出包含背景模板、视频素材、配音、字幕、标题、音乐、封面和带货画面的剪映草稿

## 环境要求

- Windows 10/11
- Python 3.11+
- Node.js 18+
- FFmpeg 和 FFprobe，且可从命令行直接调用
- 剪映专业版（导出剪映草稿时需要）

## 环境变量

在项目根目录创建 `.env.local`。后端启动时会自动读取根目录或 `services/api` 下的同名文件，且不会覆盖系统环境中已有的变量。完整配置见 [`.env.local.example`](.env.local.example)。

主要配置：

| 功能 | 变量 |
| --- | --- |
| MiniMax AI 标题候选 | `MINIMAX_API_KEY` |
| 豆包配音与时间戳 | `VOLC_TTS_APP_ID`、`VOLC_TTS_ACCESS_KEY` |

前端 API 地址放在 `apps/web/.env.local`：

```env
VITE_API_URL=http://127.0.0.1:8000
```

## 启动

后端：

```powershell
python -m venv .\services\api\.venv
.\services\api\.venv\Scripts\python.exe -m pip install -r .\services\api\requirements.txt
Set-Location services/api
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```powershell
Set-Location apps/web
npm install
npm run dev
```

默认访问 `http://127.0.0.1:5173`，健康检查为 `http://127.0.0.1:8000/api/health`。

### 本地软件模式

完成依赖安装后，双击项目根目录的 `VideoGen.bat`。启动脚本会按需构建前端、在后台启动本地服务，并打开 `http://127.0.0.1:8000`。

侧栏“设置”中可以分别选择项目目录和剪映草稿目录。目录配置保存在当前 Windows 用户的 `%LOCALAPPDATA%\VideoGen\settings.json`，切换项目目录不会自动移动旧目录中的项目。

## 导出位置

- 项目导出：`storage/projects/<project_id>/exports`
- 生成封面：`storage/projects/<project_id>/cover/cover.png`
- 剪映草稿：`JIANYING_DRAFTS_DIR/VideoGen_<项目名>`

已生成的项目封面会自动写入剪映草稿缩略图和项目内的“封面”位置，无需在剪映中重复上传。封面使用独立子草稿，不占用正片时间线；重新导出时会使用当前项目封面。

## 生产部署

生产环境使用 Docker Compose、Nginx、单 FastAPI worker 和持久化 `storage/`。完整步骤见 [`assets/deployment.md`](assets/deployment.md)。应用没有内置登录鉴权，公网开放前必须启用访问控制。
