"""Archive genre rankings and build a fixed, rank-based pilot sample."""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.fortnite.com/ecosystem/v1"


def load_design(path: Path) -> dict:
    design = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(design, dict):
        raise TypeError("The sampling design must be a JSON object.")
    genres = design.get("genres")
    ranks = design.get("ranks")
    size = design.get("frame_size")
    if design.get("design_version") != 1:
        raise ValueError("Unsupported design_version.")
    if (
        not isinstance(genres, list)
        or not genres
        or any(
            not isinstance(g, str) or not re.fullmatch(r"[a-z0-9-]+", g) for g in genres
        )
        or len(set(genres)) != len(genres)
    ):
        raise ValueError("genres must contain distinct genre slugs.")
    if type(size) is not int or not 1 <= size <= 1000:
        raise ValueError("frame_size must be an integer between 1 and 1000.")
    if (
        not isinstance(ranks, list)
        or not ranks
        or any(type(r) is not int or not 1 <= r <= size for r in ranks)
        or len(set(ranks)) != len(ranks)
    ):
        raise ValueError("ranks must contain distinct integers within the frame.")
    return design


def fetch_json(client: httpx.Client, path: str, params: dict, archive: Path) -> dict:
    response = client.get(f"{BASE_URL}{path}", params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Expected a JSON object.")
    document = {
        "collection": {
            "collected_at_utc": datetime.now(UTC).isoformat(),
            "url": str(response.url),
            "params": params,
            "http_status": response.status_code,
        },
        "data": payload,
    }
    with archive.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return payload


def build_sample(client: httpx.Client, design: dict, archive_dir: Path) -> dict:
    """Use the first genre's latest snapshot, then pin every other genre to it."""
    archive_dir.mkdir(parents=True, exist_ok=False)
    catalog = fetch_json(client, "/genres", {}, archive_dir / "genres.json")
    available = {row["slug"] for row in catalog["data"]}
    unknown = set(design["genres"]) - available
    if unknown:
        raise ValueError(f"Unknown genres: {sorted(unknown)}")
    snapshot = None
    islands = {}
    frames = []
    for genre in design["genres"]:
        params = {"size": design["frame_size"]}
        if snapshot is not None:
            params["at"] = snapshot
        filename = archive_dir / f"rankings_{genre}.json"
        payload = fetch_json(client, f"/genres/{genre}/rankings", params, filename)
        meta = payload["meta"]
        resolved = meta.get("snapshot")
        if meta.get("snapshotAvailable") is not True or not resolved:
            raise ValueError(f"No ranking snapshot available for {genre}.")
        if snapshot is not None and resolved != snapshot:
            raise ValueError(f"Snapshot mismatch for {genre}.")
        snapshot = resolved
        by_rank = {}
        seen_codes = set()
        for row in payload["data"]:
            rank, code = row["rank"], row["islandCode"]
            if type(rank) is not int or rank in by_rank:
                raise ValueError(f"Invalid or duplicate rank in {genre}.")
            if not isinstance(code, str) or not re.fullmatch(
                r"\d{4}-\d{4}-\d{4}", code
            ):
                raise ValueError(f"Invalid island code in {genre}.")
            if code in seen_codes:
                raise ValueError(f"Duplicate island code in {genre}.")
            seen_codes.add(code)
            by_rank[rank] = code
        missing = set(design["ranks"]) - by_rank.keys()
        if missing:
            raise ValueError(f"Missing requested ranks in {genre}: {sorted(missing)}")
        frames.append(
            {"genre": genre, "total_ranked": meta["total"], "returned": len(by_rank)}
        )
        for rank in design["ranks"]:
            code = by_rank[rank]
            island = islands.setdefault(code, {"island_code": code, "memberships": []})
            island["memberships"].append({"genre": genre, "selection_rank": rank})
    return {
        "sample_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "snapshot_utc": snapshot,
        "design": design,
        "frames": frames,
        "source_directory": archive_dir.relative_to(ROOT).as_posix(),
        "islands": list(islands.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design", type=Path, default=ROOT / "config/sample-design.json"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "config/islands.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Output already exists. Use a new path to preserve the fixed sample."
        )
    try:
        design = load_design(args.design)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
        archive_dir = ROOT / "data/raw" / f"sample_{run_id}"
        with httpx.Client(timeout=30) as client:
            sample = build_sample(client, design, archive_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(sample, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Sample creation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Selected {len(sample['islands'])} distinct islands at {sample['snapshot_utc']}."
    )
    print(f"Manifest saved to: {args.output}")
    print(f"Raw evidence saved to: {archive_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
