"""Prompt 04: administracao Superadmin de entitlements (A01-A32)."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-admin-modulos")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("UPLOAD_DIRECTORY", str(Path(tempfile.gettempdir()) / "pesquisa360-test-uploads"))
os.environ.setdefault("UPLOAD_MAX_SIZE_BYTES", "1048576")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://testserver")

from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.main import app
from pesquisa360.services import auditoria


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def sqlite(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        for name, argc in (("RecoverGeometryColumn", 5), ("CreateSpatialIndex", 2), ("CheckSpatialIndex", 2),
                           ("DisableSpatialIndex", 2), ("DiscardGeometryColumn", 2)):
            conn.create_function(name, argc, lambda *_args: 1)
        conn.create_function("GeomFromEWKT", 1, lambda value: value)
        conn.create_function("ST_GeomFromEWKT", 1, lambda value: value)
        conn.create_function("AsEWKB", 1, lambda value: value)

    tables = [models.Company.__table__, models.Perfil.__table__, models.Usuario.__table__, models.AuditEvent.__table__,
              models.Projeto.__table__, models.Pesquisa.__table__, models.Modulo.__table__,
              models.ModuloFuncionalidade.__table__, models.ModuloEntitlement.__table__,
              models.ModuloEntitlementFuncionalidade.__table__]
    models.Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add_all([models.Company(id=10, name="Empresa A", is_active=True), models.Company(id=20, name="Empresa B", is_active=True),
                models.Perfil(id=1, nome="Superadmin"), models.Perfil(id=2, nome="Gerente"), models.Perfil(id=3, nome="Cliente")])
    db.flush()
    admin = models.Usuario(id=1, email="admin@x", nome="Admin", senha_hash="x", ativo=True, perfil_id=1, company_id=10)
    gerente = models.Usuario(id=2, email="gerente@x", nome="Gerente", senha_hash="x", ativo=True, perfil_id=2, company_id=10)
    cliente = models.Usuario(id=3, email="cliente@x", nome="Cliente", senha_hash="x", ativo=True, perfil_id=3, company_id=10)
    db.add_all([admin, gerente, cliente]); db.flush()
    db.add_all([models.Projeto(id=101, nome="A1", coordenador_id=2, company_id=10),
                models.Projeto(id=201, nome="B1", coordenador_id=1, company_id=20)])
    db.flush(); db.add_all([models.Pesquisa(id=1001, titulo="PA", projeto_id=101), models.Pesquisa(id=2001, titulo="PB", projeto_id=201)])
    modulo = models.Modulo(id=1, chave="inteligencia_eleitoral", nome="Inteligencia Eleitoral", ativo=True)
    outro = models.Modulo(id=2, chave="outro", nome="Outro", ativo=True)
    ativa = models.ModuloFuncionalidade(id=1, modulo=modulo, chave="painel", nome="Painel", ativo=True)
    inativa = models.ModuloFuncionalidade(id=2, modulo=modulo, chave="potencial_crescimento", nome="Potencial", ativo=False)
    alheia = models.ModuloFuncionalidade(id=3, modulo=outro, chave="outra", nome="Outra", ativo=True)
    db.add_all([modulo, outro, ativa, inativa, alheia]); db.commit()
    current = {"user": admin}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)
    try:
        yield db, client, current
    finally:
        app.dependency_overrides.clear(); client.close(); db.close(); engine.dispose()


def post(client, **overrides):
    payload = {"modulo_id": 1, "escopo": "EMPRESA", "projeto_id": None, "pesquisa_id": None,
               "inicia_em": None, "expira_em": None, "funcionalidade_ids": []}
    payload.update(overrides)
    return client.post("/admin/empresas/10/entitlements/", json=payload)


def test_a01_a06_somente_superadmin_administra(env):
    db, client, current = env
    assert client.get("/admin/modulos/").status_code == 200
    assert client.get("/admin/empresas/10/entitlements/").status_code == 200
    for user_id in (2, 3):
        current["user"] = db.get(models.Usuario, user_id)
        assert client.get("/admin/modulos/").status_code == 403
        assert client.get("/admin/empresas/10/entitlements/").status_code == 403
        assert post(client).status_code == 403
        assert client.get("/admin/empresas/20/entitlements/").status_code == 403


@pytest.mark.parametrize("escopo,projeto,pesquisa", [("EMPRESA", None, None), ("PROJETO", 101, None), ("PESQUISA", None, 1001)])
def test_a07_a09_cria_escopos_validos(env, escopo, projeto, pesquisa):
    response = post(env[1], escopo=escopo, projeto_id=projeto, pesquisa_id=pesquisa)
    assert response.status_code == 201
    assert response.json()["escopo"] == escopo


def test_a10_a12_cross_tenant_e_escopo_ambiguo_rejeitados(env):
    client = env[1]
    assert post(client, escopo="PROJETO", projeto_id=201).status_code == 404
    assert post(client, escopo="PESQUISA", pesquisa_id=2001).status_code == 404
    assert post(client, escopo="EMPRESA", projeto_id=101).status_code == 422
    assert post(client, escopo="PROJETO", projeto_id=101, pesquisa_id=1001).status_code == 422


def test_a13_duplicado_retorna_409(env):
    assert post(env[1]).status_code == 201
    assert post(env[1]).status_code == 409


def test_a14_a16_feature_modulo_inativa_e_datas_rejeitadas(env):
    client = env[1]
    assert post(client, funcionalidade_ids=[3]).status_code == 422
    assert post(client, funcionalidade_ids=[2]).status_code == 422
    inicio = datetime.now(timezone.utc)
    assert post(client, inicia_em=inicio.isoformat(), expira_em=(inicio - timedelta(days=1)).isoformat()).status_code == 422


def test_a17_a20_status_e_capability(env):
    db, client, current = env
    entitlement_id = post(client).json()["id"]
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json()["modulos"]
    current["user"] = db.get(models.Usuario, 1)
    url = f"/admin/empresas/10/entitlements/{entitlement_id}"
    assert client.patch(url, json={"status": "SUSPENSO"}).status_code == 200
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json() == {"modulos": []}
    current["user"] = db.get(models.Usuario, 1)
    assert client.patch(url, json={"status": "ATIVO"}).json()["status"] == "ATIVO"
    assert client.patch(url, json={"status": "CANCELADO"}).json()["status"] == "CANCELADO"
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json() == {"modulos": []}


def test_a21_a22_features_sao_estado_explicito(env):
    db, client, _ = env
    item = post(client, funcionalidade_ids=[1]).json()
    url = f"/admin/empresas/10/entitlements/{item['id']}/funcionalidades"
    assert [x["id"] for x in item["funcionalidades"]] == [1]
    nova = models.ModuloFuncionalidade(modulo_id=1, chave="nova", nome="Nova", ativo=True)
    db.add(nova); db.commit()
    listado = client.get("/admin/empresas/10/entitlements/").json()["entitlements"][0]
    assert [x["id"] for x in listado["funcionalidades"]] == [1]
    assert client.put(url, json={"funcionalidade_ids": []}).json()["funcionalidades"] == []


def test_a23_entitlement_de_outra_empresa_nao_edita(env):
    db, client, _ = env
    item = models.ModuloEntitlement(company_id=20, modulo_id=1)
    db.add(item); db.commit()
    assert client.patch(f"/admin/empresas/10/entitlements/{item.id}", json={"status": "SUSPENSO"}).status_code == 404


def test_a24_a28_efeito_imediato_na_capability(env):
    db, client, current = env
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json() == {"modulos": []}
    current["user"] = db.get(models.Usuario, 1)
    item = post(client).json(); url = f"/admin/empresas/10/entitlements/{item['id']}"
    current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json()["modulos"][0]["chave"] == "inteligencia_eleitoral"
    current["user"] = db.get(models.Usuario, 1)
    client.patch(url, json={"status": "SUSPENSO"}); current["user"] = db.get(models.Usuario, 2)
    assert client.get("/usuarios/me/modulos/").json() == {"modulos": []}
    current["user"] = db.get(models.Usuario, 1); client.patch(url, json={"status": "ATIVO"})
    current["user"] = db.get(models.Usuario, 2); assert client.get("/usuarios/me/modulos/").json()["modulos"]
    current["user"] = db.get(models.Usuario, 1); client.patch(url, json={"status": "CANCELADO"})
    current["user"] = db.get(models.Usuario, 2); assert client.get("/usuarios/me/modulos/").json() == {"modulos": []}


def test_a29_a32_auditoria_persistente(env):
    db, client, _ = env
    item = post(client, funcionalidade_ids=[1]).json(); url = f"/admin/empresas/10/entitlements/{item['id']}"
    client.patch(url, json={"status": "SUSPENSO"})
    client.put(url + "/funcionalidades", json={"funcionalidade_ids": []})
    eventos = db.query(models.AuditEvent).order_by(models.AuditEvent.id).all()
    assert [e.event_type for e in eventos] == [auditoria.MODULE_ENTITLEMENT_CREATED, auditoria.MODULE_ENTITLEMENT_UPDATED,
                                               auditoria.MODULE_ENTITLEMENT_FEATURES_UPDATED]
    assert all(e.user_id == 1 and e.company_id == 10 for e in eventos)
    assert eventos[1].details["before"]["status"] == "ATIVO"
    assert eventos[1].details["after"]["status"] == "SUSPENSO"
    assert eventos[2].details["before"]["funcionalidades"]
    assert eventos[2].details["after"]["funcionalidades"] == []
