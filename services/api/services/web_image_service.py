from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


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


class WebImageSearchProvider:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.failures: list[dict] = []

    def search(self, keyword: str, limit: int = 12) -> list[ImageResult]:
        for source in (self._search_baidu, self._search_so, self._search_bing):
            try:
                results = source(keyword, limit)
                if results:
                    return _unique_results(results)[:limit]
            except Exception as exc:
                self.failures.append({
                    "keyword": keyword,
                    "stage": source.__name__,
                    "error": str(exc)[:300],
                })
        return []

    def _search_baidu(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {
            "tn": "resultjson_com",
            "ipn": "rj",
            "ct": "201326592",
            "is": "",
            "fp": "result",
            "queryWord": keyword,
            "cl": "2",
            "lm": "-1",
            "ie": "utf-8",
            "oe": "utf-8",
            "word": keyword,
            "pn": "0",
            "rn": str(max(1, min(limit, 30))),
        }
        url = f"https://image.baidu.com/search/acjson?{urllib.parse.urlencode(params)}"
        body = _request(url, timeout=self.timeout, referer="https://image.baidu.com/")
        data = json.loads(body.decode("utf-8", errors="ignore"))
        results = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            image_url = _clean_url(item.get("objURL") or item.get("hoverURL") or item.get("middleURL") or item.get("thumbURL"))
            thumb_url = _clean_url(item.get("thumbURL") or item.get("middleURL"))
            if not image_url:
                continue
            replace_url = item.get("replaceUrl") or []
            source_page = item.get("fromURL") or (replace_url[0].get("FromURL") if replace_url and isinstance(replace_url[0], dict) else "")
            results.append(ImageResult(
                keyword=keyword,
                title=str(item.get("fromPageTitleEnc") or item.get("fromPageTitle") or keyword),
                thumb_url=thumb_url,
                image_url=image_url,
                source_page=_clean_url(source_page),
                source="baidu_image",
            ))
        return results

    def _search_so(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {"q": keyword, "src": "srp", "sn": "0", "pn": str(max(1, min(limit, 30)))}
        url = f"https://image.so.com/j?{urllib.parse.urlencode(params)}"
        body = _request(url, timeout=self.timeout, referer="https://image.so.com/")
        data = json.loads(body.decode("utf-8", errors="ignore"))
        results = []
        for item in data.get("list") or []:
            image_url = _clean_url(item.get("img") or item.get("thumb"))
            if not image_url:
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=str(item.get("title") or keyword),
                thumb_url=_clean_url(item.get("thumb") or item.get("img")),
                image_url=image_url,
                source_page=_clean_url(item.get("link")),
                source="so_image_fallback",
            ))
        return results

    def _search_bing(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {"q": keyword, "form": "HDRSC2", "first": "1"}
        url = f"https://www.bing.com/images/search?{urllib.parse.urlencode(params)}"
        html = _request(url, timeout=self.timeout, referer="https://www.bing.com/").decode("utf-8", errors="ignore")
        results = []
        for match in re.finditer(r'm="({.+?})"', html):
            if len(results) >= limit:
                break
            raw = match.group(1).replace("&quot;", '"')
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            image_url = _clean_url(item.get("murl"))
            if not image_url:
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=str(item.get("t") or keyword),
                thumb_url=_clean_url(item.get("turl")),
                image_url=image_url,
                source_page=_clean_url(item.get("purl")),
                source="bing_image_fallback",
            ))
        return results


def search_keywords_for_shot(shot: dict) -> list[str]:
    explicit_keywords = [str(x).strip() for x in shot.get("search_keywords") or [] if str(x).strip()]
    if explicit_keywords:
        return explicit_keywords
    objects = [str(x).strip() for x in shot.get("required_object") or [] if str(x).strip()]
    scenes = [str(x).strip() for x in shot.get("required_scene") or [] if str(x).strip()]
    visual_need = str(shot.get("visual_need") or "").strip()
    voice_text = str(shot.get("voice_text") or "").strip()
    seeds = []
    if objects:
        seeds.append(" ".join(objects[:2] + ["老照片"]))
        seeds.append(" ".join(objects[:2] + ["历史照片"]))
    if scenes:
        seeds.append(" ".join(scenes[:2] + ["历史照片"]))
    if visual_need:
        seeds.append(f"{visual_need} 历史照片")
    if voice_text:
        seeds.append(f"{voice_text[:24]} 配图")
    seeds.append("历史纪实 老照片")
    return list(dict.fromkeys(seeds))


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
) -> dict | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(result.image_url, headers={"User-Agent": USER_AGENT, "Referer": result.source_page or result.thumb_url or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type.lower():
                return None
            data = response.read(8 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    if len(data) < min_file_size:
        return None
    size = _image_size(data)
    if not size:
        return None
    width, height = size
    if width < min_width or height < min_height:
        return None
    ratio = max(width / max(height, 1), height / max(width, 1))
    if ratio > 4:
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
    images_per_shot: int = 3,
    results_per_keyword: int = 12,
    delay: float = 0.8,
    timeout: int = 10,
    on_download=None,
) -> tuple[list[ImageResult], list[dict], list[dict]]:
    provider = WebImageSearchProvider(timeout=timeout)
    search_results: list[ImageResult] = []
    downloaded: list[dict] = []
    failures: list[dict] = []
    seen_hashes = set()
    seen_urls = set()
    seen_sources = set()
    seen_dimensions = set()

    for keyword_index, keyword in enumerate(search_keywords_for_shot(shot), start=1):
        if len(downloaded) >= images_per_shot:
            break
        if keyword_index > 1:
            time.sleep(delay)
        results = provider.search(keyword, results_per_keyword)
        search_results.extend(results)
        if not results:
            failures.append({"keyword": keyword, "stage": "search", "error": "no image result"})
            continue
        for result_index, result in enumerate(results, start=1):
            if len(downloaded) >= images_per_shot:
                break
            candidate_key = _url_key(result.image_url or result.thumb_url)
            source_key = _url_key(result.source_page)
            if candidate_key in seen_urls or (source_key and source_key in seen_sources):
                continue
            seen_urls.add(candidate_key)
            if source_key:
                seen_sources.add(source_key)
            item = download_image(
                result,
                output_dir,
                f"shot_{shot['shot_index']:03d}_kw_{keyword_index:02d}_img_{result_index:03d}",
                timeout=timeout,
            )
            if not item:
                continue
            dimension_key = (item["width"], item["height"], item["file_size"])
            if item["hash"] in seen_hashes or dimension_key in seen_dimensions:
                Path(item["local_path"]).unlink(missing_ok=True)
                continue
            seen_hashes.add(item["hash"])
            seen_dimensions.add(dimension_key)
            item.update({
                "keyword": result.keyword,
                "title": result.title,
                "source_page": result.source_page,
                "image_url": result.image_url,
                "source": result.source,
            })
            downloaded.append(item)
            if on_download:
                on_download(item, len(downloaded))

    failures.extend(provider.failures)
    return search_results, downloaded, failures
