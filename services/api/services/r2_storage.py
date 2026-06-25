from __future__ import annotations

import mimetypes
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


class R2StorageError(RuntimeError):
    pass


def r2_enabled() -> bool:
    return os.getenv("R2_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _required_env() -> dict[str, str]:
    values = {
        "R2_ACCOUNT_ID": os.getenv("R2_ACCOUNT_ID", "").strip(),
        "R2_BUCKET_NAME": os.getenv("R2_BUCKET_NAME", "").strip(),
        "R2_ACCESS_KEY_ID": os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        "R2_SECRET_ACCESS_KEY": os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise R2StorageError(f"Missing R2 configuration: {', '.join(missing)}")
    return values


@lru_cache(maxsize=1)
def _client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise R2StorageError("boto3 is required for Cloudflare R2 storage") from exc

    values = _required_env()
    endpoint = os.getenv("R2_ENDPOINT", "").strip()
    if not endpoint:
        endpoint = f"https://{values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=values["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 4, "mode": "standard"}),
    )


def _bucket() -> str:
    return _required_env()["R2_BUCKET_NAME"]


def asset_object_key(asset: dict[str, Any], path: Path | None = None) -> str:
    existing = str(asset.get("object_key") or "").strip()
    if existing:
        return existing
    suffix = (path.suffix if path else Path(str(asset.get("file_name") or "")).suffix).lower()
    return f"assets/{asset['id']}{suffix or '.asset'}"


def asset_content_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}/content"


def upload_asset(asset: dict[str, Any], path: Path) -> None:
    if not r2_enabled():
        return
    if not path.is_file():
        raise R2StorageError(f"Local asset file does not exist: {path}")
    key = asset_object_key(asset, path)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        _client().upload_file(
            str(path),
            _bucket(),
            key,
            ExtraArgs={"ContentType": content_type},
        )
    except Exception as exc:
        raise R2StorageError(f"Failed to upload asset to R2: {exc}") from exc
    asset["storage_provider"] = "cloudflare_r2"
    asset["object_key"] = key
    asset["file_url"] = asset_content_url(str(asset["id"]))
    asset["thumbnail_url"] = asset["file_url"]


def ensure_asset_local(asset: dict[str, Any]) -> Path:
    local_value = str(asset.get("local_path") or "").strip()
    if local_value and Path(local_value).is_file():
        return Path(local_value)
    key = str(asset.get("object_key") or "").strip()
    if asset.get("storage_provider") != "cloudflare_r2" or not key:
        return Path(local_value)

    from services.store import ASSETS_DIR

    suffix = Path(key).suffix or Path(str(asset.get("file_name") or "")).suffix or ".asset"
    target = ASSETS_DIR / f"r2_{asset['id']}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        asset["local_path"] = str(target)
        return target
    temporary = target.with_suffix(f"{target.suffix}.download")
    try:
        _client().download_file(_bucket(), key, str(temporary))
        temporary.replace(target)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise R2StorageError(f"Failed to download asset from R2: {exc}") from exc
    asset["local_path"] = str(target)
    return target


def delete_asset_object(asset: dict[str, Any]) -> bool:
    key = str(asset.get("object_key") or "").strip()
    if asset.get("storage_provider") != "cloudflare_r2" or not key:
        return False
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except Exception as exc:
        raise R2StorageError(f"Failed to delete asset from R2: {exc}") from exc
    return True


def check_r2_connection() -> None:
    _client().head_bucket(Bucket=_bucket())
