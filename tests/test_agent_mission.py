import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


os.environ.setdefault("SECRET_KEY", "test-only-agent-mission-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints.agente import get_missao_agente
from pesquisa360.db import models


def _sector(sector_id, name, meta, tolerance, geometry):
    return SimpleNamespace(
        id=sector_id,
        nome=name,
        meta=meta,
        tolerancia=tolerance,
        geometria=geometry,
    )


def _database(sectors, completed=0):
    ordered_sectors = sorted(sectors, key=lambda sector: sector.id)
    db = MagicMock()
    sector_query = MagicMock()
    sector_query.filter.return_value = sector_query
    sector_query.order_by.return_value = sector_query
    sector_query.all.return_value = ordered_sectors

    scalar_values = iter(
        [completed]
        + [
            json.dumps(geometry)
            for geometry in (s.geometria for s in ordered_sectors)
            if geometry
        ]
    )

    def query(entity):
        if entity is models.Setor:
            return sector_query
        scalar_query = MagicMock()
        scalar_query.filter.return_value = scalar_query
        scalar_query.scalar.side_effect = lambda: next(scalar_values)
        return scalar_query

    db.query.side_effect = query
    return db, sector_query


def _mission(sectors, *, completed=0, user_id=7, company_id=10):
    db, sector_query = _database(sectors, completed=completed)
    user = SimpleNamespace(id=user_id, company_id=company_id)
    with patch(
        "pesquisa360.api.endpoints.agente.crud.get_pesquisa",
        return_value=object(),
    ) as get_survey:
        payload = get_missao_agente(
            db=db,
            pesquisa_id=100,
            current_user=user,
        )
    get_survey.assert_called_once_with(
        db=db,
        pesquisa_id=100,
        current_user=user,
    )
    return payload, sector_query


def test_mission_returns_all_agent_sectors_in_deterministic_order():
    sede_geometry = {
        "type": "Polygon",
        "coordinates": [[[-49.68, -3.78], [-49.66, -3.78], [-49.67, -3.76]]],
    }
    rural_geometry = {
        "type": "Polygon",
        "coordinates": [[[-49.80, -3.90], [-49.70, -3.90], [-49.75, -3.80]]],
    }
    sectors = [
        _sector(11, "Rural Geral", 18, 150, rural_geometry),
        _sector(10, "Sede", 81, 50, sede_geometry),
    ]

    payload, query = _mission(sectors, completed=3)

    assert [sector["id"] for sector in payload["setores"]] == [10, 11]
    assert [sector["meta"] for sector in payload["setores"]] == [81, 18]
    assert [sector["geometria"] for sector in payload["setores"]] == [
        sede_geometry,
        rural_geometry,
    ]
    assert payload["setor_id"] == 10
    assert payload["setor_nome"] == "Sede"
    assert payload["meta"] == 81
    assert payload["realizado"] == 3
    assert payload["restante"] == 78
    assert payload["meta"] != sum(sector["meta"] for sector in payload["setores"])
    assert json.loads(json.dumps(payload)) == payload

    criteria = " ".join(str(item) for item in query.filter.call_args.args)
    assert "setores.pesquisa_id" in criteria
    assert "setores.agente_id" in criteria
    query.order_by.assert_called_once()


def test_mission_with_one_sector_keeps_legacy_and_list_payloads():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [0, 1]]],
    }
    payload, _ = _mission([_sector(20, "Unico", 12, 25, geometry)])

    assert payload["setor_id"] == 20
    assert payload["geometria"] == geometry
    assert len(payload["setores"]) == 1
    assert payload["setores"][0]["meta"] == 12


def test_mission_without_sector_returns_complete_empty_payload():
    payload, _ = _mission([])

    assert payload == {
        "tem_setor": False,
        "setor_id": None,
        "setor_nome": None,
        "meta": 0,
        "realizado": 0,
        "restante": 0,
        "tolerancia_metros": 0,
        "geometria": None,
        "setores": [],
    }


def test_sector_query_is_scoped_to_current_agent_and_requested_survey():
    _, query = _mission([], user_id=77, company_id=10)
    criteria = query.filter.call_args.args

    assert str(criteria[0].left) == "setores.pesquisa_id"
    assert criteria[0].right.value == 100
    assert str(criteria[1].left) == "setores.agente_id"
    assert criteria[1].right.value == 77
