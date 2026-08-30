import json
import os
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("SECRET_KEY", "test-only-agent-mission-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy.sql.functions import Function

from pesquisa360 import crud
from pesquisa360.api.endpoints.agente import get_missao_agente
from pesquisa360.db import models
from pesquisa360.services import pergunta_territorio


MISSION_FINALIDADES = {"OPERACAO", "AMBOS"}


def _sector(
    sector_id,
    name,
    meta,
    tolerance,
    geometry,
    finalidade="OPERACAO",
    municipio=None,
):
    """Setor da fixture. `municipio` = (id, nome) persistido em
    `setores.municipio_territorio_id` (ADR-035); None = sem referencia."""
    return SimpleNamespace(
        id=sector_id,
        nome=name,
        meta=meta,
        tolerancia=tolerance,
        geometria=geometry,
        finalidade=finalidade,
        municipio_territorio_id=municipio[0] if municipio else None,
        municipio=municipio,
    )


def _pergunta(pergunta_id, ordem, aplicabilidade="GLOBAL", municipio_ids=()):
    return SimpleNamespace(
        id=pergunta_id,
        ordem=ordem,
        aplicabilidade=aplicabilidade,
        municipio_ids=tuple(municipio_ids),
    )


def _column_key(column):
    """'tabela.coluna' de uma coluna ORM/Core, para reconhecer a consulta.

    Colunas com `.label(...)` sao desembrulhadas ate a coluna real; alias de
    tabela (ex.: `municipio`) responde com o nome do alias."""
    while getattr(column, "element", None) is not None and getattr(column, "table", None) is None:
        column = column.element
    table = getattr(column, "table", None)
    key = getattr(column, "key", None) or getattr(column, "name", None)
    if table is not None and key is not None:
        return f"{table.name}.{key}"
    return None


def _entities_key(entities):
    keys = []
    for entity in entities:
        if isinstance(entity, type):
            keys.append(entity.__tablename__)
        else:
            keys.append(_column_key(entity))
    return tuple(keys)


def _filter_values(criteria, column_key):
    """Valores literais comparados (==) com `column_key` nos criterios."""
    values = []
    for criterion in criteria:
        left = getattr(criterion, "left", None)
        right = getattr(criterion, "right", None)
        if left is not None and _column_key(left) == column_key and hasattr(right, "value"):
            values.append(right.value)
    return values


def _in_values(criteria, column_key):
    """Valores do `column IN (...)` presente nos criterios."""
    for criterion in criteria:
        left = getattr(criterion, "left", None)
        right = getattr(criterion, "right", None)
        if left is None or _column_key(left) != column_key or right is None:
            continue
        value = getattr(right, "value", None)
        if isinstance(value, (list, tuple, set)):
            return set(value)
        clauses = getattr(right, "clauses", None)
        if clauses is not None:
            return {clause.value for clause in clauses}
    raise AssertionError(f"consulta sem filtro IN em {column_key}")


class _ChainQuery:
    """Query encadeavel (join/filter/order_by/...) que registra as chamadas
    e delega o resultado final a `resolve(query)`. Cada consulta real da
    missao recebe seu proprio resolvedor, escolhido pelas entidades pedidas."""

    def __init__(self, entities, resolve):
        self.entities = entities
        self._resolve = resolve
        self.filters = []
        self.order_by_calls = []
        self.join_calls = []
        self.outerjoin_calls = []
        self.distinct_calls = 0
        self.group_by_calls = []

    def filter(self, *criteria):
        self.filters.append(criteria)
        return self

    def join(self, *args, **kwargs):
        self.join_calls.append((args, kwargs))
        return self

    def outerjoin(self, *args, **kwargs):
        self.outerjoin_calls.append((args, kwargs))
        return self

    def distinct(self):
        self.distinct_calls += 1
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def group_by(self, *args):
        self.group_by_calls.append(args)
        return self

    @property
    def criteria(self):
        return [item for group in self.filters for item in group]

    def all(self):
        return list(self._resolve(self))

    def first(self):
        rows = list(self._resolve(self))
        return rows[0] if rows else None

    def scalar(self):
        rows = list(self._resolve(self))
        return rows[0] if rows else None


class _FakeMissionDatabase:
    """Banco falso que reconhece as consultas do contrato FASE F pelas
    entidades/colunas solicitadas e pelos filtros relevantes -- nao pela
    ordem interna das chamadas. Uma consulta fora do contrato falha alto."""

    SETOR = ("setores",)
    MUNICIPIO_PERSISTIDO = (
        "setores.id",
        "territorio_eleitoral.id",
        "territorio_eleitoral.nome",
    )
    COMPOSICAO = (
        "setor_territorio_eleitoral.setor_id",
        "territorio_eleitoral.id",
        "municipio.id",
        "municipio.nome",
    )
    PERGUNTAS = ("perguntas.id", "perguntas.aplicabilidade")
    PERGUNTA_ID = ("perguntas.id",)
    PERGUNTA_MUNICIPIOS = (
        "pergunta_territorio_eleitoral.pergunta_id",
        "pergunta_territorio_eleitoral.territorio_eleitoral_id",
    )
    PLANO_COTA_PERFIL = ("planos_cota_perfil",)

    def __init__(self, sectors, perguntas=(), composicao=()):
        self.sectors = sorted(sectors, key=lambda sector: sector.id)
        self.perguntas = sorted(perguntas, key=lambda p: (p.ordem, p.id))
        # (setor_id, bairro_id, municipio_id, municipio_nome)
        self.composicao = list(composicao)
        self.queries = []
        self.sector_query = None

    # -- SQLAlchemy Session API usada pela missao -------------------------
    def query(self, *entities):
        if len(entities) == 1 and isinstance(entities[0], Function):
            query = _ChainQuery(entities, self._resolve_geojson)
        else:
            key = _entities_key(entities)
            resolvers = {
                self.SETOR: self._resolve_setores,
                self.MUNICIPIO_PERSISTIDO: self._resolve_municipio_persistido,
                self.COMPOSICAO: self._resolve_composicao,
                self.PERGUNTAS: self._resolve_perguntas,
                self.PERGUNTA_ID: self._resolve_pergunta_id,
                self.PERGUNTA_MUNICIPIOS: self._resolve_pergunta_municipios,
                self.PLANO_COTA_PERFIL: self._resolve_plano_cota_perfil,
            }
            if key not in resolvers:
                raise AssertionError(f"Consulta fora do contrato da missao: {key}")
            query = _ChainQuery(entities, resolvers[key])
            if key == self.SETOR:
                self.sector_query = query
        self.queries.append(query)
        return query

    # -- resolvedores ------------------------------------------------------
    def _resolve_setores(self, query):
        """Setores do agente na pesquisa: OPERACAO/AMBOS, Setor.id ASC."""
        [pesquisa_id] = _filter_values(query.criteria, "setores.pesquisa_id")
        assert pesquisa_id == 100
        assert query.distinct_calls == 1
        assert query.order_by_calls, "missao deve ordenar de forma deterministica"
        return [
            sector
            for sector in self.sectors
            if sector.finalidade in MISSION_FINALIDADES
        ]

    def _resolve_municipio_persistido(self, query):
        """query(Setor.id, Municipio.id, Municipio.nome) da FASE F/ADR-035."""
        ids = _in_values(query.criteria, "setores.id")
        assert query.join_calls, "resolucao persistida exige JOIN no municipio"
        return [
            (sector.id, sector.municipio[0], sector.municipio[1])
            for sector in self.sectors
            if sector.id in ids and sector.municipio is not None
        ]

    def _resolve_composicao(self, query):
        ids = _in_values(query.criteria, "setor_territorio_eleitoral.setor_id")
        return [
            SimpleNamespace(
                setor_id=setor_id,
                bairro_id=bairro_id,
                municipio_id=municipio_id,
                municipio_nome=municipio_nome,
            )
            for setor_id, bairro_id, municipio_id, municipio_nome in self.composicao
            if setor_id in ids
        ]

    def _resolve_perguntas(self, query):
        [pesquisa_id] = _filter_values(query.criteria, "perguntas.pesquisa_id")
        assert pesquisa_id == 100
        return [SimpleNamespace(id=p.id, aplicabilidade=p.aplicabilidade) for p in self.perguntas]

    def _resolve_pergunta_id(self, query):
        [pesquisa_id] = _filter_values(query.criteria, "perguntas.pesquisa_id")
        assert pesquisa_id == 100
        [aplicabilidade] = _filter_values(query.criteria, "perguntas.aplicabilidade")
        return [(p.id,) for p in self.perguntas if p.aplicabilidade == aplicabilidade]

    def _resolve_pergunta_municipios(self, query):
        return [
            (p.id, municipio_id)
            for p in self.perguntas
            for municipio_id in p.municipio_ids
        ]

    def _resolve_plano_cota_perfil(self, query):
        return []  # sem plano de cota de perfil ativo nesta fixture

    def _resolve_geojson(self, query):
        [expression] = query.entities
        assert expression.name == "ST_AsGeoJSON"
        [geometry] = [clause.value for clause in expression.clauses]
        return [json.dumps(geometry)]


def _progressos(sectors, completed):
    return {
        sector.id: crud.calcular_cota_territorial(
            meta=sector.meta,
            realizado=completed,
            snapshot_ate_coleta_id=None,
            snapshot_em=None,
            agentes_atribuidos_total=1,
        )
        for sector in sectors
        if sector.finalidade in MISSION_FINALIDADES
    }


def _mission(
    sectors,
    *,
    completed=0,
    user_id=7,
    company_id=10,
    perguntas=(),
    composicao=(),
):
    db = _FakeMissionDatabase(sectors, perguntas=perguntas, composicao=composicao)
    user = SimpleNamespace(id=user_id, company_id=company_id)
    with patch(
        "pesquisa360.api.endpoints.agente.crud.get_pesquisa",
        return_value=object(),
    ) as get_survey, patch(
        "pesquisa360.api.endpoints.agente.crud.obter_progressos_setores",
        return_value=_progressos(sectors, completed),
    ):
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
    return payload, db.sector_query


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
        _sector(10, "Sede", 81, 50, sede_geometry, municipio=(500, "Tucurui")),
    ]
    perguntas = [
        _pergunta(1, 1, "GLOBAL"),
        _pergunta(2, 2, "TERRITORIAL", municipio_ids=[500]),
        _pergunta(3, 3, "GLOBAL"),
    ]

    payload, query = _mission(sectors, completed=3, perguntas=perguntas)

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

    # FASE F: resolucao territorial em lote, municipio e aplicabilidade por setor.
    sede, rural = payload["setores"]
    assert sede["territorio_status"] == pergunta_territorio.STATUS_RESOLVIDO
    assert sede["municipio"] == {"id": 500, "nome": "Tucurui"}
    assert sede["pergunta_ids_aplicaveis"] == [1, 2, 3]
    assert rural["territorio_status"] == pergunta_territorio.STATUS_SEM_MUNICIPIO
    assert rural["municipio"] is None
    assert rural["pergunta_ids_aplicaveis"] == [1, 3]
    assert payload["possui_perguntas_territoriais"] is True

    criteria = " ".join(str(item) for item in query.criteria)
    assert "setores.pesquisa_id" in criteria
    assert "setor_agentes.id" in criteria
    assert "setores.agente_id" in criteria
    assert "setores.finalidade" in criteria
    assert len(query.order_by_calls) == 1
    assert str(query.order_by_calls[0][0]) == "setores.id ASC"


def test_mission_with_one_sector_keeps_legacy_and_list_payloads():
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [0, 1]]],
    }
    perguntas = [
        _pergunta(1, 1, "GLOBAL"),
        _pergunta(2, 2, "TERRITORIAL", municipio_ids=[600]),
        _pergunta(4, 3, "TERRITORIAL", municipio_ids=[999]),
    ]
    payload, _ = _mission(
        [_sector(20, "Unico", 12, 25, geometry, municipio=(600, "Cameta"))],
        perguntas=perguntas,
    )

    # Compatibilidade legada (campos de topo refletem o unico setor).
    assert payload["tem_setor"] is True
    assert payload["setor_id"] == 20
    assert payload["setor_nome"] == "Unico"
    assert payload["meta"] == 12
    assert payload["tolerancia_metros"] == 25
    assert payload["geometria"] == geometry
    # Lista com exatamente 1 item, coerente com o legado.
    assert len(payload["setores"]) == 1
    unico = payload["setores"][0]
    assert unico["id"] == 20
    assert unico["meta"] == 12
    assert unico["geometria"] == geometry
    # Resolucao territorial + municipio + aplicabilidade do setor.
    assert unico["territorio_status"] == pergunta_territorio.STATUS_RESOLVIDO
    assert unico["municipio"] == {"id": 600, "nome": "Cameta"}
    assert unico["pergunta_ids_aplicaveis"] == [1, 2]
    assert payload["possui_perguntas_territoriais"] is True


def test_mission_with_one_sector_without_territorial_questions():
    payload, _ = _mission(
        [_sector(21, "Unico", 5, 25, None)],
        perguntas=[_pergunta(1, 1, "GLOBAL")],
    )

    assert payload["setor_id"] == 21
    assert payload["setores"][0]["territorio_status"] == pergunta_territorio.STATUS_SEM_MUNICIPIO
    assert payload["setores"][0]["municipio"] is None
    assert payload["setores"][0]["pergunta_ids_aplicaveis"] == [1]
    assert payload["possui_perguntas_territoriais"] is False


def test_mission_without_sector_returns_complete_empty_payload():
    payload, _ = _mission([])

    assert payload == {
        "tem_setor": False,
        "setor_id": None,
        "setor_nome": None,
        "meta": 0,
        "realizado": 0,
        "restante": 0,
        "excedente": 0,
        "percentual_atingimento": None,
        "cota_atingida": None,
        "tolerancia_metros": 0,
        "geometria": None,
        "setores": [],
        "possui_perguntas_territoriais": False,
    }


def test_sector_query_is_scoped_to_current_agent_and_requested_survey():
    _, query = _mission([], user_id=77, company_id=10)
    criteria = query.criteria

    assert str(criteria[0].left) == "setores.pesquisa_id"
    assert criteria[0].right.value == 100
    assert "setor_agentes.id" in str(criteria[1])
    assert "setores.agente_id" in str(criteria[1])


def test_mission_returns_operacao_and_ambos_but_not_relatorio():
    sectors = [
        _sector(30, "Operacao", 10, 50, None, "OPERACAO"),
        _sector(31, "Relatorio", 20, 50, None, "RELATORIO"),
        _sector(32, "Ambos", 30, 50, None, "AMBOS"),
    ]

    payload, _ = _mission(sectors)

    assert [sector["id"] for sector in payload["setores"]] == [30, 32]
    assert len({sector["id"] for sector in payload["setores"]}) == 2
    assert payload["setor_id"] == 30
    assert all(sector["id"] != 31 for sector in payload["setores"])
