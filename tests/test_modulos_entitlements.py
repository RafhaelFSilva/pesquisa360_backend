"""Sprint 0: catalogo modular, entitlements e isolamento de tenant."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-modulos-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("UPLOAD_DIRECTORY", str(Path(tempfile.gettempdir()) / "pesquisa360-test-uploads"))
os.environ.setdefault("UPLOAD_MAX_SIZE_BYTES", "1048576")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://testserver")

from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.main import app
from pesquisa360.services import modulos


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _sqlite(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        for name, arguments in (
            ("RecoverGeometryColumn", 5), ("CreateSpatialIndex", 2),
            ("CheckSpatialIndex", 2), ("DisableSpatialIndex", 2),
            ("DiscardGeometryColumn", 2),
        ):
            dbapi_connection.create_function(name, arguments, lambda *_args: 1)
        dbapi_connection.create_function("GeomFromEWKT", 1, lambda value: value)
        dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda value: value)
        dbapi_connection.create_function("AsEWKB", 1, lambda value: value)

    tables = [
        models.Company.__table__, models.Perfil.__table__, models.Usuario.__table__,
        models.Projeto.__table__, models.Pesquisa.__table__, models.Modulo.__table__,
        models.ModuloFuncionalidade.__table__, models.ModuloEntitlement.__table__,
        models.ModuloEntitlementFuncionalidade.__table__,
    ]
    models.Base.metadata.create_all(engine, tables=tables)
    session = sessionmaker(bind=engine)()
    session.add_all([
        models.Company(id=10, name="Empresa A", is_active=True),
        models.Company(id=20, name="Empresa B", is_active=True),
        models.Perfil(id=1, nome="Gerente"),
    ])
    session.flush()
    session.add_all([
        models.Usuario(id=1, email="a@example.com", nome="A", senha_hash="x", ativo=True, perfil_id=1, company_id=10),
        models.Usuario(id=2, email="b@example.com", nome="B", senha_hash="x", ativo=True, perfil_id=1, company_id=20),
    ])
    session.flush()
    session.add_all([
        models.Projeto(id=101, nome="A1", coordenador_id=1, company_id=10),
        models.Projeto(id=102, nome="A2", coordenador_id=1, company_id=10),
        models.Projeto(id=201, nome="B1", coordenador_id=2, company_id=20),
    ])
    session.flush()
    session.add_all([
        models.Pesquisa(id=1001, titulo="A1 P1", projeto_id=101),
        models.Pesquisa(id=1002, titulo="A1 P2", projeto_id=101),
        models.Pesquisa(id=2001, titulo="B1 P1", projeto_id=201),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def catalogo(db):
    modulo = models.Modulo(chave="inteligencia_eleitoral", nome="Inteligencia Eleitoral")
    feature = models.ModuloFuncionalidade(
        modulo=modulo, chave="painel_eleitoral", nome="Painel Eleitoral", ativo=True
    )
    db.add_all([modulo, feature])
    db.flush()
    return modulo, feature


def conceder(db, modulo, feature=None, *, company_id=10, projeto_id=None, pesquisa_id=None,
             status="ATIVO", inicia_em=None, expira_em=None):
    entitlement = modulos.criar_entitlement(
        db, company_id, modulo.id, projeto_id=projeto_id, pesquisa_id=pesquisa_id,
        status=status, inicia_em=inicia_em, expira_em=expira_em,
    )
    db.add(entitlement)
    db.flush()
    if feature is not None:
        modulos.vincular_funcionalidade(db, entitlement, feature)
    db.commit()
    return entitlement


def test_t01_catalogo_possui_chave_unica(db):
    db.add_all([models.Modulo(chave="duplicado", nome="A"), models.Modulo(chave="duplicado", nome="B")])
    with pytest.raises(IntegrityError):
        db.commit()


def test_t02_t03_funcionalidade_pertence_ao_modulo_e_nao_duplica(db):
    modulo, feature = catalogo(db)
    db.commit()
    assert feature.modulo_id == modulo.id
    db.add(models.ModuloFuncionalidade(modulo_id=modulo.id, chave=feature.chave, nome="Duplicada"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_t04_t21_t22_entitlement_empresa_e_aditivo_nos_escopos(db):
    modulo, feature = catalogo(db)
    conceder(db, modulo, feature)
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave)
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave, projeto_id=101)
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave, pesquisa_id=1001)


def test_t05_t06_entitlement_projeto_so_vale_no_projeto(db):
    modulo, _ = catalogo(db)
    conceder(db, modulo, projeto_id=101)
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave, projeto_id=101)
    assert not modulos.empresa_tem_modulo(db, 10, modulo.chave, projeto_id=102)


def test_t07_t08_entitlement_pesquisa_so_vale_na_pesquisa(db):
    modulo, _ = catalogo(db)
    conceder(db, modulo, pesquisa_id=1001)
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave, pesquisa_id=1001)
    assert not modulos.empresa_tem_modulo(db, 10, modulo.chave, pesquisa_id=1002)


@pytest.mark.parametrize("escopo", [{"projeto_id": 201}, {"pesquisa_id": 2001}])
def test_t09_t10_recurso_cross_tenant_nao_participa_da_resolucao(db, escopo):
    modulo, _ = catalogo(db)
    conceder(db, modulo)
    with pytest.raises(Exception) as erro:
        modulos.resolver_entitlements(db, 10, **escopo)
    assert erro.value.status_code == 404


@pytest.mark.parametrize(
    "status,inicio,fim,esperado",
    [
        ("SUSPENSO", None, None, False),
        ("ATIVO", timedelta(hours=1), None, False),
        ("ATIVO", None, timedelta(hours=-1), False),
        ("ATIVO", timedelta(hours=-1), timedelta(hours=1), True),
    ],
)
def test_t11_t14_status_e_validade_temporal(db, status, inicio, fim, esperado):
    agora = datetime.now(timezone.utc)
    modulo, _ = catalogo(db)
    conceder(
        db, modulo, status=status,
        inicia_em=agora + inicio if inicio else None,
        expira_em=agora + fim if fim else None,
    )
    assert modulos.empresa_tem_modulo(db, 10, modulo.chave, agora=agora) is esperado


def test_t15_feature_nao_vinculada_nao_e_concedida(db):
    modulo, feature = catalogo(db)
    conceder(db, modulo)
    assert not modulos.empresa_tem_funcionalidade(db, 10, modulo.chave, feature.chave)


def test_t16_feature_de_outro_modulo_e_recusada(db):
    modulo, _ = catalogo(db)
    outro = models.Modulo(chave="outro", nome="Outro")
    feature = models.ModuloFuncionalidade(modulo=outro, chave="f", nome="F")
    db.add_all([outro, feature])
    db.flush()
    entitlement = models.ModuloEntitlement(company_id=10, modulo_id=modulo.id)
    db.add(entitlement)
    db.flush()
    with pytest.raises(ValueError):
        modulos.vincular_funcionalidade(db, entitlement, feature)


def test_integridade_de_criacao_recusa_escopo_de_outro_tenant(db):
    modulo, _ = catalogo(db)
    with pytest.raises(Exception) as erro:
        modulos.criar_entitlement(db, 10, modulo.id, projeto_id=201)
    assert erro.value.status_code == 404


def cliente(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_t17_t18_endpoint_usa_tenant_do_usuario_e_nao_vaza_outro(db):
    modulo, feature = catalogo(db)
    conceder(db, modulo, feature, company_id=10)
    outro = models.Modulo(chave="modulo_b", nome="Modulo B")
    db.add(outro)
    db.flush()
    conceder(db, outro, company_id=20)
    try:
        corpo = cliente(db, SimpleNamespace(company_id=10)).get("/usuarios/me/modulos/").json()
    finally:
        app.dependency_overrides.clear()
    assert [item["chave"] for item in corpo["modulos"]] == ["inteligencia_eleitoral"]


def test_t19_tenant_sem_entitlement_recebe_lista_vazia(db):
    try:
        response = cliente(db, SimpleNamespace(company_id=10)).get("/usuarios/me/modulos/")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"modulos": []}


def test_t20_company_id_da_query_nao_troca_tenant(db):
    modulo, _ = catalogo(db)
    conceder(db, modulo, company_id=20)
    try:
        response = cliente(db, SimpleNamespace(company_id=10)).get("/usuarios/me/modulos/?company_id=20")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"modulos": []}


def test_t23_rotas_existentes_nao_receberam_gating():
    projetos = [route for route in app.routes if getattr(route, "path", None) == "/projetos/"]
    assert projetos
    assert all("modulo" not in repr(route.dependant.dependencies).lower() for route in projetos)
