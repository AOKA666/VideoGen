from __future__ import annotations

import re
import urllib.parse


URL_PATTERN = re.compile(r"https?://[^\s，。；;、)）\]】>\"']+", re.I)


def url_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered = [
        (key, value)
        for key, value in query
        if not key.lower().startswith(("utm_", "spm", "from"))
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        urllib.parse.urlencode(filtered),
        "",
    ))


def first_url(*values: str | None) -> str:
    for value in values:
        match = URL_PATTERN.search(str(value or ""))
        if match:
            return match.group(0).strip()
    return ""


def normalize_asset_source_fields(asset: dict) -> bool:
    changed = False
    original_path = str(asset.get("original_path") or "")
    source_note = str(asset.get("source_note") or "")
    remote_url = str(asset.get("remote_url") or "")
    source_page = str(asset.get("source_page") or "")

    if not remote_url:
        candidate = first_url(remote_url, original_path)
        if candidate:
            asset["remote_url"] = candidate
            remote_url = candidate
            changed = True

    if not source_page:
        candidate = first_url(source_page, source_note, original_path)
        if candidate:
            asset["source_page"] = candidate
            source_page = candidate
            changed = True

    source_key = url_key(source_page or remote_url or original_path)
    if source_key and asset.get("source_key") != source_key:
        asset["source_key"] = source_key
        changed = True

    if not asset.get("source_type"):
        if source_page or remote_url or first_url(original_path):
            asset["source_type"] = "web"
        else:
            asset["source_type"] = "manual_upload"
        changed = True

    return changed


def asset_source_keys(asset: dict) -> set[str]:
    keys = {
        url_key(str(asset.get("remote_url") or "")),
        url_key(str(asset.get("source_page") or "")),
        url_key(str(asset.get("source_key") or "")),
        url_key(str(asset.get("original_path") or "")),
    }
    return {key for key in keys if key}
