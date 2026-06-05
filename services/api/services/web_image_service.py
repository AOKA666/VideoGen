from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services.image_scoring_service import aspect_ratio_score, quick_score_image_for_shot
from services.image_postprocess_service import detect_blocking_watermark
from services.search_intent_service import validate_core_keyword


logger = logging.getLogger("uvicorn.error")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
RANDOM = random.SystemRandom()
TENCENT_IMAGE_HOST = "wimgs.tencentcloudapi.com"
TENCENT_IMAGE_SERVICE = "wimgs"
TENCENT_IMAGE_VERSION = "2025-11-06"
BLOCKED_SOURCE_WORDS = [
    "tuchacha", "图查查", "ztupic", "renrendoc", "人人文库", "docin", "道客巴巴",
]


@dataclass
class ImageResult:
    keyword: str
    title: str
    thumb_url: str
    image_url: str
    source_page: str
    source: str


def _request(url: str, *, timeout: int = 10, referer: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _clean_url(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).replace("\\/", "/").strip()
    return value if value.startswith(("http://", "https://")) else ""


def _unique_results(results: list[ImageResult]) -> list[ImageResult]:
    seen = set()
    unique = []
    for item in results:
        key = item.image_url or item.thumb_url
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _url_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [(k, v) for k, v in query if not k.lower().startswith(("utm_", "spm", "from"))]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(filtered), ""))


def _blocked_source_text(*values: str | None) -> str:
    return " ".join(str(value or "").lower() for value in values)


def is_blocked_image_source(*values: str | None) -> bool:
    text = _blocked_source_text(*values)
    return any(word.lower() in text for word in BLOCKED_SOURCE_WORDS)


class WebImageSearchProvider:
    def __init__(self, timeout: int = 10, provider_name: str = "so"):
        self.timeout = timeout
        self.provider_name = provider_name if provider_name in {"so", "tencent"} else "so"
        self.failures: list[dict] = []
        self.diagnostics: list[dict] = []

    def search(self, keyword: str, limit: int = 12) -> list[ImageResult]:
        results: list[ImageResult] = []
        primary_limit = max(6, limit)
        source_queries = [(
            self._search_tencent if self.provider_name == "tencent" else self._search_so,
            keyword,
            primary_limit,
        )]
        for source, query, source_limit in source_queries:
            if not query:
                continue
            started = time.monotonic()
            source_name = source.__name__.removeprefix("_search_")
            try:
                source_results = source(query, source_limit)
                results.extend(source_results)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info(
                    "[搜图] 渠道=%s 关键词=%s 返回=%d 耗时=%dms",
                    source_name,
                    query,
                    len(source_results),
                    elapsed_ms,
                )
                self.diagnostics.append({
                    "type": "source",
                    "keyword": query,
                    "source": source_name,
                    "returned": len(source_results),
                    "elapsed_ms": elapsed_ms,
                })
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "[搜图] 渠道=%s 关键词=%s 请求失败 耗时=%dms 错误=%s",
                    source_name,
                    query,
                    elapsed_ms,
                    str(exc)[:200],
                )
                self.diagnostics.append({
                    "type": "source",
                    "keyword": query,
                    "source": source_name,
                    "returned": 0,
                    "elapsed_ms": elapsed_ms,
                    "error": str(exc)[:300],
                })
                self.failures.append({
                    "keyword": query,
                    "stage": source.__name__,
                    "error": str(exc)[:300],
                })
        return _unique_results(results)[:limit]

    def _search_so(self, keyword: str, limit: int) -> list[ImageResult]:
        pool_size = max(20, min(30, limit * 3))
        start = RANDOM.choice((0, 10, 20, 30, 40))
        logger.info(
            "[搜图] 360请求 关键词=%s 随机起点=%d 候选池=%d 最多返回=%d",
            keyword,
            start,
            pool_size,
            limit,
        )
        params = {
            "q": keyword,
            "src": "srp",
            "sn": str(start),
            "pn": str(pool_size),
            "_": str(int(time.time() * 1000)),
        }
        url = f"https://image.so.com/j?{urllib.parse.urlencode(params)}"
        body = _request(url, timeout=self.timeout, referer="https://image.so.com/")
        data = json.loads(body.decode("utf-8", errors="ignore"))
        results = []
        for item in data.get("list") or []:
            image_url = _clean_url(item.get("img") or item.get("thumb"))
            if not image_url:
                continue
            if is_blocked_image_source(item.get("link"), image_url, item.get("thumb"), item.get("title")):
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=str(item.get("title") or keyword),
                thumb_url=_clean_url(item.get("thumb") or item.get("img")),
                image_url=image_url,
                source_page=_clean_url(item.get("link")),
                source="so_image_fallback",
            ))
        RANDOM.shuffle(results)
        return results[:limit]


    def _search_tencent(self, keyword: str, limit: int) -> list[ImageResult]:
        secret_id = os.getenv("TENCENT_CLOUD_SECRET_ID", "").strip()
        secret_key = os.getenv("TENCENT_CLOUD_SECRET_KEY", "").strip()
        if not secret_id or not secret_key:
            raise RuntimeError("腾讯云联网图像搜索密钥未配置")

        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
        payload = json.dumps({"Query": keyword}, ensure_ascii=False, separators=(",", ":"))
        hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_headers = (
            "content-type:application/json; charset=utf-8\n"
            f"host:{TENCENT_IMAGE_HOST}\n"
            f"x-tc-action:{'SearchByText'.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        canonical_request = "\n".join([
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_payload,
        ])
        credential_scope = f"{date}/{TENCENT_IMAGE_SERVICE}/tc3_request"
        string_to_sign = "\n".join([
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])

        def sign(key: bytes, message: str) -> bytes:
            return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

        secret_date = sign(("TC3" + secret_key).encode("utf-8"), date)
        secret_service = sign(secret_date, TENCENT_IMAGE_SERVICE)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": TENCENT_IMAGE_HOST,
            "X-TC-Action": "SearchByText",
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": TENCENT_IMAGE_VERSION,
        }
        logger.info("[搜图] 腾讯请求 关键词=%s 最多使用=%d", keyword, limit)
        request = urllib.request.Request(
            f"https://{TENCENT_IMAGE_HOST}",
            data=payload.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"腾讯图像搜索 HTTP {exc.code}: {detail[:500]}") from exc

        response = body.get("Response") or {}
        if response.get("Error"):
            error = response["Error"]
            raise RuntimeError(f"{error.get('Code', 'TencentError')}: {error.get('Message', '')}")
        results: list[ImageResult] = []
        for raw_item in response.get("Images") or []:
            try:
                item = json.loads(raw_item) if isinstance(raw_item, str) else raw_item
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            image_url = _clean_url(item.get("origPicUrl") or item.get("thumbnailUrl"))
            thumb_url = _clean_url(item.get("thumbnailUrl") or item.get("origPicUrl"))
            source_page = _clean_url(item.get("siteUrl"))
            if not image_url:
                continue
            title = str(item.get("title") or item.get("siteName") or keyword)
            if is_blocked_image_source(source_page, image_url, thumb_url, title):
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=title,
                thumb_url=thumb_url,
                image_url=image_url,
                source_page=source_page,
                source="tencent_wimgs",
            ))
        RANDOM.shuffle(results)
        return results[:limit]


def search_keywords_for_shot(shot: dict) -> list[str]:
    explicit_keywords = [str(x).strip() for x in shot.get("search_keywords") or [] if str(x).strip()]
    if not explicit_keywords:
        return []
    try:
        return [validate_core_keyword(explicit_keywords[0])]
    except ValueError:
        return []


def search_query_for_shot(shot: dict) -> str:
    return " ".join(search_keywords_for_shot(shot)[:1]).strip()


def _extension_from_content(content_type: str, url: str) -> str:
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    if guessed == ".jpe":
        guessed = ".jpg"
    if guessed in SUPPORTED_EXTS:
        return guessed
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in SUPPORTED_EXTS else ".jpg"


def _image_size(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if len(data) >= 30 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X":
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        if data[12:16] == b"VP8 " and len(data) >= 30:
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            block_len = int.from_bytes(data[i + 2:i + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
            i += 2 + max(block_len, 1)
    return None


def download_image(
    result: ImageResult,
    output_dir: Path,
    filename_stem: str,
    *,
    timeout: int = 10,
    min_width: int = 300,
    min_height: int = 300,
    min_file_size: int = 20 * 1024,
    reject_reasons: list[str] | None = None,
) -> dict | None:
    if is_blocked_image_source(result.title, result.keyword, result.source_page, result.image_url, result.thumb_url, result.source):
        if reject_reasons is not None:
            reject_reasons.append("来源屏蔽")
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(result.image_url, headers={"User-Agent": USER_AGENT, "Referer": result.source_page or result.thumb_url or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type.lower():
                if reject_reasons is not None:
                    reject_reasons.append("非图片响应")
                return None
            data = response.read(8 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        if reject_reasons is not None:
            reject_reasons.append(f"下载失败:{type(exc).__name__}")
        return None
    if len(data) < min_file_size:
        if reject_reasons is not None:
            reject_reasons.append("文件过小")
        return None
    size = _image_size(data)
    if not size:
        if reject_reasons is not None:
            reject_reasons.append("无法识别尺寸")
        return None
    width, height = size
    if width < min_width or height < min_height:
        if reject_reasons is not None:
            reject_reasons.append("尺寸过小")
        return None
    ratio = max(width / max(height, 1), height / max(width, 1))
    if ratio > 4:
        if reject_reasons is not None:
            reject_reasons.append("比例过窄")
        return None
    watermark = detect_blocking_watermark(
        data,
        " ".join([result.title, result.keyword, result.source_page, result.image_url, result.source]),
    )
    if watermark.get("rejected"):
        if reject_reasons is not None:
            reject_reasons.append(f"水印:{watermark.get('reason') or '疑似水印'}")
        return None
    ext = _extension_from_content(content_type, result.image_url)
    path = output_dir / f"{filename_stem}{ext}"
    path.write_bytes(data)
    return {
        "local_path": str(path),
        "file_url": "",
        "file_name": path.name,
        "width": width,
        "height": height,
        "file_size": len(data),
        "hash": hashlib.sha256(data).hexdigest(),
    }


def download_images_for_shot(
    shot: dict,
    output_dir: Path,
    *,
    images_per_shot: int = 6,
    images_per_keyword: int = 2,
    results_per_keyword: int = 12,
    keyword_start: int = 0,
    keyword_limit: int = 1,
    delay: float = 0.1,
    timeout: int = 6,
    on_download=None,
    exclude_urls: set[str] | None = None,
    exclude_hashes: set[str] | None = None,
    exclude_sources: set[str] | None = None,
    provider_name: str = "so",
) -> tuple[list[ImageResult], list[dict], list[dict], list[dict]]:
    provider = WebImageSearchProvider(timeout=timeout, provider_name=provider_name)
    download_batch_id = uuid4().hex[:8]
    search_results: list[ImageResult] = []
    downloaded: list[dict] = []
    failures: list[dict] = []
    diagnostics: list[dict] = []
    seen_hashes = set(exclude_hashes or set())
    seen_urls = {_url_key(url) for url in (exclude_urls or set()) if url}
    seen_sources = {_url_key(url) for url in (exclude_sources or set()) if url}
    seen_dimensions = set()

    keywords = search_keywords_for_shot(shot)[keyword_start:keyword_start + keyword_limit]
    if not keywords:
        logger.warning("[搜图] 镜头=%s 没有有效核心关键词，跳过", shot.get("shot_index"))
    for offset, keyword in enumerate(keywords, start=1):
        keyword_index = keyword_start + offset
        if len(downloaded) >= images_per_shot:
            break
        logger.info(
            "[搜图] 镜头=%s 关键词=%s 开始检查候选=%d 下载目标=%d",
            shot.get("shot_index"),
            keyword,
            results_per_keyword,
            images_per_keyword,
        )
        results = provider.search(keyword, results_per_keyword)
        search_results.extend(results)
        keyword_diag = {
            "type": "keyword",
            "keyword": keyword,
            "result_count": len(results),
            "attempted_downloads": 0,
            "downloaded": 0,
            "rejected": {},
        }
        if not results:
            failures.append({"keyword": keyword, "stage": "search", "error": "no image result"})
            diagnostics.append(keyword_diag)
            continue
        if keyword_index > 1 and delay:
            time.sleep(delay)
        keyword_downloaded = 0
        for result_index, result in enumerate(results, start=1):
            if len(downloaded) >= images_per_shot or keyword_downloaded >= images_per_keyword:
                break
            candidate_key = _url_key(result.image_url or result.thumb_url)
            source_key = _url_key(result.source_page)
            if candidate_key in seen_urls or (source_key and source_key in seen_sources):
                keyword_diag["rejected"]["重复URL/来源"] = keyword_diag["rejected"].get("重复URL/来源", 0) + 1
                logger.info(
                    "[搜图] 镜头=%s 候选=%d/%d 过滤=重复URL或来源",
                    shot.get("shot_index"),
                    result_index,
                    len(results),
                )
                continue
            seen_urls.add(candidate_key)
            if source_key:
                seen_sources.add(source_key)
            reject_reasons: list[str] = []
            keyword_diag["attempted_downloads"] += 1
            item = download_image(
                result,
                output_dir,
                f"shot_{shot['shot_index']:03d}_{download_batch_id}_kw_{keyword_index:02d}_img_{result_index:03d}",
                timeout=timeout,
                reject_reasons=reject_reasons,
            )
            if not item:
                reason = reject_reasons[0] if reject_reasons else "未知过滤"
                keyword_diag["rejected"][reason] = keyword_diag["rejected"].get(reason, 0) + 1
                logger.info(
                    "[搜图] 镜头=%s 候选=%d/%d 过滤=%s",
                    shot.get("shot_index"),
                    result_index,
                    len(results),
                    reason,
                )
                continue
            dimension_key = (item["width"], item["height"], item["file_size"])
            if item["hash"] in seen_hashes or dimension_key in seen_dimensions:
                Path(item["local_path"]).unlink(missing_ok=True)
                keyword_diag["rejected"]["重复图片"] = keyword_diag["rejected"].get("重复图片", 0) + 1
                logger.info(
                    "[搜图] 镜头=%s 候选=%d/%d 过滤=重复图片",
                    shot.get("shot_index"),
                    result_index,
                    len(results),
                )
                continue
            seen_hashes.add(item["hash"])
            seen_dimensions.add(dimension_key)
            item.update({
                "keyword": result.keyword,
                "title": result.title,
                "source_page": result.source_page,
                "image_url": result.image_url,
                "source": result.source,
                "aspect_ratio_score": aspect_ratio_score(item),
            })
            quick_score = quick_score_image_for_shot(shot, item)
            if quick_score.get("non_photo_reasons"):
                Path(item["local_path"]).unlink(missing_ok=True)
                reason = f"非照片:{quick_score['non_photo_reasons'][0]}"
                keyword_diag["rejected"][reason] = keyword_diag["rejected"].get(reason, 0) + 1
                logger.info(
                    "[搜图] 镜头=%s 候选=%d/%d 过滤=%s",
                    shot.get("shot_index"),
                    result_index,
                    len(results),
                    reason,
                )
                continue
            downloaded.append(item)
            keyword_downloaded += 1
            keyword_diag["downloaded"] += 1
            logger.info(
                "[搜图] 镜头=%s 候选=%d/%d 下载成功=%dx%d 来源=%s",
                shot.get("shot_index"),
                result_index,
                len(results),
                item["width"],
                item["height"],
                result.source_page or result.image_url,
            )
            if on_download:
                on_download(item, len(downloaded))
        diagnostics.append(keyword_diag)
        logger.info(
            "[搜图] 镜头=%s 关键词=%s 本轮完成 返回=%d 尝试下载=%d 成功=%d 过滤=%s",
            shot.get("shot_index"),
            keyword,
            len(results),
            keyword_diag["attempted_downloads"],
            keyword_diag["downloaded"],
            keyword_diag["rejected"] or "无",
        )

    failures.extend(provider.failures)
    diagnostics.extend(provider.diagnostics)
    downloaded.sort(key=lambda item: (item.get("aspect_ratio_score") or 0, item.get("width", 0) * item.get("height", 0)), reverse=True)
    return search_results, downloaded, failures, diagnostics
