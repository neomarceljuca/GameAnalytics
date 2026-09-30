"""Collect one island's daily metrics and preserve the API response locally."""

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

BASE_URL = "https://api.fortnite.com/ecosystem/v1"
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a valid date: YYYY-MM-DD.") from exc


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description="Retrieve daily metrics for one island and save the raw JSON response."
    )
    parser.add_argument(
        "--island-code", required=True, help="Island code: 1234-1234-1234"
    )
    parser.add_argument(
        "--from-date",
        type=parse_date,
        default=today - timedelta(days=1),
        help="Inclusive start date in UTC (YYYY-MM-DD). Default: yesterday in UTC.",
    )
    parser.add_argument(
        "--to-date",
        type=parse_date,
        default=today,
        help="Exclusive end date in UTC (YYYY-MM-DD). Default: today in UTC.",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}", args.island_code):
        parser.error("--island-code must use the format 1234-1234-1234.")
    if args.from_date >= args.to_date:
        parser.error("--from-date must be earlier than --to-date.")
    return args


def main() -> int:
    args = parse_args()
    url = f"{BASE_URL}/islands/{args.island_code}/metrics/day"
    params = {
        "from": f"{args.from_date.isoformat()}T00:00:00Z",
        "to": f"{args.to_date.isoformat()}T00:00:00Z",
    }

    try:
        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("The JSON response is not a metrics object.")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = " Wait before trying again." if status == 429 else ""
        print(f"HTTP error {status} while querying the API.{hint}", file=sys.stderr)
        return 1
    except httpx.RequestError as exc:
        print(f"Connection failure or timeout: {exc}", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as exc:
        print(f"Invalid API response: {exc}", file=sys.stderr)
        return 1

    collected_at = datetime.now(UTC)
    document = {
        "collection": {
            "collected_at_utc": collected_at.isoformat(),
            "island_code": args.island_code,
            "interval": "day",
            "url": str(response.url),
            "params": params,
            "http_status": response.status_code,
        },
        "data": payload,
    }
    filename = (
        f"{args.island_code}_day_{collected_at.strftime('%Y%m%dT%H%M%S%fZ')}"
        f"_{uuid4().hex[:8]}.json"
    )
    output = RAW_DIR / filename
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        with output.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    except OSError as exc:
        print(f"Failed to save the JSON file: {exc}", file=sys.stderr)
        return 1

    print(f"HTTP {response.status_code} | Island {args.island_code} | Interval day")
    print(f"UTC period: {params['from']} (inclusive) to {params['to']} (exclusive)")
    print(f"Metrics received: {', '.join(payload)}")
    print(f"JSON saved to: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
