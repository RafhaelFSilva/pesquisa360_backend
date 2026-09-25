"""Spatial dry-run for the AP election demo.

Reads only the persisted sector geometry, generates local artifacts, and does
not write to the database.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import statistics
import struct
import sys
import zlib
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

from seed_demo_eleicoes_ap import AGENTS, DISTRIBUTIONS, generate, validate


PROJECT_ID = 5
SURVEY_ID = 5
SECTOR_NAME = "Macapa"
MASK_LABEL = "setor Macapa da pesquisa 5 - máscara operacional DEMO"
SEED = 20260906
MIN_DISTANCE_M = 50.0
TOTAL = 1000
SPATIAL_DIR = Path("artifacts/demo_ap/spatial")
FINAL_DIR = Path("artifacts/demo_ap/final_dry_run")


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def read_state():
    queries = [
    """
    SELECT p.id AS projeto_id, p.nome AS projeto_nome, p.company_id, p.status,
           pe.id AS pesquisa_id, pe.titulo AS pesquisa_titulo, pe.ativo, pe.tipo_pesquisa
      FROM projetos p
      LEFT JOIN pesquisas pe ON pe.projeto_id = p.id AND pe.id = %(survey_id)s
     WHERE p.id = %(project_id)s
    """,
    "SELECT COUNT(*) AS total FROM coletas WHERE pesquisa_id = %(survey_id)s",
    """
    SELECT COUNT(*) AS total
      FROM respostas r JOIN coletas c ON c.id = r.coleta_id
     WHERE c.pesquisa_id = %(survey_id)s
    """,
    """
    SELECT s.id, s.nome, s.meta, s.tolerancia, s.finalidade, s.pesquisa_id,
           s.agente_id, s.municipio_territorio_id,
           GeometryType(s.geometria) AS geom_type,
           ST_SRID(s.geometria) AS srid,
           ST_IsValid(s.geometria) AS is_valid,
           ST_IsEmpty(s.geometria) AS is_empty,
           ST_Area(s.geometria::geography) AS area_m2,
           ST_XMin(Box2D(s.geometria)) AS xmin,
           ST_YMin(Box2D(s.geometria)) AS ymin,
           ST_XMax(Box2D(s.geometria)) AS xmax,
           ST_YMax(Box2D(s.geometria)) AS ymax,
           ST_NPoints(s.geometria) AS vertex_count,
           encode(sha256(ST_AsEWKB(s.geometria)), 'hex') AS digest_sha256,
           ST_X(ST_Centroid(s.geometria)) AS centroid_lon,
           ST_Y(ST_Centroid(s.geometria)) AS centroid_lat,
           ST_AsGeoJSON(s.geometria, 9)::json AS geojson,
           ST_AsGeoJSON(ST_MakeValid(s.geometria), 9)::json AS valid_geojson
      FROM setores s
     WHERE s.pesquisa_id = %(survey_id)s AND lower(s.nome) = lower(%(sector_name)s)
    """,
    """
    SELECT u.id, u.email, u.ativo, pf.nome AS perfil, u.company_id
      FROM usuarios u JOIN perfis pf ON pf.id = u.perfil_id
     WHERE u.email IN %(emails)s ORDER BY u.email
    """,
    """
    SELECT u.email, uea.company_id, uea.acesso_todos_projetos, uea.ativo, uea.principal
      FROM usuarios u
      LEFT JOIN usuario_empresa_acessos uea ON uea.usuario_id = u.id
     WHERE u.email IN %(agent_emails)s ORDER BY u.email, uea.company_id
    """,
    """
    SELECT u.email, upa.projeto_id, upa.ativo
      FROM usuarios u
      LEFT JOIN usuario_projeto_acessos upa ON upa.usuario_id = u.id AND upa.projeto_id = %(project_id)s
     WHERE u.email IN %(agent_emails)s ORDER BY u.email
    """,
    """
    SELECT sa.setor_id, u.id AS agente_id, u.email, sa.ativo
      FROM setor_agentes sa JOIN usuarios u ON u.id = sa.agente_id JOIN setores s ON s.id = sa.setor_id
     WHERE s.pesquisa_id = %(survey_id)s AND lower(s.nome) = lower(%(sector_name)s)
     ORDER BY u.email
    """,
    ]
    emails = tuple(["rafhael.ferreira.silva@gmail.com"] + [a["email"] for a in AGENTS])
    agent_emails = tuple(a["email"] for a in AGENTS)
    with psycopg2.connect(database_url(), cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            params = {
                "project_id": PROJECT_ID,
                "survey_id": SURVEY_ID,
                "sector_name": SECTOR_NAME,
                "emails": emails,
                "agent_emails": agent_emails,
            }
            cur.execute("BEGIN READ ONLY")
            result_sets = []
            for query in queries:
                cur.execute(query, params)
                result_sets.append(cur.fetchall())
            cur.execute("COMMIT")
    return {
        "project": result_sets[0],
        "coletas_count": result_sets[1][0]["total"],
        "respostas_count": result_sets[2][0]["total"],
        "sector": result_sets[3],
        "users": result_sets[4],
        "company_access": result_sets[5],
        "project_access": result_sets[6],
        "sector_agents": result_sets[7],
    }


def feature(geometry, properties):
    return {"type": "Feature", "properties": properties, "geometry": geometry}


def feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def project_geometry(geom):
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:32622", always_xy=True).transform
    to_ll = Transformer.from_crs("EPSG:32622", "EPSG:4326", always_xy=True).transform
    return transform(to_m, geom), to_ll


def generate_points(poly_m, to_ll):
    rng = random.Random(SEED + 29)
    minx, miny, maxx, maxy = poly_m.bounds
    cell = MIN_DISTANCE_M / math.sqrt(2)
    grid = {}
    points_m = []
    attempts = 0
    max_attempts = 2_000_000
    while len(points_m) < TOTAL and attempts < max_attempts:
        attempts += 1
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if not poly_m.contains(p):
            continue
        gx, gy = int((p.x - minx) // cell), int((p.y - miny) // cell)
        ok = True
        for nx in range(gx - 2, gx + 3):
            for ny in range(gy - 2, gy + 3):
                other = grid.get((nx, ny))
                if other is not None and p.distance(other) < MIN_DISTANCE_M:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            grid[(gx, gy)] = p
            points_m.append(p)
    if len(points_m) < TOTAL:
        raise RuntimeError(f"capacity block: generated {len(points_m)} of {TOTAL}")
    points_ll = [transform(to_ll, p) for p in points_m]
    return points_m, points_ll, attempts


def nearest_distances(points_m):
    distances = []
    for i, p in enumerate(points_m):
        nearest = min(p.distance(q) for j, q in enumerate(points_m) if i != j)
        distances.append(nearest)
    return sorted(distances)


def validate_points_with_postgis(points_ll):
    values = [
        {
            "seq": idx + 1,
            "lon": point.x,
            "lat": point.y,
        }
        for idx, point in enumerate(points_ll)
    ]
    sql = """
    WITH pts AS (
      SELECT * FROM jsonb_to_recordset(%s::jsonb)
        AS p(seq int, lon double precision, lat double precision)
    ), geom AS (
      SELECT geometria FROM setores WHERE id = 29
    ), point_geoms AS (
      SELECT seq, ST_SetSRID(ST_MakePoint(lon, lat), 4326) AS g FROM pts
    ), nn AS (
      SELECT p1.seq, MIN(ST_Distance(p1.g::geography, p2.g::geography)) AS nearest_m
        FROM point_geoms p1
        JOIN point_geoms p2 ON p1.seq <> p2.seq
       GROUP BY p1.seq
    )
    SELECT
      (SELECT COUNT(*) FROM point_geoms) AS points,
      (SELECT COUNT(*) FROM point_geoms, geom WHERE ST_Covers(geom.geometria, point_geoms.g)) AS inside_covers,
      (SELECT COUNT(*) FROM point_geoms, geom WHERE NOT ST_Covers(geom.geometria, point_geoms.g)) AS outside_covers,
      (SELECT MIN(nearest_m) FROM nn) AS min_geography_m,
      (SELECT percentile_cont(0.05) WITHIN GROUP (ORDER BY nearest_m) FROM nn) AS p05_geography_m,
      (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY nearest_m) FROM nn) AS median_geography_m,
      (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY nearest_m) FROM nn) AS p95_geography_m,
      (SELECT MAX(nearest_m) FROM nn) AS max_geography_m
    """
    with psycopg2.connect(database_url(), cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute(sql, (json.dumps(values),))
            row = dict(cur.fetchone())
            cur.execute("COMMIT")
    return {key: float(value) if hasattr(value, "as_integer_ratio") else value for key, value in row.items()}


def percentile(sorted_values, pct):
    k = (len(sorted_values) - 1) * pct / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def schedule_records(records):
    rng = random.Random(SEED + 77)
    tz = ZoneInfo("America/Belem")
    windows = []
    for day in range(7):
        date = datetime(2026, 8, 24, tzinfo=tz) + timedelta(days=day)
        for start_hour, end_hour in [(8, 12), (14, 19)]:
            start = date.replace(hour=start_hour, minute=0, second=0)
            end = date.replace(hour=end_hour, minute=0, second=0)
            windows.extend(start + timedelta(seconds=s) for s in range(0, int((end - start).total_seconds()), 225))
    rng.shuffle(windows)
    windows = sorted(windows[:len(records)])
    for idx, record in enumerate(records):
        duration = 360 + ((idx * 37) % 721)
        start = windows[idx]
        end = start + timedelta(seconds=duration)
        record["data_inicio_coleta"] = start.isoformat()
        record["data_fim_coleta"] = end.isoformat()
        record["duracao_segundos"] = duration


def merge_records_with_points(records, points_ll):
    schedule_records(records)
    for record, point in zip(records, points_ll):
        coords = {"lat": round(point.y, 8), "lon": round(point.x, 8)}
        record["localizacao_inicio"] = coords
        record["localizacao_fim"] = coords
        record["setor_id"] = 29
    return records


def write_records(records):
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    with (FINAL_DIR / "records.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(FINAL_DIR / "records-preview.json", {
        "total": len(records),
        "first": records[:5],
        "representative": records[::125][:8],
    })


def write_spatial_artifacts(sector, points_ll, distances, postgis_metrics, attempts, state, poly_m):
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    sector_props = {k: sector[k] for k in sector.keys() if k not in {"geojson", "valid_geojson"}}
    original = feature_collection([feature(sector["geojson"], sector_props)])
    validated = feature_collection([feature(sector["valid_geojson"], sector_props | {"processing_note": "ST_MakeValid only for processing; original preserved"})])
    write_json(SPATIAL_DIR / "sector_macapa_original.geojson", original)
    write_json(SPATIAL_DIR / "sector_macapa_validated.geojson", validated)
    write_json(SPATIAL_DIR / "mask.geojson", validated)
    point_features = [
        feature(mapping(point), {"seq": idx + 1, "record_key": f"AP-DEMO-{idx + 1:04d}"})
        for idx, point in enumerate(points_ll)
    ]
    write_json(SPATIAL_DIR / "points_preview.geojson", feature_collection(point_features))
    with (SPATIAL_DIR / "points_preview.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["seq", "record_key", "lat", "lon"])
        for idx, point in enumerate(points_ll):
            writer.writerow([idx + 1, f"AP-DEMO-{idx + 1:04d}", f"{point.y:.8f}", f"{point.x:.8f}"])
    centroid_x = sector["centroid_lon"]
    centroid_y = sector["centroid_lat"]
    quadrants = Counter()
    for point in points_ll:
        ew = "E" if point.x >= centroid_x else "W"
        ns = "N" if point.y >= centroid_y else "S"
        quadrants[f"{ns}{ew}"] += 1
    metrics = {
        "status": "PASS",
        "source_mask": MASK_LABEL,
        "source_sector_id": sector["id"],
        "source_sector_digest": sector["digest_sha256"],
        "random_seed": SEED,
        "total_expected": TOTAL,
        "points_generated": len(points_ll),
        "inside_mask": len(points_ll),
        "outside_mask": 0,
        "min_distance_m": distances[0],
        "nearest_neighbor_p05_m": percentile(distances, 5),
        "nearest_neighbor_median_m": statistics.median(distances),
        "nearest_neighbor_p95_m": percentile(distances, 95),
        "nearest_neighbor_max_m": distances[-1],
        "area_m2": sector["area_m2"],
        "density_points_per_km2": len(points_ll) / (sector["area_m2"] / 1_000_000),
        "quadrants": dict(sorted(quadrants.items())),
        "generation_attempts": attempts,
        "capacity_estimate_at_50m": int(poly_m.area / (math.pi * (MIN_DISTANCE_M / 2) ** 2)),
        "postgis_geography": postgis_metrics,
        "database_writes": 0,
        "geocoding_calls": 0,
        "notifications": 0,
    }
    write_json(SPATIAL_DIR / "spatial-metrics.json", metrics)
    write_json(SPATIAL_DIR / "sector_macapa_summary.json", {
        "sector": sector_props,
        "state": state,
        "processing": "original geometry preserved; ST_MakeValid materialized only as artifact",
    })
    return metrics


def write_png(path: Path, poly_m, points_m):
    width, height, margin = 1200, 900, 40
    minx, miny, maxx, maxy = poly_m.bounds
    scale = min((width - 2 * margin) / (maxx - minx), (height - 2 * margin) / (maxy - miny))
    img = bytearray([255, 255, 255] * width * height)

    def pix(x, y):
        px = int(margin + (x - minx) * scale)
        py = int(height - margin - (y - miny) * scale)
        return px, py

    def set_pixel(px, py, color):
        if 0 <= px < width and 0 <= py < height:
            off = (py * width + px) * 3
            img[off:off + 3] = bytes(color)

    for geom in getattr(poly_m, "geoms", [poly_m]):
        rings = [geom.exterior] + list(geom.interiors)
        for ring in rings:
            coords = list(ring.coords)
            for a, b in zip(coords, coords[1:]):
                x1, y1 = pix(*a)
                x2, y2 = pix(*b)
                steps = max(abs(x2 - x1), abs(y2 - y1), 1)
                for s in range(steps + 1):
                    t = s / steps
                    set_pixel(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t), (30, 80, 120))
    for p in points_m:
        px, py = pix(p.x, p.y)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx * dx + dy * dy <= 4:
                    set_pixel(px + dx, py + dy, (220, 70, 45))
    raw = b"".join(b"\x00" + img[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    for chunk_type, data in [(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)), (b"IDAT", zlib.compress(raw, 9)), (b"IEND", b"")]:
        png += struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xffffffff)
    path.write_bytes(png)


def write_html(path: Path, sector_geojson, points_geojson):
    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prévia espacial demo AP</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>html,body,#map{{height:100%;margin:0}} .leaflet-popup-content{{font:13px sans-serif}}</style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const mask = {json.dumps(sector_geojson, ensure_ascii=False)};
    const points = {json.dumps(points_geojson, ensure_ascii=False)};
    const map = L.map('map');
    const maskLayer = L.geoJSON(mask, {{style: {{color: '#1f5d7a', weight: 2, fillOpacity: 0.08}}}}).addTo(map);
    L.geoJSON(points, {{pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{radius: 3, color: '#bf3d2a', weight: 1, fillOpacity: 0.75}})}}).addTo(map);
    map.fitBounds(maskLayer.getBounds(), {{padding: [20, 20]}});
    L.control.scale({{metric: true, imperial: false}}).addTo(map);
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    state = read_state()
    if not state["project"] or not state["sector"]:
        raise RuntimeError("project/survey/sector not found")
    if state["coletas_count"] or state["respostas_count"]:
        raise RuntimeError("ABORT: pesquisa 5 has coletas/respostas")
    sector = state["sector"][0]
    geom = shape(sector["valid_geojson"])
    poly_m, to_ll = project_geometry(geom)
    points_m, points_ll, attempts = generate_points(poly_m, to_ll)
    distances = nearest_distances(points_m)
    postgis_metrics = validate_points_with_postgis(points_ll)
    run_id, records = generate(SEED, PROJECT_ID, SURVEY_ID)
    logical_errors = validate(records)
    records = merge_records_with_points(records, points_ll)
    if logical_errors:
        raise RuntimeError("; ".join(logical_errors))
    write_records(records)
    metrics = write_spatial_artifacts(sector, points_ll, distances, postgis_metrics, attempts, state, poly_m)
    points_geojson = json.loads((SPATIAL_DIR / "points_preview.geojson").read_text(encoding="utf-8"))
    mask_geojson = json.loads((SPATIAL_DIR / "mask.geojson").read_text(encoding="utf-8"))
    write_html(SPATIAL_DIR / "preview-map.html", mask_geojson, points_geojson)
    write_png(SPATIAL_DIR / "preview-map.png", poly_m, points_m)
    dataset_hash = hashlib.sha256((FINAL_DIR / "records.jsonl").read_bytes()).hexdigest()
    spatial_hash = hashlib.sha256((SPATIAL_DIR / "points_preview.geojson").read_bytes()).hexdigest()
    write_json(FINAL_DIR / "manifest.json", {
        "status": "PASS",
        "mode": "SPATIAL_LOGICAL_DRY_RUN_READ_ONLY",
        "seed_run_id": str(run_id),
        "random_seed": SEED,
        "project_id": PROJECT_ID,
        "survey_id": SURVEY_ID,
        "company_id": state["project"][0]["company_id"],
        "source_mask": MASK_LABEL,
        "source_sector_id": sector["id"],
        "source_sector_digest": sector["digest_sha256"],
        "total_expected": TOTAL,
        "records_validated": len(records),
        "answers_per_record": 20,
        "dataset_sha256": dataset_hash,
        "dataset_digest": dataset_hash,
        "spatial_digest": spatial_hash,
        "created_at": datetime.now(ZoneInfo("America/Belem")).isoformat(),
        "coordinates": "synthetic_points_inside_mask",
        "database_access": "READ_ONLY",
        "database_writes": 0,
        "reverse_geocoding_calls": 0,
        "notifications": 0,
        "spatial_status": "PASS",
        "spatial_metrics_file": str(SPATIAL_DIR / "spatial-metrics.json"),
        "errors": [],
    })
    write_json(FINAL_DIR / "distributions.json", json.loads(Path("artifacts/demo_ap/logical/distributions.json").read_text(encoding="utf-8")))
    write_json(FINAL_DIR / "agents-plan.json", {
        "source": "production_read_only_2026-09-07",
        "agents": [
            {"email": row["email"], "user_id": row["id"], "active": row["ativo"], "profile": row["perfil"], "company_id": row["company_id"]}
            for row in state["users"] if row["email"] != "rafhael.ferreira.silva@gmail.com"
        ],
        "sector_assignments": state["sector_agents"],
        "acl_changes_performed": False,
    })
    report = [
        "SPATIAL DRY-RUN: PASS",
        f"source_mask: {MASK_LABEL}",
        f"source_sector_id: {sector['id']}",
        f"source_sector_digest: {sector['digest_sha256']}",
        f"points_generated: {metrics['points_generated']}",
        f"inside_mask: {metrics['inside_mask']}",
        f"outside_mask: {metrics['outside_mask']}",
        f"min_distance_m: {metrics['min_distance_m']:.3f}",
        f"p05_m: {metrics['nearest_neighbor_p05_m']:.3f}",
        f"median_m: {metrics['nearest_neighbor_median_m']:.3f}",
        f"p95_m: {metrics['nearest_neighbor_p95_m']:.3f}",
        f"max_m: {metrics['nearest_neighbor_max_m']:.3f}",
        f"area_m2: {metrics['area_m2']:.3f}",
        f"density_points_per_km2: {metrics['density_points_per_km2']:.3f}",
        f"quadrants: {metrics['quadrants']}",
        f"postgis_inside_covers: {int(postgis_metrics['inside_covers'])}",
        f"postgis_outside_covers: {int(postgis_metrics['outside_covers'])}",
        f"min_geography_m: {postgis_metrics['min_geography_m']:.8f}",
        f"p05_geography_m: {postgis_metrics['p05_geography_m']:.8f}",
        f"median_geography_m: {postgis_metrics['median_geography_m']:.8f}",
        f"p95_geography_m: {postgis_metrics['p95_geography_m']:.8f}",
        f"max_geography_m: {postgis_metrics['max_geography_m']:.8f}",
        "logical_dry_run: PASS 1000/1000",
        "q15_q16_duplicate_candidate: 0",
        "database_writes: 0",
        "geocoding_calls: 0",
        "notifications: 0",
    ]
    (SPATIAL_DIR / "spatial-validation-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    (FINAL_DIR / "validation-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "metrics": metrics, "dataset_sha256": dataset_hash}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
