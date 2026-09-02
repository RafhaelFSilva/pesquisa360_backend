"""Prompt 02: prova HTTP dos gates comerciais reutilizaveis."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-module-gates-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("UPLOAD_DIRECTORY", str(Path(tempfile.gettempdir()) / "pesquisa360-test-uploads"))
os.environ.setdefault("UPLOAD_MAX_SIZE_BYTES", "1048576")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://testserver")

from pesquisa360.api.dependencies.modulos import (
    get_project_entitlement_context,
    get_survey_entitlement_context,
    require_feature,
    require_module,
)
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import modulos


MODULO = "modulo_teste"
FEATURE = "feature_teste"


@pytest.fixture
def ambiente():
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
        models.UsuarioEmpresaAcesso.__table__, models.Projeto.__table__,
        models.UsuarioProjetoAcesso.__table__, models.Pesquisa.__table__,
        models.Modulo.__table__, models.ModuloFuncionalidade.__table__,
        models.ModuloEntitlement.__table__,
        models.ModuloEntitlementFuncionalidade.__table__,
    ]
    models.Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine)()
    db.add_all([
        models.Company(id=10, name="Empresa A", is_active=True),
        models.Company(id=20, name="Empresa B", is_active=True),
        models.Perfil(id=1, nome="Gerente"),
    ])
    db.flush()
    user_a = models.Usuario(
        id=1, email="a@gates.test", nome="A", senha_hash="x", ativo=True,
        perfil_id=1, company_id=10,
    )
    user_b = models.Usuario(
        id=2, email="b@gates.test", nome="B", senha_hash="x", ativo=True,
        perfil_id=1, company_id=20,
    )
    db.add_all([user_a, user_b])
    db.flush()
    db.add_all([
        models.UsuarioEmpresaAcesso(
            usuario_id=1, company_id=10, acesso_todos_projetos=True, ativo=True, principal=True,
        ),
        models.UsuarioEmpresaAcesso(
            usuario_id=2, company_id=20, acesso_todos_projetos=True, ativo=True, principal=True,
        ),
        models.Projeto(id=101, nome="A1", coordenador_id=1, company_id=10),
        models.Projeto(id=102, nome="A2", coordenador_id=1, company_id=10),
        models.Projeto(id=201, nome="B1", coordenador_id=2, company_id=20),
    ])
    db.flush()
    db.add_all([
        models.Pesquisa(id=1001, titulo="A1 P1", projeto_id=101),
        models.Pesquisa(id=1002, titulo="A2 P1", projeto_id=102),
        models.Pesquisa(id=2001, titulo="B1 P1", projeto_id=201),
    ])
    catalogo = models.Modulo(chave=MODULO, nome="Modulo Teste", ativo=True)
    feature = models.ModuloFuncionalidade(
        modulo=catalogo, chave=FEATURE, nome="Feature Teste", ativo=True,
    )
    db.add_all([catalogo, feature])
    db.commit()

    test_app = FastAPI()

    @test_app.get("/_test/module")
    def module_gate(_=Depends(require_module(MODULO))):
        return {"ok": True}

    @test_app.get("/_test/feature")
    def feature_gate(_=Depends(require_feature(MODULO, FEATURE))):
        return {"ok": True}

    @test_app.get("/_test/project/{projeto_id}/module")
    def project_module(_=Depends(require_module(MODULO, get_project_entitlement_context))):
        return {"ok": True}

    @test_app.get("/_test/project/{projeto_id}/feature")
    def project_feature(_=Depends(require_feature(MODULO, FEATURE, get_project_entitlement_context))):
        return {"ok": True}

    @test_app.get("/_test/survey/{pesquisa_id}/module")
    def survey_module(_=Depends(require_module(MODULO, get_survey_entitlement_context))):
        return {"ok": True}

    @test_app.get("/_test/survey/{pesquisa_id}/feature")
    def survey_feature(_=Depends(require_feature(MODULO, FEATURE, get_survey_entitlement_context))):
        return {"ok": True}

    current = {"user": user_a}
    test_app.dependency_overrides[get_db] = lambda: db
    test_app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(test_app)
    try:
        yield db, client, current, catalogo, feature
    finally:
        client.close()
        db.close()
        engine.dispose()


def conceder(
    db, modulo, feature=None, *, company_id=10, projeto_id=None,
    pesquisa_id=None, status="ATIVO", inicia_em=None, expira_em=None,
):
    entitlement = modulos.criar_entitlement(
        db, company_id, modulo.id, projeto_id=projeto_id,
        pesquisa_id=pesquisa_id, status=status, inicia_em=inicia_em,
        expira_em=expira_em,
    )
    db.flush()
    if feature is not None:
        modulos.vincular_funcionalidade(db, entitlement, feature)
    db.commit()
    return entitlement


def test_g01_tenant_com_entitlement_empresa_passa_require_module(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo)
    assert client.get("/_test/module").status_code == 200


def test_g02_tenant_sem_entitlement_recebe_403(ambiente):
    assert ambiente[1].get("/_test/module").status_code == 403


@pytest.mark.parametrize(
    "dados",
    [
        {"status": "SUSPENSO"},
        {"expira_em": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"inicia_em": datetime.now(timezone.utc) + timedelta(hours=1)},
    ],
    ids=["G03-suspenso", "G04-expirado", "G05-futuro"],
)
def test_g03_g05_entitlement_inefetivo_recebe_403(ambiente, dados):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, **dados)
    assert client.get("/_test/module").status_code == 403


def test_g06_g07_entitlement_empresa_funciona_nos_descendentes(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo)
    assert client.get("/_test/project/101/module").status_code == 200
    assert client.get("/_test/survey/1001/module").status_code == 200


def test_g08_g10_entitlement_projeto_so_vale_no_projeto_e_pesquisas_filhas(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, projeto_id=101)
    assert client.get("/_test/project/101/module").status_code == 200
    assert client.get("/_test/project/102/module").status_code == 403
    assert client.get("/_test/survey/1001/module").status_code == 200
    assert client.get("/_test/survey/1002/module").status_code == 403


def test_g11_entitlement_pesquisa_so_vale_na_pesquisa_correspondente(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, pesquisa_id=1001)
    assert client.get("/_test/survey/1001/module").status_code == 200
    assert client.get("/_test/survey/1002/module").status_code == 403


@pytest.mark.parametrize("escopo", [{"projeto_id": 101}, {"pesquisa_id": 1001}], ids=["G13", "G12"])
def test_g12_g13_entitlement_especifico_nao_satisfaz_endpoint_global(ambiente, escopo):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, **escopo)
    assert client.get("/_test/module").status_code == 403


def test_g14_feature_ativa_e_explicitamente_concedida_autoriza(ambiente):
    db, client, _, modulo, feature = ambiente
    conceder(db, modulo, feature)
    assert client.get("/_test/feature").status_code == 200


def test_g15_feature_ativa_nao_concedida_recebe_403(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo)
    assert client.get("/_test/feature").status_code == 403


def test_g16_feature_de_outro_modulo_nao_e_capacidade_disponivel(ambiente):
    db, client, _, modulo, _ = ambiente
    outro = models.Modulo(chave="outro_modulo", nome="Outro")
    db.add(models.ModuloFuncionalidade(modulo=outro, chave=FEATURE, nome="Outra feature"))
    db.query(models.ModuloFuncionalidade).filter_by(modulo_id=modulo.id, chave=FEATURE).delete()
    db.commit()
    conceder(db, modulo)
    assert client.get("/_test/feature").status_code == 404


def test_g17_feature_inativa_e_negada_mesmo_vinculada(ambiente):
    db, client, _, modulo, feature = ambiente
    feature.ativo = False
    conceder(db, modulo, feature)
    assert client.get("/_test/feature").status_code == 404


def test_g18_modulo_inativo_e_negado(ambiente):
    db, client, _, modulo, _ = ambiente
    modulo.ativo = False
    conceder(db, modulo)
    assert client.get("/_test/module").status_code == 404


def test_g19_modulo_inexistente_recebe_404(ambiente):
    db, _, current, _, _ = ambiente
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]

    @app.get("/x")
    def rota(_=Depends(require_module("inexistente"))):
        return {}

    with TestClient(app) as client:
        response = client.get("/x")
    assert response.status_code == 404
    assert response.json() == {"detail": "Capacidade comercial nao encontrada."}


def test_g20_feature_inexistente_recebe_404(ambiente):
    db, _, current, _, _ = ambiente
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]

    @app.get("/x")
    def rota(_=Depends(require_feature(MODULO, "inexistente"))):
        return {}

    with TestClient(app) as client:
        response = client.get("/x")
    assert response.status_code == 404
    assert response.json() == {"detail": "Capacidade comercial nao encontrada."}


def test_g21_nova_feature_nao_e_concedida_automaticamente(ambiente):
    db, _, current, modulo, feature = ambiente
    conceder(db, modulo, feature)
    nova = models.ModuloFuncionalidade(modulo=modulo, chave="feature_nova", nome="Nova")
    db.add(nova)
    db.commit()
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]

    @app.get("/x")
    def rota(_=Depends(require_feature(MODULO, "feature_nova"))):
        return {}

    with TestClient(app) as client:
        assert client.get("/x").status_code == 403


def test_g22_g23_cross_tenant_retorna_404_antes_do_entitlement(ambiente):
    _, client, _, _, _ = ambiente
    project = client.get("/_test/project/201/module")
    survey = client.get("/_test/survey/2001/module")
    assert project.status_code == 404
    assert survey.status_code == 404
    assert "Modulo" not in project.json()["detail"]
    assert "Modulo" not in survey.json()["detail"]


def test_g24_entitlement_empresa_a_nao_e_herdado_por_usuario_b(ambiente):
    db, client, current, modulo, _ = ambiente
    conceder(db, modulo, company_id=10)
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/_test/module").status_code == 403


def test_g25_entitlement_empresa_b_nao_e_usado_por_usuario_a(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, company_id=20)
    assert client.get("/_test/module").status_code == 403


def test_g26_company_id_da_query_nao_altera_tenant(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, company_id=20)
    assert client.get("/_test/module?company_id=20").status_code == 403


def test_entitlement_suspenso_especifico_nao_nega_entitlement_empresa(ambiente):
    db, client, _, modulo, _ = ambiente
    conceder(db, modulo, company_id=10)
    conceder(db, modulo, company_id=10, projeto_id=101, status="SUSPENSO")
    assert client.get("/_test/project/101/module").status_code == 200


def test_usuario_multiempresa_usa_tenant_do_recurso_autorizado(ambiente):
    db, client, _, modulo, _ = ambiente
    db.add(models.UsuarioEmpresaAcesso(
        usuario_id=1, company_id=20, acesso_todos_projetos=True, ativo=True,
        principal=False,
    ))
    db.commit()
    conceder(db, modulo, company_id=20)
    assert client.get("/_test/project/201/module").status_code == 200
    assert client.get("/_test/module").status_code == 403
