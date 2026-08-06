import os
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-spontaneous-analysis-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import apuracao_espontanea
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.utils.response_normalization import normalizar_resposta_espontanea


class ApuracaoEspontaneaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        @event.listens_for(cls.engine, "connect")
        def register_spatial_functions(connection, _):
            connection.create_function("AsEWKB", 1, lambda value: value)
            connection.create_function("ST_AsEWKB", 1, lambda value: value)
            connection.create_function("AsGeoJSON", 1, lambda value: None)
            connection.create_function("ST_AsGeoJSON", 1, lambda value: None)

        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, cnpj TEXT,
                    logo_url TEXT, is_active BOOLEAN, created_at DATETIME
                )
            """))
            connection.execute(text("""
                CREATE TABLE perfis (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT
                )
            """))
            connection.execute(text("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT,
                    senha_hash TEXT NOT NULL, ativo BOOLEAN NOT NULL,
                    perfil_id INTEGER NOT NULL, company_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT,
                    status TEXT NOT NULL, data_inicio DATE, data_fim DATE,
                    coordenador_id INTEGER NOT NULL, company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo_pesquisa TEXT,
                    ativo BOOLEAN NOT NULL, projeto_id INTEGER NOT NULL,
                    cerca_eletronica BLOB, tolerancia_metros INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE perguntas (
                    id INTEGER PRIMARY KEY, texto_pergunta TEXT NOT NULL,
                    tipo_pergunta TEXT NOT NULL, ordem INTEGER NOT NULL,
                    eh_obrigatoria BOOLEAN NOT NULL, eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0,
                    ativo BOOLEAN NOT NULL, pesquisa_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY, pesquisa_id INTEGER NOT NULL,
                    agente_id INTEGER NOT NULL, company_id INTEGER NOT NULL,
                    client_uuid TEXT NOT NULL, data_inicio_coleta DATETIME NOT NULL,
                    data_fim_coleta DATETIME, foi_offline BOOLEAN, endereco_estimado TEXT,
                    status_sincronizacao TEXT, inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            connection.execute(text("""
                CREATE TABLE respostas (
                    id INTEGER PRIMARY KEY, pergunta_id INTEGER NOT NULL,
                    coleta_id INTEGER NOT NULL, valor_resposta TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE categorias_resposta_espontanea (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL,
                    nome TEXT NOT NULL,
                    nome_normalizado TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    criado_por_id INTEGER NOT NULL,
                    atualizado_por_id INTEGER NOT NULL,
                    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            connection.execute(text("""
                CREATE UNIQUE INDEX uq_categoria_resposta_espontanea_pesquisa_nome_ativo
                ON categorias_resposta_espontanea (pesquisa_id, nome_normalizado)
                WHERE ativo = 1
            """))
            connection.execute(text("""
                CREATE TABLE mapeamentos_resposta_espontanea (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL,
                    categoria_id INTEGER NOT NULL,
                    chave_normalizada TEXT NOT NULL,
                    texto_referencia TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    criado_por_id INTEGER NOT NULL,
                    atualizado_por_id INTEGER NOT NULL,
                    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            connection.execute(text("""
                CREATE UNIQUE INDEX uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo
                ON mapeamentos_resposta_espontanea (pesquisa_id, chave_normalizada)
                WHERE ativo = 1
            """))

        cls.app = FastAPI()
        cls.app.include_router(apuracao_espontanea.router)

        def override_get_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        cls.app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as connection:
            for table in (
                "mapeamentos_resposta_espontanea",
                "categorias_resposta_espontanea",
                "respostas",
                "coletas",
                "perguntas",
                "pesquisas",
                "projetos",
                "usuarios",
                "perfis",
                "companies",
            ):
                connection.execute(text(f"DELETE FROM {table}"))

            connection.execute(text("""
                INSERT INTO companies (id, name, is_active)
                VALUES (1, 'Empresa A', 1),
                       (2, 'Empresa B', 1)
            """))
            connection.execute(text("""
                INSERT INTO perfis (id, nome)
                VALUES (10, 'Gerente'),
                       (11, 'Supervisor'),
                       (12, 'Agente')
            """))
            connection.execute(text("""
                INSERT INTO usuarios
                    (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'manager@a.com', 'Manager', 'hash', 1, 10, 1),
                       (2, 'other@b.com', 'Other', 'hash', 1, 10, 2),
                       (3, 'agent@a.com', 'Agent', 'hash', 1, 12, 1)
            """))
            connection.execute(text("""
                INSERT INTO projetos
                    (id, nome, status, coordenador_id, company_id)
                VALUES (100, 'Projeto A', 'Ativo', 1, 1),
                       (200, 'Projeto B', 'Ativo', 2, 2)
            """))
            connection.execute(text("""
                INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                VALUES (1000, 'Pesquisa A', 1, 100),
                       (2000, 'Pesquisa B', 1, 200)
            """))
            connection.execute(text("""
                INSERT INTO perguntas
                    (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, eh_resposta_espontanea, ativo, pesquisa_id)
                VALUES (10, 'Quem é seu candidato?', 'TEXTO', 1, 1, 1, 1, 1000),
                       (11, 'Quem você acha que será governador?', 'TEXTO', 2, 1, 1, 1, 1000),
                       (12, 'Pergunta não espontânea', 'TEXTO', 3, 1, 0, 1, 1000),
                       (13, 'Pergunta espontânea inativa', 'TEXTO', 4, 1, 1, 0, 1000),
                       (20, 'Pergunta da outra empresa', 'TEXTO', 1, 1, 1, 1, 2000)
            """))
            connection.execute(text("""
                INSERT INTO coletas
                    (id, pesquisa_id, agente_id, company_id, client_uuid, data_inicio_coleta,
                     data_fim_coleta, foi_offline, status_sincronizacao, inconformidade_localizacao)
                VALUES (100, 1000, 1, 1, 'uuid-1', '2026-08-06 10:00:00', '2026-08-06 10:05:00', 0, 'sincronizado', 0),
                       (101, 1000, 1, 1, 'uuid-2', '2026-08-06 10:10:00', '2026-08-06 10:15:00', 0, 'sincronizado', 0),
                       (200, 2000, 2, 2, 'uuid-3', '2026-08-06 11:00:00', '2026-08-06 11:05:00', 0, 'sincronizado', 0)
            """))
            connection.execute(text("""
                INSERT INTO respostas (id, pergunta_id, coleta_id, valor_resposta)
                VALUES (1, 10, 100, 'Clécio'),
                       (2, 10, 101, 'clecio'),
                       (3, 11, 100, 'Dr. Furlan'),
                       (4, 11, 101, 'dr furlan'),
                       (5, 12, 100, 'Ignorar'),
                       (6, 20, 200, 'Empresa B'),
                       (7, 10, 100, ''),
                       (8, 10, 101, '   '),
                       (9, 20, 100, 'Pergunta de outra pesquisa'),
                       (10, 13, 100, 'Pergunta inativa')
            """))

        self.current_user = SimpleNamespace(id=1, perfil_id=10, company_id=1)

        def override_get_current_user():
            return self.current_user

        self.app.dependency_overrides[get_current_user] = override_get_current_user

    def tearDown(self):
        self.app.dependency_overrides.pop(get_current_user, None)

    def test_normalization_helper_is_deterministic(self):
        self.assertEqual(normalizar_resposta_espontanea(" Clécio  "), "clecio")
        self.assertEqual(normalizar_resposta_espontanea("DR. FURLAN"), "dr furlan")

    def test_default_question_flag_stays_false_and_can_be_preserved_on_update(self):
        db = self.Session()
        try:
            created = crud.create_pergunta(
                db=db,
                pesquisa_id=1000,
                pergunta=schemas.PerguntaCreate(
                    texto_pergunta="Pergunta extra",
                    tipo_pergunta="TEXTO",
                    ordem=4,
                ),
            )
            self.assertFalse(created.eh_resposta_espontanea)

            updated = crud.update_pergunta(
                db=db,
                pesquisa_id=1000,
                pergunta_id=created.id,
                pergunta_in=schemas.PerguntaUpdate(
                    texto_pergunta="Pergunta extra",
                    eh_resposta_espontanea=True,
                ),
            )
            self.assertTrue(updated.eh_resposta_espontanea)
        finally:
            db.close()

    def test_grouping_categories_and_tenant_validation(self):
        response = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pesquisa_id"], 1000)
        self.assertEqual(payload["total_chaves"], 2)
        self.assertEqual(payload["total_respostas"], 4)
        self.assertEqual(payload["respostas_categorizadas"], 0)
        self.assertEqual(payload["respostas_pendentes"], 4)
        self.assertEqual(payload["itens"][0]["chave_normalizada"], "clecio")
        self.assertEqual(
            [variant["texto_original"] for variant in payload["itens"][0]["variantes"]],
            ["Clécio", "clecio"],
        )
        self.assertEqual(
            [question["id"] for question in payload["itens"][0]["perguntas"]],
            [10],
        )

        filtered = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas?busca=dr&modo_busca=comeca_com")
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.json()["total_chaves"], 1)
        self.assertEqual(filtered.json()["itens"][0]["chave_normalizada"], "dr furlan")

        paginated = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas?pagina=2&por_pagina=1")
        self.assertEqual(paginated.status_code, 200)
        self.assertEqual(paginated.json()["total_paginas"], 2)
        self.assertEqual(paginated.json()["total_chaves"], payload["total_chaves"])
        self.assertEqual(paginated.json()["total_respostas"], payload["total_respostas"])
        self.assertEqual(paginated.json()["itens"][0]["chave_normalizada"], "dr furlan")

        pagina_fora = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas?pagina=3&por_pagina=1")
        self.assertEqual(pagina_fora.status_code, 200)
        self.assertEqual(pagina_fora.json()["itens"], [])
        self.assertEqual(self.client.get(
            "/pesquisas/1000/apuracao-espontanea/respostas?por_pagina=101"
        ).status_code, 422)

        forbidden = self.client.get("/pesquisas/2000/apuracao-espontanea/respostas")
        self.assertEqual(forbidden.status_code, 404)

    def test_category_lifecycle_mapping_and_payload_validation(self):
        invalid_payload = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "Clécio Luís", "company_id": 999},
        )
        self.assertEqual(invalid_payload.status_code, 422)

        created = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "Clécio Luís"},
        )
        self.assertEqual(created.status_code, 201)
        categoria = created.json()
        self.assertEqual(categoria["nome"], "Clécio Luís")

        duplicate = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": " clecio luis "},
        )
        self.assertEqual(duplicate.status_code, 409)

        listed = self.client.get("/pesquisas/1000/apuracao-espontanea/categorias")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        updated = self.client.patch(
            f"/pesquisas/1000/apuracao-espontanea/categorias/{categoria['id']}",
            json={"nome": "Clécio do Norte"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["nome_normalizado"], "clecio do norte")

        lote = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clecio", "dr furlan"]},
        )
        self.assertEqual(lote.status_code, 200)
        self.assertEqual(lote.json()["criadas"], 2)

        categorizadas = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas?status=categorizada")
        self.assertEqual(categorizadas.status_code, 200)
        self.assertEqual(categorizadas.json()["total_chaves"], 2)
        self.assertEqual(categorizadas.json()["respostas_pendentes"], 0)

        deletar_categoria = self.client.delete(f"/pesquisas/1000/apuracao-espontanea/categorias/{categoria['id']}")
        self.assertEqual(deletar_categoria.status_code, 200)

        pendentes = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas?status=pendente")
        self.assertEqual(pendentes.status_code, 200)
        self.assertEqual(pendentes.json()["total_chaves"], 2)
        self.assertEqual(pendentes.json()["total_respostas"], 4)
        self.assertEqual(pendentes.json()["respostas_categorizadas"], 0)
        self.assertEqual(pendentes.json()["respostas_pendentes"], 4)
        self.assertEqual(pendentes.json()["percentual_categorizado"], 0.0)

        with self.engine.begin() as connection:
            categoria_audit = connection.execute(text(
                "SELECT ativo, criado_por_id, atualizado_por_id, criado_em, atualizado_em "
                "FROM categorias_resposta_espontanea WHERE id = :id"
            ), {"id": categoria["id"]}).one()
            mapeamentos = connection.execute(text(
                "SELECT ativo, atualizado_por_id, atualizado_em "
                "FROM mapeamentos_resposta_espontanea WHERE categoria_id = :id"
            ), {"id": categoria["id"]}).all()
            valor_original = connection.execute(
                text("SELECT valor_resposta FROM respostas WHERE id = 1")
            ).scalar_one()

        self.assertFalse(categoria_audit.ativo)
        self.assertEqual(categoria_audit.criado_por_id, 1)
        self.assertEqual(categoria_audit.atualizado_por_id, 1)
        self.assertIsNotNone(categoria_audit.criado_em)
        self.assertIsNotNone(categoria_audit.atualizado_em)
        self.assertEqual(len(mapeamentos), 2)
        self.assertTrue(all(not mapping.ativo for mapping in mapeamentos))
        self.assertTrue(all(mapping.atualizado_por_id == 1 for mapping in mapeamentos))
        self.assertTrue(all(mapping.atualizado_em is not None for mapping in mapeamentos))
        self.assertEqual(valor_original, "Clécio")

    def test_category_can_be_recreated_after_repeated_soft_deletes(self):
        for ciclo in range(3):
            created = self.client.post(
                "/pesquisas/1000/apuracao-espontanea/categorias",
                json={"nome": "  Intenção de voto  "},
            )
            self.assertEqual(created.status_code, 201)
            duplicate = self.client.post(
                "/pesquisas/1000/apuracao-espontanea/categorias",
                json={"nome": "intencao de voto"},
            )
            self.assertEqual(duplicate.status_code, 409)
            if ciclo < 2:
                deleted = self.client.delete(
                    f"/pesquisas/1000/apuracao-espontanea/categorias/{created.json()['id']}"
                )
                self.assertEqual(deleted.status_code, 200)

        with self.engine.begin() as connection:
            historico = connection.execute(text(
                "SELECT ativo FROM categorias_resposta_espontanea "
                "WHERE pesquisa_id = 1000 AND nome_normalizado = 'intencao de voto'"
            )).all()
        self.assertEqual(len(historico), 3)
        self.assertEqual(sum(bool(row.ativo) for row in historico), 1)

    def test_batch_is_atomic_and_mapping_can_be_repeatedly_undone_and_recreated(self):
        categoria = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "Candidato"},
        ).json()

        self.assertEqual(self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": []},
        ).status_code, 422)
        self.assertEqual(self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": ["Clécio", "clecio"]},
        ).status_code, 422)

        falha = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clecio", "inexistente"]},
        )
        self.assertEqual(falha.status_code, 404)
        with self.engine.begin() as connection:
            self.assertEqual(connection.execute(text(
                "SELECT COUNT(*) FROM mapeamentos_resposta_espontanea WHERE ativo = 1"
            )).scalar_one(), 0)

        for ciclo in range(3):
            mapped = self.client.post(
                "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
                json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clécio"]},
            )
            self.assertEqual(mapped.status_code, 200)
            self.assertEqual(mapped.json()["criadas"], 1)
            same_mapping = self.client.post(
                "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
                json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clecio"]},
            )
            self.assertEqual(same_mapping.status_code, 200)
            self.assertEqual(same_mapping.json()["inalteradas"], 1)

            with self.engine.begin() as connection:
                mapping_id = connection.execute(text(
                    "SELECT id FROM mapeamentos_resposta_espontanea "
                    "WHERE pesquisa_id = 1000 AND chave_normalizada = 'clecio' AND ativo = 1"
                )).scalar_one()
            if ciclo < 2:
                undone = self.client.delete(
                    f"/pesquisas/1000/apuracao-espontanea/mapeamentos/{mapping_id}"
                )
                self.assertEqual(undone.status_code, 200, msg=f"falha no ciclo {ciclo + 1}")

        with self.engine.begin() as connection:
            historico = connection.execute(text(
                "SELECT ativo, atualizado_por_id FROM mapeamentos_resposta_espontanea "
                "WHERE pesquisa_id = 1000 AND chave_normalizada = 'clecio'"
            )).all()
        self.assertEqual(len(historico), 3)
        self.assertEqual(sum(bool(row.ativo) for row in historico), 1)
        self.assertTrue(all(row.atualizado_por_id == 1 for row in historico))

    def test_inactive_and_cross_scope_categories_are_rejected(self):
        categoria = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "Inativa"},
        ).json()
        self.assertEqual(self.client.delete(
            f"/pesquisas/1000/apuracao-espontanea/categorias/{categoria['id']}"
        ).status_code, 200)
        self.assertEqual(self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clecio"]},
        ).status_code, 404)

        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO categorias_resposta_espontanea "
                "(id, pesquisa_id, nome, nome_normalizado, ativo, criado_por_id, atualizado_por_id) "
                "VALUES (900, 2000, 'Outro tenant', 'outro tenant', 1, 2, 2)"
            ))
            connection.execute(text(
                "INSERT INTO mapeamentos_resposta_espontanea "
                "(id, pesquisa_id, categoria_id, chave_normalizada, texto_referencia, ativo, criado_por_id, atualizado_por_id) "
                "VALUES (901, 2000, 900, 'empresa b', 'Empresa B', 1, 2, 2)"
            ))

        self.assertEqual(self.client.patch(
            "/pesquisas/2000/apuracao-espontanea/categorias/900", json={"nome": "Intrusão"}
        ).status_code, 404)
        self.assertEqual(self.client.delete(
            "/pesquisas/2000/apuracao-espontanea/categorias/900"
        ).status_code, 404)
        self.assertEqual(self.client.delete(
            "/pesquisas/2000/apuracao-espontanea/mapeamentos/901"
        ).status_code, 404)
        self.assertEqual(self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": 900, "chaves_normalizadas": ["clecio"]},
        ).status_code, 404)

    def test_empty_survey_and_percentages_are_consistent(self):
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id) "
                "VALUES (1001, 'Pesquisa vazia', 1, 100)"
            ))
        vazio = self.client.get("/pesquisas/1001/apuracao-espontanea/respostas")
        self.assertEqual(vazio.status_code, 200)
        self.assertEqual(vazio.json()["total_chaves"], 0)
        self.assertEqual(vazio.json()["total_respostas"], 0)
        self.assertEqual(vazio.json()["percentual_categorizado"], 0.0)
        self.assertEqual(vazio.json()["total_paginas"], 1)

        categoria = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias", json={"nome": "Todos"}
        ).json()
        self.assertEqual(self.client.post(
            "/pesquisas/1000/apuracao-espontanea/mapeamentos/lote",
            json={"categoria_id": categoria["id"], "chaves_normalizadas": ["clecio", "dr furlan"]},
        ).status_code, 200)
        completo = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas")
        self.assertEqual(completo.json()["respostas_categorizadas"], 4)
        self.assertEqual(completo.json()["respostas_pendentes"], 0)
        self.assertEqual(completo.json()["percentual_categorizado"], 100.0)

    def test_company_id_from_other_tenant_is_blocked(self):
        self.current_user = SimpleNamespace(id=2, perfil_id=10, company_id=2)
        response = self.client.get("/pesquisas/1000/apuracao-espontanea/respostas")
        self.assertEqual(response.status_code, 404)


    def test_unicode_category_roundtrip_preserves_nome_and_db_value(self):
        created = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "Clécio Luís"},
        )
        self.assertEqual(created.status_code, 201)
        created_payload = created.json()
        categoria_id = created_payload["id"]
        self.assertEqual(created_payload["nome"], "Clécio Luís")
        self.assertEqual(created_payload["nome_normalizado"], "clecio luis")

        listed = self.client.get("/pesquisas/1000/apuracao-espontanea/categorias")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["nome"], "Clécio Luís")
        self.assertEqual(listed.json()[0]["nome_normalizado"], "clecio luis")

        updated = self.client.patch(
            f"/pesquisas/1000/apuracao-espontanea/categorias/{categoria_id}",
            json={"nome": "Não sabe/Indeciso"},
        )
        self.assertEqual(updated.status_code, 200)
        updated_payload = updated.json()
        self.assertEqual(updated_payload["nome"], "Não sabe/Indeciso")
        self.assertEqual(updated_payload["nome_normalizado"], "nao sabe indeciso")

        extra = self.client.post(
            "/pesquisas/1000/apuracao-espontanea/categorias",
            json={"nome": "João d'Ávila - seção/ação"},
        )
        self.assertEqual(extra.status_code, 201)
        self.assertEqual(extra.json()["nome"], "João d'Ávila - seção/ação")
        self.assertEqual(extra.json()["nome_normalizado"], "joao d avila secao acao")

        with self.engine.begin() as connection:
            categoria_db = connection.execute(text(
                "SELECT nome, nome_normalizado FROM categorias_resposta_espontanea WHERE id = :id"
            ), {"id": categoria_id}).one()
            extra_db = connection.execute(text(
                "SELECT nome, nome_normalizado FROM categorias_resposta_espontanea WHERE id = :id"
            ), {"id": extra.json()["id"]}).one()

        self.assertEqual(categoria_db.nome, "Não sabe/Indeciso")
        self.assertEqual(categoria_db.nome_normalizado, "nao sabe indeciso")
        self.assertEqual(extra_db.nome, "João d'Ávila - seção/ação")
        self.assertEqual(extra_db.nome_normalizado, "joao d avila secao acao")


if __name__ == "__main__":
    unittest.main()
