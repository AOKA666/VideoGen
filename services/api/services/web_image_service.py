from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import math
from dataclasses import dataclass
from pathlib import Path

from services.image_scoring_service import aspect_ratio_score
from services.image_postprocess_service import detect_blocking_watermark


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
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
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.failures: list[dict] = []

    def search(self, keyword: str, limit: int = 12, archive_keyword: str | None = None) -> list[ImageResult]:
        results: list[ImageResult] = []
        primary_limit = max(6, limit)
        archive_limit = max(2, min(6, math.ceil(limit / 4)))
        archive_query = (archive_keyword or keyword).strip()
        source_queries = [
            (self._search_baidu, keyword),
            (self._search_so, keyword),
            (self._search_bing, keyword),
        ]
        if archive_query and archive_query != keyword:
            source_queries.append((self._search_bing, archive_query))
        source_queries.extend([
            (self._search_hpc, archive_query),
            (self._search_nara, archive_query),
            (self._search_ntu_old_photos, keyword),
        ])
        for source, query in source_queries:
            if not query:
                continue
            try:
                source_limit = archive_limit if source.__name__ in {"_search_hpc", "_search_nara", "_search_ntu_old_photos"} else primary_limit
                results.extend(source(query, source_limit))
            except Exception as exc:
                if source.__name__ not in {"_search_hpc", "_search_nara", "_search_ntu_old_photos"}:
                    self.failures.append({
                        "keyword": query,
                        "stage": source.__name__,
                        "error": str(exc)[:300],
                    })
        return _unique_results(results)[:limit]

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
            if is_blocked_image_source(source_page, image_url, thumb_url, item.get("fromPageTitleEnc"), item.get("fromPageTitle")):
                continue
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
            if is_blocked_image_source(item.get("purl"), image_url, item.get("t")):
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

    def _html_image_results(
        self,
        *,
        keyword: str,
        html: str,
        base_url: str,
        source: str,
        source_page: str,
        limit: int,
    ) -> list[ImageResult]:
        results: list[ImageResult] = []
        for match in re.finditer(r"<img\b[^>]*>", html, flags=re.I):
            if len(results) >= limit:
                break
            tag = match.group(0)
            src_match = re.search(r"""(?:src|data-src|data-original|data-lazy-src)=["']([^"']+)["']""", tag, flags=re.I)
            if not src_match:
                continue
            image_url = urllib.parse.urljoin(base_url, src_match.group(1).replace("&amp;", "&"))
            if not image_url.startswith(("http://", "https://")):
                continue
            lower = image_url.lower()
            if any(skip in lower for skip in ("logo", "icon", "sprite", "favicon", "placeholder", "loading")):
                continue
            title_match = re.search(r"""(?:alt|title)=["']([^"']+)["']""", tag, flags=re.I)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else keyword
            if is_blocked_image_source(source_page, image_url, title):
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=title or keyword,
                thumb_url=image_url,
                image_url=image_url,
                source_page=source_page,
                source=source,
            ))
        return results

    def _search_hpc(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {"search_api_fulltext": keyword}
        url = f"https://hpcbristol.net/search?{urllib.parse.urlencode(params)}"
        html = _request(url, timeout=min(self.timeout, 3), referer="https://hpcbristol.net/").decode("utf-8", errors="ignore")
        return self._html_image_results(
            keyword=keyword,
            html=html,
            base_url="https://hpcbristol.net/",
            source="historical_photographs_of_china",
            source_page=url,
            limit=limit,
        )

    def _search_nara(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {
            "q": f"{keyword} photograph",
            "availableOnline": "true",
            "limit": str(max(1, min(limit, 20))),
        }
        url = f"https://catalog.archives.gov/api/v2/records/search?{urllib.parse.urlencode(params)}"
        body = _request(url, timeout=min(self.timeout, 3), referer="https://catalog.archives.gov/")
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            html = body.decode("utf-8", errors="ignore")
            return self._html_image_results(
                keyword=keyword,
                html=html,
                base_url="https://catalog.archives.gov/",
                source="nara",
                source_page=f"https://catalog.archives.gov/search?q={urllib.parse.quote(keyword)}",
                limit=limit,
            )

        urls: list[str] = []

        def collect_images(value):
            if len(urls) >= limit:
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and key.lower() in {"url", "objecturl", "thumbnailurl", "downloadurl"}:
                        if item.startswith(("http://", "https://")) and re.search(r"\.(jpe?g|png|webp)(\?|$)", item, flags=re.I):
                            urls.append(item)
                    else:
                        collect_images(item)
            elif isinstance(value, list):
                for item in value:
                    collect_images(item)

        collect_images(data)
        results = []
        for image_url in list(dict.fromkeys(urls))[:limit]:
            if is_blocked_image_source(url, image_url):
                continue
            results.append(ImageResult(
                keyword=keyword,
                title=f"NARA {keyword}",
                thumb_url=image_url,
                image_url=image_url,
                source_page=url,
                source="nara",
            ))
        return results

    def _search_ntu_old_photos(self, keyword: str, limit: int) -> list[ImageResult]:
        params = {"q": keyword}
        url = f"https://dl.lib.ntu.edu.tw/s/photo/photo?{urllib.parse.urlencode(params)}"
        html = _request(url, timeout=min(self.timeout, 3), referer="https://dl.lib.ntu.edu.tw/").decode("utf-8", errors="ignore")
        return self._html_image_results(
            keyword=keyword,
            html=html,
            base_url="https://dl.lib.ntu.edu.tw/",
            source="ntu_old_photos",
            source_page=url,
            limit=limit,
        )


def search_keywords_for_shot(shot: dict) -> list[str]:
    explicit_keywords = [str(x).strip() for x in shot.get("search_keywords") or [] if str(x).strip()]
    if explicit_keywords:
        return explicit_keywords[:3]
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
    return list(dict.fromkeys(seeds))[:3]


def archive_keywords_for_shot(shot: dict) -> list[str]:
    explicit_keywords = [str(x).strip() for x in shot.get("archive_keywords") or [] if str(x).strip()]
    if explicit_keywords:
        return explicit_keywords[:3]
    keywords = search_keywords_for_shot(shot)
    people = [str(x).strip() for x in shot.get("required_object") or [] if str(x).strip()]
    scenes = [str(x).strip() for x in shot.get("required_scene") or [] if str(x).strip()]
    seeds = []
    for person in people[:2]:
        if re.search(r"[A-Za-z]", person):
            seeds.append(f"{person} photograph")
    if any("导弹" in item or "missile" in item.lower() for item in [*keywords, *scenes]):
        seeds.append("Chinese missile photograph")
    if any("战机" in item or "飞机" in item or "航班" in item for item in [*keywords, *scenes]):
        seeds.append("Chinese aircraft photograph")
    if any("军舰" in item or "航母" in item or "海军" in item for item in [*keywords, *scenes]):
        seeds.append("Chinese navy warship photograph")
    if any("大使馆" in item for item in [*keywords, *scenes]):
        seeds.append("Chinese embassy bombing photograph")
    seeds.append("China historical photograph")
    return list(dict.fromkeys(seeds))[:3]


def search_query_for_shot(shot: dict) -> str:
    return " ".join(search_keywords_for_shot(shot)[:3]).strip()


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
    if is_blocked_image_source(result.title, result.keyword, result.source_page, result.image_url, result.thumb_url, result.source):
        return None
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
    watermark = detect_blocking_watermark(
        data,
        " ".join([result.title, result.keyword, result.source_page, result.image_url, result.source]),
    )
    if watermark.get("rejected"):
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
    keyword_limit: int = 3,
    delay: float = 0.1,
    timeout: int = 6,
    on_download=None,
    exclude_urls: set[str] | None = None,
    exclude_hashes: set[str] | None = None,
    exclude_sources: set[str] | None = None,
) -> tuple[list[ImageResult], list[dict], list[dict]]:
    provider = WebImageSearchProvider(timeout=timeout)
    search_results: list[ImageResult] = []
    downloaded: list[dict] = []
    failures: list[dict] = []
    seen_hashes = set(exclude_hashes or set())
    seen_urls = {_url_key(url) for url in (exclude_urls or set()) if url}
    seen_sources = {_url_key(url) for url in (exclude_sources or set()) if url}
    seen_dimensions = set()

    keywords = search_keywords_for_shot(shot)[keyword_start:keyword_start + keyword_limit]
    archive_keywords = archive_keywords_for_shot(shot)[keyword_start:keyword_start + keyword_limit]
    for offset, keyword in enumerate(keywords, start=1):
        keyword_index = keyword_start + offset
        archive_keyword = archive_keywords[offset - 1] if offset - 1 < len(archive_keywords) else ""
        if len(downloaded) >= images_per_shot:
            break
        results = provider.search(keyword, results_per_keyword, archive_keyword=archive_keyword)
        search_results.extend(results)
        if not results:
            failures.append({"keyword": keyword, "stage": "search", "error": "no image result"})
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
                "aspect_ratio_score": aspect_ratio_score(item),
            })
            downloaded.append(item)
            keyword_downloaded += 1
            if on_download:
                on_download(item, len(downloaded))

    failures.extend(provider.failures)
    downloaded.sort(key=lambda item: (item.get("aspect_ratio_score") or 0, item.get("width", 0) * item.get("height", 0)), reverse=True)
    return search_results, downloaded, failures
