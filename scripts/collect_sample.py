"""Collect a fixed sample's daily history and report coverage per island."""

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

if __package__:
    from .collect_island import BASE_URL, RAW_DIR, parse_date
    from .metric_validation import validate_metrics
else:
    from collect_island import BASE_URL, RAW_DIR, parse_date
    from metric_validation import validate_metrics

ROOT = Path(__file__).resolve().parents[1]


def load_sample(path: Path) -> tuple[dict, str]:
    content = path.read_bytes()
    manifest = json.loads(content)
    if not isinstance(manifest, dict) or manifest.get("sample_version") != 1:
        raise ValueError("Expected a sample_version 1 manifest.")
    islands = manifest.get("islands")
    if not isinstance(islands, list) or not islands:
        raise ValueError("The manifest must contain a nonempty islands array.")
    codes = set()
    for island in islands:
        code = island.get("island_code") if isinstance(island, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"\d{4}-\d{4}-\d{4}", code):
            raise ValueError("Invalid island code in the manifest.")
        if code in codes:
            raise ValueError(f"Duplicate island code in the manifest: {code}")
        codes.add(code)
    return manifest, hashlib.sha256(content).hexdigest()


def save_json(path: Path, document: dict) -> None:
    """Replace a report atomically; raw paths are unique within each run."""
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        for attempt in range(4):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                # Windows readers/scanners may briefly hold the destination open.
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def collect_one(client, code, start, end, output):
    params = {"from": f"{start}T00:00:00Z", "to": f"{end}T00:00:00Z"}
    url = f"{BASE_URL}/islands/{code}/metrics/day"
    result = {
        "island_code": code,
        "status": "pending",
        "http_status": None,
        "raw_file": None,
    }
    try:
        response = client.get(url, params=params)
    except httpx.RequestError as exc:
        return {**result, "status": "network_error", "error": str(exc)}
    result["http_status"] = response.status_code
    raw = {
        "collection": {
            "collected_at_utc": datetime.now(UTC).isoformat(),
            "island_code": code,
            "interval": "day",
            "url": str(response.url),
            "params": params,
            "http_status": response.status_code,
        },
    }
    try:
        payload = response.json()
        # The original body is still available if it contains nonstandard JSON numbers.
        json.dumps(payload, allow_nan=False)
        raw["data"] = payload
    except ValueError:
        payload = None
        raw["body_text"] = response.text
    try:
        save_json(output, raw)
    except OSError as exc:
        return {**result, "status": "write_error", "error": str(exc)}
    result["raw_file"] = output.name
    if not response.is_success:
        return {
            **result,
            "status": "http_error",
            "retry_after": response.headers.get("retry-after"),
        }
    if "data" not in raw:
        return {**result, "status": "invalid_json"}
    validation = validate_metrics(payload, start, end)
    return {**result, "status": validation["status"], "validation": validation}


def collect_batch(client, manifest, digest, start, end, run_dir, delay=0.25):
    run_dir.mkdir(parents=True, exist_ok=False)
    save_json(run_dir / "sample.json", manifest)
    report = {
        "report_version": 1,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "state": "running",
        "sample_sha256": digest,
        "counts": {"pending": len(manifest["islands"])},
        "params": {
            "from": f"{start}T00:00:00Z",
            "to": f"{end}T00:00:00Z",
            "interval": "day",
        },
        "results": [
            {"island_code": row["island_code"], "status": "pending"}
            for row in manifest["islands"]
        ],
    }
    report_path = run_dir / "report.json"
    save_json(report_path, report)
    rate_limited = False
    for index, row in enumerate(report["results"]):
        code = row["island_code"]
        if rate_limited:
            result = {"island_code": code, "status": "skipped_rate_limit"}
        else:
            if index and delay:
                time.sleep(delay)
            result = collect_one(client, code, start, end, run_dir / f"{code}.json")
            rate_limited = result.get("http_status") == 429
        report["results"][index] = result
        report["counts"] = dict(Counter(r["status"] for r in report["results"]))
        save_json(report_path, report)
        print(f"[{index + 1}/{len(report['results'])}] {code}: {result['status']}")
    report["state"] = "finished"
    report["finished_at_utc"] = datetime.now(UTC).isoformat()
    save_json(report_path, report)
    return report


def main() -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=ROOT / "config/islands.json")
    parser.add_argument(
        "--from-date",
        type=parse_date,
        default=today - timedelta(days=6),
        help="Inclusive UTC date. Default: six days ago.",
    )
    parser.add_argument(
        "--to-date",
        type=parse_date,
        default=today,
        help="Exclusive UTC date. Default: today.",
    )
    args = parser.parse_args()
    if not today - timedelta(days=7) <= args.from_date < args.to_date <= today:
        parser.error(
            "Use a nonempty period within the last seven days, excluding the current UTC day."
        )
    try:
        manifest, digest = load_sample(args.sample)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex[:8]
        run_dir = RAW_DIR / f"batch_{run_id}"
        with httpx.Client(timeout=30) as client:
            report = collect_batch(
                client, manifest, digest, args.from_date, args.to_date, run_dir
            )
    except (OSError, ValueError, TypeError) as exc:
        print(f"Batch collection failed: {exc}", file=sys.stderr)
        return 1
    print(f"Summary: {report['counts']}")
    print(f"Report saved to: {run_dir / 'report.json'}")
    return 0 if all(r["status"] == "ok" for r in report["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
