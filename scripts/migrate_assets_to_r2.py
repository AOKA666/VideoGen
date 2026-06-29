from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from services.env import load_env_local
from services.r2_storage import asset_object_key, check_r2_connection, ensure_asset_local, r2_enabled, upload_asset, upload_asset_metadata
from services.store import load_db, save_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate material-library files to Cloudflare R2.")
    parser.add_argument("--force", action="store_true", help="Upload assets already marked as R2 objects.")
    args = parser.parse_args()
    load_env_local()
    if not r2_enabled():
        print("R2_ENABLED must be true before migration", file=sys.stderr)
        return 2
    try:
        check_r2_connection()
    except Exception as exc:
        print(f"R2 connection failed: {exc}", file=sys.stderr)
        return 2
    db = load_db()
    migrated = metadata_synced = skipped = 0
    failed = 0

    for asset in db.get("assets", []):
        if asset.get("storage_provider") == "cloudflare_r2" and asset.get("object_key") and not args.force:
            local_path = ensure_asset_local(asset)
            if asset.get("object_key") != asset_object_key(asset, local_path):
                try:
                    upload_asset(asset, local_path)
                    save_db(db)
                    migrated += 1
                    print(f"renamed {asset.get('id')} {asset.get('file_name')}")
                except Exception as exc:
                    failed += 1
                    print(f"failed rename {asset.get('id')}: {exc}", file=sys.stderr)
                continue
            try:
                upload_asset_metadata(asset)
                save_db(db)
                metadata_synced += 1
                print(f"metadata {asset.get('id')} {asset.get('file_name')}")
            except Exception as exc:
                failed += 1
                print(f"failed metadata {asset.get('id')}: {exc}", file=sys.stderr)
            continue
        try:
            upload_asset(asset, ensure_asset_local(asset))
            save_db(db)
            migrated += 1
            print(f"migrated {asset.get('id')} {asset.get('file_name')}")
        except Exception as exc:
            failed += 1
            print(f"failed {asset.get('id')}: {exc}", file=sys.stderr)

    print(f"completed: migrated={migrated} metadata_synced={metadata_synced} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
