# VideoGen 短视频草稿生成器

面向纪实解说、历史人物和知识类短视频的本地工作台。系统完成文案改写、分镜拆分、AI 图片提示词、AI 出图、配音字幕、封面合成和剪映草稿导出。

## 主要功能

- AI 改写口播文案并自动拆分分镜
- 为每个分镜生成、编辑和复制 AI 图片提示词
- 使用 Seedream 或 OpenAI 生成 9:16 竖屏分镜图片
- 对 AI 图片执行黑白转换、裁剪和去水印处理
- 使用上传的人物图片合成 9:16 视频封面
- 使用豆包语音合成模型 2.0 生成完整配音与字幕
- 导出 PNG 分镜、字幕、时间线和剪映草稿

项目不提供本地素材库、素材匹配、手动分镜图上传或联网图片搜索。

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
| MiniMax 文案与提示词 | `MINIMAX_API_KEY` |
| DeepSeek 文案与提示词 | `DEEPSEEK_API_KEY` |
| OpenAI 文案与提示词 | `OPENAI_API_KEY` |
| OpenAI 分镜出图 | `OPENAI_IMAGE_API_KEY` |
| Seedream 分镜出图与去水印 | `ARK_API_KEY` |
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

## 生产部署

生产环境使用 Docker Compose、Nginx、单 FastAPI worker 和持久化 `storage/`。完整步骤见 [`assets/deployment.md`](assets/deployment.md)。应用没有内置登录鉴权，公网开放前必须启用访问控制。
