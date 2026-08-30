import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


os.environ.setdefault("SECRET_KEY", "test-only-setor-agentes-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints.agente import get_missao_agente
from pesquisa360.api.endpoints.projetos import setor_to_dict
from tests.acl_fixture import criar_tabelas_acl


def usuario(user_id=1, company_id=10, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id,
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome=perfil),
    )


class SetorAgentesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        raw = self.engine.raw_connection()
        raw.create_function("ST_GeomFromText", 2, lambda value, srid: value)
        raw.create_function(
            "ST_AsGeoJSON",
            1,
            lambda value: json.dumps(mapping(shapely_wkt.loads(value))) if value else None,
        )
        raw.create_function(
            "AsGeoJSON",
            1,
            lambda value: json.dumps(mapping(shapely_wkt.loads(value))) if value else None,
        )
        raw.create_function("AsEWKB", 1, lambda value: value)
        raw.close()
        criar_tabelas_acl(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.engine.begin() as connection:
            for statement in (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT, is_active BOOLEAN, created_at DATETIME)",
                "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
                "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
                "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER)",
                "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
                "CREATE TABLE setor_agentes (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, CONSTRAINT uq_setor_agentes_setor_agente UNIQUE (setor_id, agente_id))",
                "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER, setor_id INTEGER)",
                # FASE F: a missao passou a resolver municipio e perguntas aplicaveis.
                "CREATE TABLE territorio_eleitoral (id INTEGER PRIMARY KEY, base_eleitoral_id INTEGER, parent_id INTEGER, tipo TEXT, nome TEXT, nome_normalizado TEXT, municipio_id INTEGER)",
                "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL, criado_em DATETIME)",
                "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN DEFAULT 0, papel_analitico TEXT, metadados_analiticos TEXT DEFAULT '{}', ativo BOOLEAN DEFAULT 1, pesquisa_id INTEGER, aplicabilidade TEXT NOT NULL DEFAULT 'GLOBAL')",
                "CREATE TABLE pergunta_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL)",
                # PROMPT 05: a missao consulta o plano de cotas de perfil.
                "CREATE TABLE planos_cota_perfil (id INTEGER PRIMARY KEY, pesquisa_id INTEGER NOT NULL, company_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, pergunta_sexo_id INTEGER NOT NULL, pergunta_idade_id INTEGER NOT NULL, modo_idade TEXT NOT NULL DEFAULT 'NUMERICA', sexo_valores TEXT NOT NULL, criado_em DATETIME, atualizado_em DATETIME)",
                "CREATE TABLE cotas_perfil (id INTEGER PRIMARY KEY, plano_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL, sexo TEXT NOT NULL, faixa_rotulo TEXT NOT NULL, idade_min INTEGER, idade_max INTEGER, idade_valores TEXT, meta INTEGER NOT NULL DEFAULT 0, ordem INTEGER NOT NULL DEFAULT 0)",
            ):
                connection.execute(text(statement))
            connection.execute(text("INSERT INTO companies VALUES (10,'A',NULL,NULL,1,NULL),(20,'B',NULL,NULL,1,NULL)"))
            connection.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL),(3,'Analista',NULL)"))
            connection.execute(text("""INSERT INTO usuarios VALUES
                (1,'g@a','Gerente A','x',1,1,10),
                (2,'a1@a','Agente 1','x',1,2,10),
                (3,'a2@a','Agente 2','x',1,2,10),
                (4,'a@b','Agente B','x',1,2,20),
                (5,'x@a','Analista A','x',1,3,10),
                (6,'a3@a','Agente 3','x',1,2,10)"""))
            connection.execute(text("INSERT INTO projetos VALUES (100,'P',NULL,'Ativo',NULL,NULL,1,10)"))
            connection.execute(text("INSERT INTO pesquisas VALUES (1000,'Q',NULL,1,100,NULL,NULL)"))
            connection.execute(text("""INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) VALUES
                (500,'Centro',200,50,'OPERACAO',NULL,1000,2),
                (501,'Trem',100,50,'OPERACAO',NULL,1000,NULL)"""))
            connection.execute(text("INSERT INTO setor_agentes (setor_id,agente_id,ativo) VALUES (500,2,1)"))

    def tearDown(self):
        self.engine.dispose()

    def atualizar(self, db, setor_id, payload):
        return crud.update_setor(
            db=db,
            projeto_id=100,
            pesquisa_id=1000,
            setor_id=setor_id,
            setor_update=schemas.SetorUpdate(**payload),
            current_user=usuario(),
        )

    def ativos(self, db, setor_id):
        return [agente.id for agente in crud.listar_agentes_ativos_setor(db, setor_id)]

    def inserir_coletas(self, db, *, inicio, quantidade, agente_id, setor_id):
        db.execute(
            text(
                "INSERT INTO coletas (id, pesquisa_id, agente_id, setor_id) "
                "VALUES (:id, 1000, :agente_id, :setor_id)"
            ),
            [
                {
                    "id": inicio + indice,
                    "agente_id": agente_id,
                    "setor_id": setor_id,
                }
                for indice in range(quantidade)
            ],
        )
        db.commit()

    def test_um_setor_tres_agentes_e_um_agente_dois_setores(self):
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_ids": [2, 3, 6]})
            self.atualizar(db, 501, {"agente_ids": [2]})
            self.assertEqual(self.ativos(db, 500), [2, 3, 6])
            self.assertEqual(self.ativos(db, 501), [2])
            setores_agente_2 = db.execute(
                text("SELECT setor_id FROM setor_agentes WHERE agente_id=2 AND ativo=1 ORDER BY setor_id")
            ).scalars().all()
            self.assertEqual(setores_agente_2, [500, 501])

            row = crud.get_setores_by_pesquisa(db, 1000)[0]
            payload = setor_to_dict(row, db)
            self.assertEqual(payload["agente_ids"], [2, 3, 6])
            self.assertEqual([item["nome"] for item in payload["agentes"]], ["Agente 1", "Agente 2", "Agente 3"])

    def test_create_legado_e_create_lista_explicita(self):
        with self.Session() as db:
            legado = crud.create_setor(
                db=db,
                setor_in=schemas.SetorCreate(
                    nome="Legado",
                    meta=10,
                    agente_id=2,
                    geometria_coords=[[0, -51], [0, -50], [1, -50]],
                ),
                pesquisa_id=1000,
                current_user=usuario(),
            )
            legado_id = legado.id
            novo = crud.create_setor(
                db=db,
                setor_in=schemas.SetorCreate(
                    nome="N para N",
                    meta=20,
                    agente_id=2,
                    agente_ids=[2, 3, 6],
                    geometria_coords=[[0, -51], [0, -50], [1, -50]],
                ),
                pesquisa_id=1000,
                current_user=usuario(),
            )
            novo_id = novo.id
            self.assertEqual(self.ativos(db, legado_id), [2])
            self.assertEqual(self.ativos(db, novo_id), [2, 3, 6])

    def test_lista_explicita_prevalece_sobre_agente_id(self):
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_id": 2, "agente_ids": [3, 6]})
            self.assertEqual(self.ativos(db, 500), [3, 6])
            self.assertEqual(
                db.execute(text("SELECT agente_id FROM setores WHERE id=500")).scalar_one(),
                3,
            )

    def test_duplicidade_e_patch_legado_aditivo(self):
        with self.assertRaises(ValidationError):
            schemas.SetorUpdate(agente_ids=[2, 2])
        with self.assertRaises(ValidationError):
            schemas.SetorUpdate(agente_ids=None)
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_ids": [2, 3]})
            self.atualizar(db, 500, {"agente_id": 2})
            self.atualizar(db, 500, {"agente_id": 2})
            self.assertEqual(self.ativos(db, 500), [2, 3])
            quantidade = db.execute(
                text("SELECT count(*) FROM setor_agentes WHERE setor_id=500 AND agente_id=2")
            ).scalar_one()
            self.assertEqual(quantidade, 1)

    def test_patch_explicito_substitui_e_lista_vazia_desativa(self):
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_ids": [2, 3]})
            self.atualizar(db, 500, {"agente_ids": [3, 6]})
            estados = db.execute(
                text("SELECT agente_id, ativo FROM setor_agentes WHERE setor_id=500 ORDER BY agente_id")
            ).all()
            self.assertEqual(estados, [(2, 0), (3, 1), (6, 1)])
            self.assertEqual(db.execute(text("SELECT agente_id FROM setores WHERE id=500")).scalar_one(), 3)

            self.atualizar(db, 500, {"agente_ids": []})
            self.assertEqual(self.ativos(db, 500), [])
            self.assertIsNone(db.execute(text("SELECT agente_id FROM setores WHERE id=500")).scalar_one())

    def test_rejeita_tenant_cruzado_e_usuario_nao_agente_sem_parcial(self):
        with self.Session() as db:
            for ids in ([2, 4], [2, 5]):
                with self.assertRaises(HTTPException) as contexto:
                    self.atualizar(db, 500, {"agente_ids": ids})
                self.assertEqual(contexto.exception.status_code, 404)
                self.assertEqual(self.ativos(db, 500), [2])

    def test_missao_compartilhada_preserva_contrato_legado(self):
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_ids": [2, 3]})
            with patch("pesquisa360.api.endpoints.agente.crud.get_pesquisa", return_value=object()):
                missao_2 = get_missao_agente(db=db, pesquisa_id=1000, current_user=usuario(2, 10, "Agente"))
                missao_3 = get_missao_agente(db=db, pesquisa_id=1000, current_user=usuario(3, 10, "Agente"))
                missao_6 = get_missao_agente(db=db, pesquisa_id=1000, current_user=usuario(6, 10, "Agente"))
            self.assertEqual([item["id"] for item in missao_2["setores"]], [500])
            self.assertEqual([item["id"] for item in missao_3["setores"]], [500])
            self.assertEqual(missao_6["setores"], [])
            for campo in ("tem_setor", "setor_id", "setor_nome", "meta", "realizado", "restante", "tolerancia_metros", "geometria", "setores"):
                self.assertIn(campo, missao_2)

    def test_cota_setor_soma_agentes_isola_setores_e_ignora_null(self):
        with self.Session() as db:
            self.atualizar(db, 500, {"agente_ids": [2, 3, 6]})
            self.atualizar(db, 501, {"agente_ids": [2]})
            self.inserir_coletas(db, inicio=1, quantidade=40, agente_id=2, setor_id=500)
            self.inserir_coletas(db, inicio=41, quantidade=60, agente_id=3, setor_id=500)
            self.inserir_coletas(db, inicio=101, quantidade=25, agente_id=6, setor_id=500)
            self.inserir_coletas(db, inicio=126, quantidade=30, agente_id=2, setor_id=501)
            self.inserir_coletas(db, inicio=156, quantidade=50, agente_id=2, setor_id=None)

            with patch("pesquisa360.api.endpoints.agente.crud.get_pesquisa", return_value=object()):
                missao_2 = get_missao_agente(
                    db=db, pesquisa_id=1000, current_user=usuario(2, 10, "Agente")
                )
                missao_3 = get_missao_agente(
                    db=db, pesquisa_id=1000, current_user=usuario(3, 10, "Agente")
                )

            for missao in (missao_2, missao_3):
                self.assertEqual(missao["realizado"], 125)
                self.assertEqual(missao["restante"], 75)
                self.assertEqual(missao["excedente"], 0)
                self.assertEqual(missao["percentual_atingimento"], 62.5)
                self.assertFalse(missao["cota_atingida"])
                self.assertEqual(missao["setores"][0]["realizado"], 125)
            self.assertEqual(missao_2["setores"][1]["realizado"], 30)
            self.assertEqual(len(missao_3["setores"]), 1)

            setores = crud.get_setores_by_pesquisa(db, 1000)
            progressos = crud.obter_progressos_setores(db, setores, pesquisa_id=1000)
            self.assertEqual(progressos[500]["realizado"], 125)
            self.assertEqual(progressos[501]["realizado"], 30)
            payload_setor = setor_to_dict(setores[0], db, progressos[500])
            self.assertEqual(payload_setor["realizado"], 125)
            self.assertEqual(payload_setor["restante"], 75)

            db.execute(
                text(
                    "UPDATE setor_agentes SET ativo=0 "
                    "WHERE setor_id=500 AND agente_id=2"
                )
            )
            db.execute(text("UPDATE setores SET agente_id=NULL WHERE id=500"))
            db.commit()
            progressos_apos_remocao = crud.obter_progressos_setores(
                db, setores, pesquisa_id=1000
            )
            self.assertEqual(progressos_apos_remocao[500]["realizado"], 125)

    def test_metricas_de_cota_atingida_ultrapassada_e_meta_zero(self):
        self.assertEqual(
            crud.calcular_progresso_setor(200, 200),
            {
                "meta": 200,
                "realizado": 200,
                "restante": 0,
                "excedente": 0,
                "percentual_atingimento": 100.0,
                "cota_atingida": True,
            },
        )
        self.assertEqual(
            crud.calcular_progresso_setor(200, 203),
            {
                "meta": 200,
                "realizado": 203,
                "restante": 0,
                "excedente": 3,
                "percentual_atingimento": 101.5,
                "cota_atingida": True,
            },
        )
        zero = crud.calcular_progresso_setor(0, 3)
        self.assertIsNone(zero["percentual_atingimento"])
        self.assertIsNone(zero["cota_atingida"])

    def test_progressos_de_varios_setores_usam_uma_query_agrupada(self):
        statements = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def registrar(_conn, _cursor, statement, _params, _context, _many):
            if "FROM coletas" in statement:
                statements.append(statement)

        try:
            with self.Session() as db:
                setores = crud.get_setores_by_pesquisa(db, 1000)
                crud.obter_progressos_setores(db, setores, pesquisa_id=1000)
        finally:
            event.remove(self.engine, "before_cursor_execute", registrar)

        self.assertEqual(len(statements), 1)
        self.assertIn("GROUP BY coletas.setor_id", statements[0])


if __name__ == "__main__":
    unittest.main()
