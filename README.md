# VideoGen 短视频草稿生成器

面向纪实解说、历史人物和知识类短视频的本地工作台。系统可以完成文案改写、分镜拆分、图片管理、配音字幕、上传图片合成封面和剪映草稿导出。

## 主要功能

- AI 改写口播文案并自动拆分分镜
- 使用腾讯云或 360 图片搜索分镜素材
- 手动上传、裁剪、去水印和管理素材库
- 使用 Seedream 生成 9:16 竖屏分镜图片，使用上传图片合成 9:16 视频封面
- 使用豆包语音合成模型 2.0 一次性生成完整配音
- 读取字词级时间戳，生成每条不超过 9 个汉字的字幕
- 导出 PNG 分镜、字幕、时间线和剪映草稿
- 剪映草稿包含缩放组合动画和独立视频封面

## 环境要求

- Windows 10/11
- Python 3.11+
- Node.js 18+
- FFmpeg 和 FFprobe，且可从命令行直接调用
- 剪映专业版（导出剪映草稿时需要）

## 环境变量

在项目根目录创建 `.env.local`。后端启动时会自动读取：

1. `VideoGen/.env.local`
2. `VideoGen/services/api/.env.local`

已经存在于系统环境中的变量不会被 `.env.local` 覆盖。

### 完整示例

下面列出了项目支持的全部配置项。不要把真实密钥提交到 Git。

```env
# MiniMax：文案改写、搜索意图和素材库图片识别
MINIMAX_API_KEY=your_minimax_api_key
MINIMAX_ENDPOINT=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M3
MINIMAX_IMAGE_MODEL=MiniMax-M3

# DeepSeek：文案界面可选创作模型
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_ENDPOINT=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

# OpenAI：文案界面可选创作模型
OPENAI_API_KEY=your_openai_api_key
OPENAI_ENDPOINT=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.6
# OpenAI：分镜图片生成（与文案 Key 分开）
OPENAI_IMAGE_API_KEY=your_openai_image_api_key
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_QUALITY=medium
WHISPERX_COMMAND=whisperx
WHISPERX_MODEL=small
WHISPERX_LANGUAGE=zh
WHISPERX_DEVICE=cpu
WHISPERX_COMPUTE_TYPE=int8

# 火山方舟 / Seedream：AI 分镜图和去水印
ARK_API_KEY=your_ark_api_key
ARK_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3
ARK_IMAGE_MODEL=doubao-seedream-4-0-250828
ARK_IMAGE_EDIT_MODEL=doubao-seedream-4-0-250828

# 腾讯云图片搜索
TENCENT_CLOUD_SECRET_ID=your_tencent_secret_id
TENCENT_CLOUD_SECRET_KEY=your_tencent_secret_key

# 豆包语音合成模型 2.0
VOLC_TTS_APP_ID=your_volc_tts_app_id
VOLC_TTS_ACCESS_KEY=your_volc_tts_access_key
VOLC_TTS_ENDPOINT=https://openspeech.bytedance.com/api/v3/tts/unidirectional
VOLC_TTS_RESOURCE_ID=seed-tts-2.0
VOLC_TTS_CLONED_RESOURCE_ID=seed-icl-2.0
VOLC_TTS_VOICE=zh_male_dongfanghaoran_uranus_bigtts

# 可选：部分火山账号使用 API Key 鉴权，可替代 APP ID + Access Key
VOLC_TTS_API_KEY=

# 可选：火山接口的 App Key；不填时使用代码内置的公版值
VOLC_TTS_APP_KEY=

# 兼容保留：当前主流程不直接读取
VOLC_TTS_SECRET_KEY=
VOLC_TTS_MODEL=

# 图片搜索并发数，范围 1-8
WEB_IMAGE_CONCURRENCY=6

# 剪映草稿目录；不填时会自动尝试 E:\JianyingPro Drafts
JIANYING_DRAFTS_DIR=E:\JianyingPro Drafts
```

### 必填项

按当前完整工作流，建议至少配置：

| 功能 | 必填变量 |
| --- | --- |
| 文案改写、搜索意图和素材库图片识别 | `MINIMAX_API_KEY` |
| 文案界面选择 DeepSeek 时 | `DEEPSEEK_API_KEY` |
| 文案界面选择 OpenAI 时 | `OPENAI_API_KEY` |
| 分镜出图选择 OpenAI 时 | `OPENAI_IMAGE_API_KEY` |
| Seedream 分镜图片与去水印 | `ARK_API_KEY` |
| 腾讯图片搜索 | `TENCENT_CLOUD_SECRET_ID`、`TENCENT_CLOUD_SECRET_KEY` |
| 豆包配音与时间戳 | `VOLC_TTS_APP_ID`、`VOLC_TTS_ACCESS_KEY` |

其余变量都有默认值或属于可选覆盖项。

### 前端 API 地址

前端变量放在 `apps/web/.env.local`：

```env
VITE_API_URL=http://127.0.0.1:8000
```

未配置时默认访问 `http://127.0.0.1:8000`。如需使用其他端口，请在 `apps/web/.env.local` 中设置对应地址；修改后需要重新启动 Vite。

## 启动

### 后端

```powershell
cd C:\Users\27819\Desktop\VideoGen
E:\Python\python.exe -m venv .\services\api\.venv
.\services\api\.venv\Scripts\python.exe -m pip install -r .\services\api\requirements.txt
cd services/api
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```text
http://127.0.0.1:8000/api/health
```

### 前端

```powershell
cd apps/web
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

## 导出位置

- 项目导出：`storage/projects/<project_id>/exports`
- 生成封面：`storage/projects/<project_id>/cover/cover.png`
- 剪映草稿：`JIANYING_DRAFTS_DIR/VideoGen_<项目名>`

## 目录结构

```text
apps/web          React 前端
services/api      FastAPI 后端
storage           素材、项目文件和导出结果
```

## 生产部署

生产环境使用 Docker Compose、Nginx、单 FastAPI worker 和持久化 `storage/`。完整步骤、安全约束、备份与回滚方法见 [`docs/deployment.md`](docs/deployment.md)。正式公网开放前必须启用 Cloudflare Access，因为应用当前没有内置登录鉴权。

## 安全提示

- `.env.local` 已用于存放本地密钥，不要上传或提交到代码仓库。
- 不要在截图、日志或聊天消息中公开 Secret Key、Access Key。
- 密钥若曾泄露，应立即在对应云平台控制台轮换。
