from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-only-shapefile-import-key")

from pesquisa360.services.setor_shapefile_import import (
    MultipleFeaturesError,
    ShapefileImportError,
    _validated_polygon,
    load_sector_geometry,
    read_source_crs,
    validate_shapefile_components,
)
from pesquisa360.schemas import FinalidadeSetor
from scripts import importar_setor_shapefile as cli


def clockwise_square(min_x: float, min_y: float, size: float = 1.0) -> list[list[float]]:
    return [
        [min_x, min_y],
        [min_x, min_y + size],
        [min_x + size, min_y + size],
        [min_x + size, min_y],
        [min_x, min_y],
    ]


def write_shapefile(
    base: Path,
    features: list[list[list[list[float]]]],
    *,
    crs: CRS = CRS.from_epsg(4326),
) -> Path:
    with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as writer:
        writer.field("id", "N")
        for index, parts in enumerate(features, start=1):
            writer.poly(parts)
            writer.record(index)
    base.with_suffix(".prj").write_text(crs.to_wkt(), encoding="utf-8")
    return base.with_suffix(".shp")


def test_validates_required_component_set(tmp_path: Path):
    shp = tmp_path / "missing.shp"
    shp.touch()
    with pytest.raises(ShapefileImportError, match=".shx"):
        validate_shapefile_components(shp)


def test_reads_prj(tmp_path: Path):
    prj = tmp_path / "sector.prj"
    prj.write_text(CRS.from_epsg(31983).to_wkt(), encoding="utf-8")
    assert read_source_crs(prj).to_epsg() == 31983


def test_reprojects_polygon_to_epsg_4326(tmp_path: Path):
    transformer = Transformer.from_crs(4326, 3857, always_xy=True)
    ring = [list(transformer.transform(lon, lat)) for lon, lat in clockwise_square(-51, -1, 0.01)]
    shp = write_shapefile(tmp_path / "projected", [[ring]], crs=CRS.from_epsg(3857))

    result = load_sector_geometry(shp)

    assert result.polygon.geom_type == "Polygon"
    assert result.polygon.bounds == pytest.approx((-51, -1, -50.99, -0.99), abs=1e-5)


def test_rejects_single_feature_that_is_multipolygon(tmp_path: Path):
    shp = write_shapefile(
        tmp_path / "multi",
        [[clockwise_square(0, 0), clockwise_square(3, 0)]],
    )
    with pytest.raises(ShapefileImportError, match="MultiPolygon"):
        load_sector_geometry(shp)


def test_multiple_features_require_explicit_union(tmp_path: Path):
    shp = write_shapefile(
        tmp_path / "two",
        [[clockwise_square(0, 0)], [clockwise_square(1, 0)]],
    )
    with pytest.raises(MultipleFeaturesError) as exc:
        load_sector_geometry(shp)
    assert exc.value.feature_count == 2


def test_adjacent_features_union_into_polygon(tmp_path: Path):
    shp = write_shapefile(
        tmp_path / "adjacent",
        [[clockwise_square(0, 0)], [clockwise_square(1, 0)]],
    )
    result = load_sector_geometry(shp, merge_features=True)
    assert result.polygon.geom_type == "Polygon"
    assert result.feature_count == 2
    assert result.features_merged is True


def test_disconnected_features_union_into_multipolygon_and_are_rejected(tmp_path: Path):
    shp = write_shapefile(
        tmp_path / "disconnected",
        [[clockwise_square(0, 0)], [clockwise_square(3, 0)]],
    )
    with pytest.raises(ShapefileImportError, match="MultiPolygon"):
        load_sector_geometry(shp, merge_features=True)


def test_invalid_geometry_is_not_silently_reduced():
    bow_tie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    with pytest.raises(ShapefileImportError, match="MultiPolygon"):
        _validated_polygon(bow_tie, "Teste")


def test_explicit_multipolygon_is_rejected():
    geometry = MultiPolygon((Polygon(clockwise_square(0, 0)), Polygon(clockwise_square(3, 0))))
    with pytest.raises(ShapefileImportError, match="MultiPolygon"):
        _validated_polygon(geometry, "Teste")


@pytest.mark.parametrize("value", (0, -1, "x", ""))
def test_meta_must_be_positive(value):
    with pytest.raises(ValueError):
        cli.validate_meta(value)


@pytest.mark.parametrize("value", (-1, "x", ""))
def test_tolerance_must_be_non_negative(value):
    with pytest.raises(ValueError):
        cli.validate_tolerance(value)
    assert cli.validate_tolerance(0) == 0


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, cnpj TEXT, logo_url TEXT,
                is_active BOOLEAN, created_at DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT)
        """))
        connection.execute(text("""
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT,
                ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE projetos (
                id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT,
                data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE pesquisas (
                id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN,
                projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE setores (
                id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER,
                finalidade TEXT DEFAULT 'OPERACAO' NOT NULL,
                geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER
            )
        """))
        connection.execute(text("INSERT INTO companies VALUES (10, 'A', NULL, NULL, 1, NULL), (20, 'B', NULL, NULL, 1, NULL)"))
        connection.execute(text("INSERT INTO perfis VALUES (1, 'Gerente', NULL), (2, 'Agente', NULL), (3, 'Outro', NULL)"))
        connection.execute(text("""
            INSERT INTO usuarios VALUES
              (1, 'manager@a', 'Manager A', 'x', 1, 1, 10),
              (2, 'agent@a', 'Agent A', 'x', 1, 2, 10),
              (3, 'agent@b', 'Agent B', 'x', 1, 2, 20),
              (4, 'inactive@a', 'Inactive', 'x', 0, 2, 10),
              (5, 'other@a', 'Other', 'x', 1, 3, 10)
        """))
        connection.execute(text("""
            INSERT INTO projetos VALUES
              (100, 'Project A', NULL, 'Ativo', NULL, NULL, 1, 10),
              (200, 'Project B', NULL, 'Ativo', NULL, NULL, 1, 20)
        """))
        connection.execute(text("""
            INSERT INTO pesquisas VALUES
              (1000, 'Survey A', NULL, 1, 100, NULL, NULL),
              (2000, 'Survey B', NULL, 1, 200, NULL, NULL),
              (1001, 'Inactive', NULL, 0, 100, NULL, NULL)
        """))
        connection.execute(text("INSERT INTO setores VALUES (1, '  Centro   Norte ', 1, 0, 'OPERACAO', NULL, 1000, 2)"))
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_validates_tenant_chain_and_admin_profile(db):
    context = cli.validate_import_context(db, user_id=1, project_id=100, survey_id=1000, agent_id=2)
    assert context.current_user.company_id == 10

    cases = (
        ({"user_id": 5, "project_id": 100, "survey_id": 1000, "agent_id": 2}, "inelegivel"),
        ({"user_id": 1, "project_id": 200, "survey_id": 2000, "agent_id": 2}, "Projeto"),
        ({"user_id": 1, "project_id": 100, "survey_id": 2000, "agent_id": 2}, "Pesquisa"),
        ({"user_id": 1, "project_id": 100, "survey_id": 1001, "agent_id": 2}, "Pesquisa"),
        ({"user_id": 1, "project_id": 100, "survey_id": 1000, "agent_id": 3}, "Agente"),
        ({"user_id": 1, "project_id": 100, "survey_id": 1000, "agent_id": 4}, "Agente"),
    )
    for kwargs, message in cases:
        with pytest.raises(ValueError, match=message):
            cli.validate_import_context(db, **kwargs)


def test_duplicate_name_uses_documented_normalization(db):
    with pytest.raises(ValueError, match="mesmo nome"):
        cli.ensure_name_available(
            db, survey_id=1000, name="centro norte", allow_duplicate=False
        )
    cli.ensure_name_available(db, survey_id=1000, name="centro norte", allow_duplicate=True)


def test_summary_uses_real_persistence_names(db):
    context = cli.validate_import_context(db, user_id=1, project_id=100, survey_id=1000, agent_id=2)
    geometry = Mock(
        polygon=Polygon(clockwise_square(-51, -1)),
        source_crs="EPSG:4326",
        feature_count=1,
        features_merged=False,
    )
    summary = cli.build_summary(
        context,
        geometry,
        file_path=Path("sector.shp"),
        name="Centro",
        finalidade=FinalidadeSetor.OPERACAO,
        meta=10,
        tolerance=20,
    )
    assert "Finalidade: OPERACAO" in summary
    assert "Cota/meta: 10" in summary
    assert "Tolerancia: 20 m" in summary
    assert "Polygon EPSG:4326" in summary


def test_summary_for_relatorio_omits_operational_fields(db):
    context = cli.validate_import_context(
        db,
        user_id=1,
        project_id=100,
        survey_id=1000,
        agent_id=None,
        finalidade=FinalidadeSetor.RELATORIO,
    )
    geometry = Mock(
        polygon=Polygon(clockwise_square(-51, -1)),
        source_crs="EPSG:4326",
        feature_count=1,
        features_merged=False,
    )
    summary = cli.build_summary(
        context,
        geometry,
        file_path=Path("sector.shp"),
        name="Alta Floresta",
        finalidade=FinalidadeSetor.RELATORIO,
        meta=None,
        tolerance=None,
    )
    assert "Finalidade: RELATORIO" in summary
    assert "Agente:" not in summary
    assert "Cota/meta:" not in summary
    assert "Tolerancia:" not in summary


def test_dry_run_does_not_call_create_or_commit(db, tmp_path: Path):
    shp = write_shapefile(tmp_path / "dry", [[clockwise_square(-51, -1)]])
    args = argparse.Namespace(
        usuario_id=1,
        projeto_id=100,
        pesquisa_id=1000,
        agente_id=2,
        arquivo=shp,
        nome="Novo setor",
        meta="10",
        tolerancia="5",
        finalidade="OPERACAO",
        unir_feicoes=False,
        dry_run=True,
        permitir_nome_duplicado=False,
        sim=False,
    )
    with patch.object(cli, "persist_sector") as create_setor, patch.object(db, "commit") as commit:
        assert cli.run(args, db) == 0
    create_setor.assert_not_called()
    commit.assert_not_called()


def test_dry_run_relatorio_without_agent_or_operational_fields(db, tmp_path: Path):
    shp = write_shapefile(tmp_path / "relatorio-dry", [[clockwise_square(-51, -1)]])
    args = argparse.Namespace(
        usuario_id=1,
        projeto_id=100,
        pesquisa_id=1000,
        agente_id=None,
        arquivo=shp,
        nome="Alta Floresta",
        meta=None,
        tolerancia=None,
        finalidade="RELATORIO",
        unir_feicoes=False,
        dry_run=True,
        permitir_nome_duplicado=False,
        sim=False,
    )
    with patch.object(cli, "persist_sector") as create_setor, patch.object(db, "commit") as commit:
        assert cli.run(args, db) == 0
    create_setor.assert_not_called()
    commit.assert_not_called()


def test_persist_sector_records_finalidade_and_optional_agent(db):
    context = cli.validate_import_context(
        db,
        user_id=1,
        project_id=100,
        survey_id=1000,
        agent_id=None,
        finalidade=FinalidadeSetor.RELATORIO,
    )
    geometry = Mock(coordinates_lat_lon=[[0, -51], [0, -50], [1, -50]])
    with patch("pesquisa360.crud.create_setor") as create_setor:
        cli.persist_sector(
            db,
            context,
            geometry,
            name="Sagrado Coracao",
            finalidade=FinalidadeSetor.RELATORIO,
            meta=0,
            tolerance=0,
        )
    setor_in = create_setor.call_args.args[1]
    assert setor_in.finalidade == FinalidadeSetor.RELATORIO
    assert setor_in.agente_id is None
    assert setor_in.meta == 0
    assert setor_in.tolerancia == 0


def test_run_persists_ambos_with_agent(db, tmp_path: Path):
    shp = write_shapefile(tmp_path / "ambos", [[clockwise_square(-51, -1)]])
    args = argparse.Namespace(
        usuario_id=1,
        projeto_id=100,
        pesquisa_id=1000,
        agente_id=2,
        arquivo=shp,
        nome="Setor Ambos",
        meta="12",
        tolerancia="7",
        finalidade="AMBOS",
        unir_feicoes=False,
        dry_run=False,
        permitir_nome_duplicado=False,
        sim=True,
    )
    with patch.object(cli, "persist_sector") as create_setor:
        create_setor.return_value = Mock(id=99)
        assert cli.run(args, db) == 0
    assert create_setor.call_args.kwargs["finalidade"] == FinalidadeSetor.AMBOS
    assert create_setor.call_args.kwargs["meta"] == 12
    assert create_setor.call_args.kwargs["tolerance"] == 7


def test_invalid_finalidade_aborts_before_persisting(db, tmp_path: Path):
    shp = write_shapefile(tmp_path / "invalid-purpose", [[clockwise_square(-51, -1)]])
    args = argparse.Namespace(
        usuario_id=1,
        projeto_id=100,
        pesquisa_id=1000,
        agente_id=None,
        arquivo=shp,
        nome="Invalido",
        meta=None,
        tolerancia=None,
        finalidade="ANALITICO",
        unir_feicoes=False,
        dry_run=False,
        permitir_nome_duplicado=False,
        sim=True,
    )
    with patch.object(cli, "persist_sector") as create_setor:
        with pytest.raises(ValueError, match="Finalidade invalida"):
            cli.run(args, db)
    create_setor.assert_not_called()


def test_main_rolls_back_and_closes_on_unexpected_error():
    fake_db = Mock()
    with patch("pesquisa360.db.session.SessionLocal", return_value=fake_db), patch.object(
        cli, "run", side_effect=RuntimeError("boom")
    ):
        assert cli.main([]) == 1
    fake_db.rollback.assert_called_once()
    fake_db.close.assert_called_once()
