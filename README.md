# 真实素材优先的 AI 短视频草稿生成器

面向历史人物、纪实解说、国之脊梁类短视频的本地 MVP 工具。

## 功能

- 创建项目并输入原始文案
- 规则版 AI 二创口播稿，可手动编辑
- 自动拆分分镜并生成素材搜索意图
- 进入素材库前先选择一个文件夹作为素材库
- 后续上传只有一个入口：上传后自动打标签并放入素材库
- 图片入库时优先调用 GLM API 自动生成标签
- 按人物、场景、关键词、年代/风格和质量进行素材匹配
- 无匹配镜头生成 AI 占位图提示词和占位画面
- 生成静音配音 WAV、SRT 字幕、时间线 JSON
- 导出素材包 ZIP，包含文案、分镜、字幕、时间线、匹配报告和素材

## 环境变量

在项目根目录创建 `.env.local`：

```env
BIGMODEL_API_KEY=你的智谱 API Key
BIGMODEL_MODEL=glm-5.1
BIGMODEL_ENDPOINT=https://open.bigmodel.cn/api/paas/v4
```

可以从 `.env.local.example` 复制一份再填写密钥：

```powershell
Copy-Item .env.local.example .env.local
```

后端启动时会自动读取项目根目录的 `.env.local`。如果没有配置 `BIGMODEL_API_KEY`，或接口调用失败，系统会用文件名规则生成标签，并在素材记录里写入 `analysis_provider` / `analysis_error`。

## 启动

后端：

```powershell
cd services/api
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd apps/web
npm install
npm run dev
```

打开 http://127.0.0.1:5173

## 目录

```text
apps/web          React 前端工作台
services/api      FastAPI 后端
storage           本地素材、项目、导出文件
```
