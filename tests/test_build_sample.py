"""Check selection invariants without calling the live API."""

import json

import httpx
import pytest

from scripts import build_sample as sampling

SNAPSHOT = "2026-10-28T12:00:00.000Z"
DESIGN = {
    "design_version": 1,
    "genres": ["shooter", "simulation-tycoon"],
    "frame_size": 2,
    "ranks": [1, 2],
}


def transport(seen, failure=None):
    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/genres"):
            return httpx.Response(
                200, json={"data": [{"slug": g} for g in DESIGN["genres"]]}
            )
        second = "simulation-tycoon" in request.url.path
        payload = {
            "meta": {"snapshotAvailable": True, "snapshot": SNAPSHOT, "total": 20},
            "data": [
                {"rank": 1, "islandCode": "1111-1111-1111"},
                {
                    "rank": 2,
                    "islandCode": "3333-3333-3333" if second else "2222-2222-2222",
                },
            ],
        }
        if second:
            if failure == "missing":
                payload["data"].pop()
            elif failure == "mismatch":
                payload["meta"]["snapshot"] = "2026-10-28T13:00:00.000Z"
            elif failure == "unavailable":
                payload["meta"]["snapshotAvailable"] = False
            elif failure == "http":
                return httpx.Response(429)
            elif failure == "duplicate":
                payload["data"][1]["islandCode"] = payload["data"][0]["islandCode"]
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


@pytest.fixture
def local_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sampling, "ROOT", tmp_path)
    return tmp_path


def test_common_snapshot_and_overlapping_memberships(local_root):
    seen = []
    archive = local_root / "data/raw/selection"
    with httpx.Client(transport=transport(seen)) as client:
        manifest = sampling.build_sample(client, DESIGN, archive)
    assert "at" not in seen[1].url.params
    assert seen[2].url.params["at"] == SNAPSHOT
    assert manifest["snapshot_utc"] == SNAPSHOT
    assert len(manifest["islands"]) == 3
    shared = manifest["islands"][0]
    assert len(shared["memberships"]) == 2
    assert {m["genre"] for m in shared["memberships"]} == set(DESIGN["genres"])
    assert len(list(archive.glob("*.json"))) == 3
    raw = json.loads((archive / "rankings_shooter.json").read_text(encoding="utf-8"))
    assert raw["data"]["meta"]["snapshot"] == SNAPSHOT
    assert raw["collection"]["http_status"] == 200


@pytest.mark.parametrize(
    "failure, message",
    [
        ("missing", "Missing requested ranks"),
        ("mismatch", "Snapshot mismatch"),
        ("unavailable", "No ranking snapshot"),
        ("duplicate", "Duplicate island code"),
    ],
)
def test_incomplete_or_inconsistent_selection_fails(local_root, failure, message):
    with (
        httpx.Client(transport=transport([], failure)) as client,
        pytest.raises(ValueError, match=message),
    ):
        sampling.build_sample(client, DESIGN, local_root / "raw")


def test_http_failure_preserves_prior_evidence(local_root):
    archive = local_root / "raw"
    with (
        httpx.Client(transport=transport([], "http")) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        sampling.build_sample(client, DESIGN, archive)
    assert (archive / "rankings_shooter.json").exists()
    assert not (archive / "rankings_simulation-tycoon.json").exists()


@pytest.mark.parametrize(
    "field, value",
    [
        ("ranks", [1, 3]),
        ("ranks", [1, 1]),
        ("ranks", [True]),
        ("genres", ["shooter", "shooter"]),
        ("genres", ["../invalid"]),
        ("frame_size", 1001),
    ],
)
def test_invalid_design_rejected(tmp_path, field, value):
    design = {**DESIGN, field: value}
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    with pytest.raises(ValueError):
        sampling.load_design(path)


def test_existing_output_refused_before_network(tmp_path, monkeypatch):
    path = tmp_path / "islands.json"
    path.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["build_sample.py", "--output", str(path)])
    with pytest.raises(SystemExit) as exc:
        sampling.main()
    assert exc.value.code == 2
    assert path.read_text(encoding="utf-8") == "keep me"


def test_non_object_design_rejected(tmp_path):
    path = tmp_path / "design.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        sampling.load_design(path)
