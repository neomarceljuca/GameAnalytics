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
        raise argparse.ArgumentTypeError("Use uma data válida: AAAA-MM-DD.") from exc


def parse_args() -> argparse.Namespace:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description="Consulta métricas diárias de uma ilha e salva o JSON bruto."
    )
    parser.add_argument("--island-code", required=True, help="Código: 1234-1234-1234")
    parser.add_argument(
        "--from-date",
        type=parse_date,
        default=today - timedelta(days=1),
        help="Início incluído, em UTC (AAAA-MM-DD). Padrão: ontem em UTC.",
    )
    parser.add_argument(
        "--to-date",
        type=parse_date,
        default=today,
        help="Fim excluído, em UTC (AAAA-MM-DD). Padrão: hoje em UTC.",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}", args.island_code):
        parser.error("--island-code deve ter o formato 1234-1234-1234.")
    if args.from_date >= args.to_date:
        parser.error("--from-date deve ser anterior a --to-date.")
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
            raise TypeError("A resposta JSON não é um objeto de métricas.")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = " Aguarde antes de tentar novamente." if status == 429 else ""
        print(f"Erro HTTP {status} ao consultar a API.{hint}", file=sys.stderr)
        return 1
    except httpx.RequestError as exc:
        print(f"Falha de conexão ou timeout: {exc}", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as exc:
        print(f"Resposta inválida da API: {exc}", file=sys.stderr)
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
        print(f"Falha ao salvar o JSON: {exc}", file=sys.stderr)
        return 1

    print(f"HTTP {response.status_code} | Ilha {args.island_code} | Intervalo day")
    print(f"Período UTC: {params['from']} (incluído) até {params['to']} (excluído)")
    print(f"Métricas recebidas: {', '.join(payload)}")
    print(f"JSON salvo em: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
