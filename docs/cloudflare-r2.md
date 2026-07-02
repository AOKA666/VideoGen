# Cloudflare R2 私有素材库

素材库文件存入 Cloudflare R2。图片分析、裁剪和导出时，后端会使用本地缓存；缓存不存在时会从私有桶恢复。

在项目根目录的 `.env.local` 中加入：

```env
R2_ENABLED=true
R2_ACCOUNT_ID=08580c63d6ce3edd2f923ee05aa43eff
R2_BUCKET_NAME=autogen
R2_ACCESS_KEY_ID=your_r2_access_key_id
R2_SECRET_ACCESS_KEY=your_r2_secret_access_key
R2_ENDPOINT=https://08580c63d6ce3edd2f923ee05aa43eff.r2.cloudflarestorage.com
```

R2 API 令牌应限制为 `autogen` 存储桶的 Object Read & Write 权限。

安装新增依赖：

```powershell
.\services\api\.venv\Scripts\python.exe -m pip install -r .\services\api\requirements.txt
```

迁移现有素材：

```powershell
.\services\api\.venv\Scripts\python.exe .\scripts\migrate_assets_to_r2.py
```

迁移命令可重复执行，已经记录为 R2 对象的素材会自动跳过。需要重新上传全部对象时使用 `--force`。
