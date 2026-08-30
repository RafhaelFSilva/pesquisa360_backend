"""Integridade do DELETE de Setor apos a FASE B (`Coleta.setor_id`).

O ponto central destes testes nao e o codigo HTTP: e o HISTORICO.

Antes desta fase o ORM carregava `Setor.coletas` e emitia
``UPDATE coletas SET setor_id = NULL`` antes do DELETE -- a exclusao "funcionava"
com HTTP 200 e desvinculava em silencio todas as coletas do setor. Os testes
abaixo travam os dois lados: a resposta controlada (409) e, sobretudo, o fato de
que nenhuma coleta e apagada nem perde o seu `setor_id`.

FK enforcement do SQLite fica LIGADA de proposito: sem ela o cenario de corrida
(pos-checagem, no commit) nao teria como acontecer no teste.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


os.environ.setdefault("SECRET_KEY", "test-only-setor-delete-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud
from pesquisa360.db import models
from pesquisa360.api.endpoints.projetos import (
    FK_COLETAS_SETOR,
    _conflito_coletas_do_setor,
    delete_setor_by_projeto_pesquisa,
)
from tests.acl_fixture import criar_tabelas_acl


PROJETO_A = 100
PESQUISA_A = 1000
SETOR_COM_HISTORICO = 500
SETOR_VAZIO = 501

PROJETO_B = 200
PESQUISA_B = 2000
SETOR_B = 900


def usuario(user_id=1, company_id=10):
    return SimpleNamespace(
        id=user_id,
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome="Gerente"),
    )


class SetorDeleteIntegridadeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Sem este PRAGMA o SQLite ignora FK e o teste de corrida viraria falso
        # positivo: o DELETE passaria onde o Postgres barra.
        @event.listens_for(self.engine, "connect")
        def _preparar_conexao(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            # Stubs das funcoes PostGIS que o mapeamento emite ao ler geometria.
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)
            dbapi_connection.create_function("ST_GeomFromText", 2, lambda valor, srid: valor)
            dbapi_connection.create_function("ST_AsGeoJSON", 1, lambda valor: valor)

        criar_tabelas_acl(self.engine)

        criar_tabelas_acl(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.engine.begin() as connection:
            for statement in (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT,"
                " logo_url TEXT, is_active BOOLEAN, created_at DATETIME)",
                "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT,"
                " senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
                "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT,"
                " status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER,"
                " company_id INTEGER)",
                "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT,"
                " tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER,"
                " cerca_eletronica TEXT, tolerancia_metros INTEGER)",
                "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER,"
                " tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER,"
                " agente_id INTEGER, municipio_territorio_id INTEGER)",
                # Espelha a producao: CASCADE na associacao N:N (FASE A)...
                "CREATE TABLE setor_agentes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " setor_id INTEGER NOT NULL REFERENCES setores(id) ON DELETE CASCADE,"
                " agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1,"
                " CONSTRAINT uq_setor_agentes_setor_agente UNIQUE (setor_id, agente_id))",
                "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " setor_id INTEGER NOT NULL REFERENCES setores(id) ON DELETE CASCADE,"
                " territorio_eleitoral_id INTEGER NOT NULL, criado_em DATETIME)",
                # ...e NO ACTION na coleta (FASE B): o historico barra o DELETE.
                "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER,"
                " agente_id INTEGER, setor_id INTEGER"
                f" CONSTRAINT {FK_COLETAS_SETOR} REFERENCES setores(id))",
            ):
                connection.execute(text(statement))

            connection.execute(text(
                "INSERT INTO companies VALUES (10,'A',NULL,NULL,1,NULL),(20,'B',NULL,NULL,1,NULL)"
            ))
            connection.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL)"))
            connection.execute(text(
                "INSERT INTO usuarios VALUES (1,'g@a','Gerente A','x',1,1,10),"
                "(2,'a1@a','Agente 1','x',1,2,10),(3,'g@b','Gerente B','x',1,1,20)"
            ))
            connection.execute(text(
                f"INSERT INTO projetos VALUES ({PROJETO_A},'P A',NULL,'Ativo',NULL,NULL,1,10),"
                f"({PROJETO_B},'P B',NULL,'Ativo',NULL,NULL,3,20)"
            ))
            connection.execute(text(
                f"INSERT INTO pesquisas VALUES ({PESQUISA_A},'Q A',NULL,1,{PROJETO_A},NULL,NULL),"
                f"({PESQUISA_B},'Q B',NULL,1,{PROJETO_B},NULL,NULL)"
            ))
            connection.execute(text(
                f"INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) VALUES ({SETOR_COM_HISTORICO},'Centro',200,50,'OPERACAO',NULL,{PESQUISA_A},2),"
                f"({SETOR_VAZIO},'Trem',100,50,'OPERACAO',NULL,{PESQUISA_A},NULL),"
                f"({SETOR_B},'Centro B',50,50,'OPERACAO',NULL,{PESQUISA_B},NULL)"
            ))
            connection.execute(text(
                f"INSERT INTO setor_agentes (setor_id, agente_id, ativo) VALUES"
                f" ({SETOR_COM_HISTORICO},2,1),({SETOR_VAZIO},2,1)"
            ))
            connection.execute(text(
                f"INSERT INTO setor_territorio_eleitoral (setor_id, territorio_eleitoral_id, criado_em)"
                f" VALUES ({SETOR_VAZIO},77,NULL)"
            ))

        self.db = self.Session()
        self.addCleanup(self.db.close)

    # --- utilidades ----------------------------------------------------------

    def _coleta(self, coleta_id, setor_id, pesquisa_id=PESQUISA_A):
        alvo = "NULL" if setor_id is None else str(setor_id)
        self.db.execute(text(
            f"INSERT INTO coletas VALUES ({coleta_id},{pesquisa_id},2,{alvo})"
        ))
        self.db.commit()

    def _excluir(self, setor_id, projeto_id=PROJETO_A, pesquisa_id=PESQUISA_A, user=None):
        return delete_setor_by_projeto_pesquisa(
            projeto_id=projeto_id,
            pesquisa_id=pesquisa_id,
            setor_id=setor_id,
            db=self.db,
            current_user=user or usuario(),
        )

    def _setor_existe(self, setor_id):
        return self.db.execute(
            text(f"SELECT COUNT(*) FROM setores WHERE id = {setor_id}")
        ).scalar() == 1

    def _setor_id_da_coleta(self, coleta_id):
        return self.db.execute(
            text(f"SELECT setor_id FROM coletas WHERE id = {coleta_id}")
        ).scalar()

    # --- setor sem historico -------------------------------------------------

    def test_setor_sem_coletas_e_excluido_normalmente(self):
        resposta = self._excluir(SETOR_VAZIO)

        self.assertIn("sucesso", resposta["message"])
        self.assertFalse(self._setor_existe(SETOR_VAZIO))

    def test_exclusao_sem_historico_nao_deixa_associacao_orfa(self):
        """FASE A e a composicao eleitoral seguem a politica de CASCADE ja existente."""
        self._excluir(SETOR_VAZIO)

        orfas_agentes = self.db.execute(text(
            f"SELECT COUNT(*) FROM setor_agentes WHERE setor_id = {SETOR_VAZIO}"
        )).scalar()
        orfas_territorio = self.db.execute(text(
            f"SELECT COUNT(*) FROM setor_territorio_eleitoral WHERE setor_id = {SETOR_VAZIO}"
        )).scalar()

        self.assertEqual(orfas_agentes, 0)
        self.assertEqual(orfas_territorio, 0)
        # O setor vizinho nao e afetado.
        self.assertEqual(
            self.db.execute(text(
                f"SELECT COUNT(*) FROM setor_agentes WHERE setor_id = {SETOR_COM_HISTORICO}"
            )).scalar(),
            1,
        )

    # --- setor com historico -------------------------------------------------

    def test_uma_coleta_vinculada_bloqueia_com_409(self):
        self._coleta(1001, SETOR_COM_HISTORICO)

        with self.assertRaises(HTTPException) as erro:
            self._excluir(SETOR_COM_HISTORICO)

        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("coletas vinculadas", erro.exception.detail)

    def test_setor_e_coleta_sobrevivem_ao_bloqueio(self):
        """O que importa nao e o 409: e o historico continuar de pe."""
        self._coleta(1001, SETOR_COM_HISTORICO)

        with self.assertRaises(HTTPException):
            self._excluir(SETOR_COM_HISTORICO)

        self.assertTrue(self._setor_existe(SETOR_COM_HISTORICO))
        self.assertEqual(
            self.db.execute(text("SELECT COUNT(*) FROM coletas WHERE id = 1001")).scalar(),
            1,
        )
        # A regressao original vivia exatamente aqui: virava NULL sem aviso.
        self.assertEqual(self._setor_id_da_coleta(1001), SETOR_COM_HISTORICO)

    def test_varias_coletas_bloqueiam_sem_remocao_parcial(self):
        for coleta_id in (1001, 1002, 1003):
            self._coleta(coleta_id, SETOR_COM_HISTORICO)

        with self.assertRaises(HTTPException) as erro:
            self._excluir(SETOR_COM_HISTORICO)

        self.assertEqual(erro.exception.status_code, 409)
        self.assertTrue(self._setor_existe(SETOR_COM_HISTORICO))
        for coleta_id in (1001, 1002, 1003):
            self.assertEqual(self._setor_id_da_coleta(coleta_id), SETOR_COM_HISTORICO)

    # --- limites da regra ----------------------------------------------------

    def test_coleta_historica_com_setor_id_nulo_nao_bloqueia(self):
        """Coleta antiga sem vinculo explicito nao segura o setor.

        Mesmo que o GPS caia dentro do poligono: nenhuma classificacao espacial
        participa desta decisao.
        """
        self._coleta(1001, None)

        resposta = self._excluir(SETOR_COM_HISTORICO)

        self.assertIn("sucesso", resposta["message"])
        self.assertFalse(self._setor_existe(SETOR_COM_HISTORICO))
        self.assertIsNone(self._setor_id_da_coleta(1001))

    def test_coleta_de_outro_setor_nao_bloqueia(self):
        self._coleta(1001, SETOR_VAZIO)

        resposta = self._excluir(SETOR_COM_HISTORICO)

        self.assertIn("sucesso", resposta["message"])
        self.assertFalse(self._setor_existe(SETOR_COM_HISTORICO))
        self.assertTrue(self._setor_existe(SETOR_VAZIO))
        self.assertEqual(self._setor_id_da_coleta(1001), SETOR_VAZIO)

    def test_setor_possui_coletas_olha_so_o_vinculo_explicito(self):
        self.assertFalse(crud.setor_possui_coletas(self.db, SETOR_COM_HISTORICO))

        self._coleta(1001, None)
        self.assertFalse(crud.setor_possui_coletas(self.db, SETOR_COM_HISTORICO))

        self._coleta(1002, SETOR_COM_HISTORICO)
        self.assertTrue(crud.setor_possui_coletas(self.db, SETOR_COM_HISTORICO))

    # --- multitenancy --------------------------------------------------------

    def test_tenant_cruzado_recebe_404_e_nao_409(self):
        """409 para outro tenant revelaria que o setor existe -- e que tem coleta."""
        self._coleta(9001, SETOR_B, pesquisa_id=PESQUISA_B)

        with self.assertRaises(HTTPException) as erro:
            self._excluir(SETOR_B, projeto_id=PROJETO_B, pesquisa_id=PESQUISA_B)

        self.assertEqual(erro.exception.status_code, 404)
        self.assertNotIn("coletas", erro.exception.detail)
        self.assertTrue(self._setor_existe(SETOR_B))
        self.assertEqual(self._setor_id_da_coleta(9001), SETOR_B)

    # --- corrida e sessao ----------------------------------------------------

    def test_vinculo_concorrente_vira_409_pela_FK(self):
        """Coleta vinculada DEPOIS do EXISTS: a FK barra e o commit e traduzido.

        Sem `passive_deletes` em `Setor.coletas` este caminho nunca aconteceria:
        o ORM zeraria o `setor_id` e o DELETE passaria feliz.
        """
        self._coleta(1001, SETOR_COM_HISTORICO)

        # Simula a corrida com fidelidade: a pre-checagem nao ve nada, o vinculo
        # aparece, o commit falha, e a reconsulta pos-rollback encontra a coleta.
        with patch.object(crud, "setor_possui_coletas", side_effect=[False, True]):
            with self.assertRaises(HTTPException) as erro:
                self._excluir(SETOR_COM_HISTORICO)

        self.assertEqual(erro.exception.status_code, 409)
        self.assertTrue(self._setor_existe(SETOR_COM_HISTORICO))
        self.assertEqual(self._setor_id_da_coleta(1001), SETOR_COM_HISTORICO)

    def test_sessao_continua_utilizavel_apos_o_conflito(self):
        self._coleta(1001, SETOR_COM_HISTORICO)

        with self.assertRaises(HTTPException):
            self._excluir(SETOR_COM_HISTORICO)

        # Sem o rollback a sessao ficaria em estado invalido e isto explodiria.
        self.assertEqual(self.db.query(models.Setor).count(), 3)
        resposta = self._excluir(SETOR_VAZIO)
        self.assertIn("sucesso", resposta["message"])

    # --- nao mascarar erro desconhecido --------------------------------------

    def test_detector_reconhece_apenas_a_FK_das_coletas(self):
        def falha(constraint):
            orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint))
            return IntegrityError("stmt", {}, orig)

        self.assertTrue(_conflito_coletas_do_setor(falha(FK_COLETAS_SETOR)))
        self.assertFalse(_conflito_coletas_do_setor(falha("uq_setor_agentes_setor_agente")))
        self.assertFalse(_conflito_coletas_do_setor(falha("fk_qualquer_outra")))

    def test_integrityerror_desconhecido_sobe_sem_virar_mensagem_de_negocio(self):
        outro = IntegrityError(
            "stmt", {}, SimpleNamespace(diag=SimpleNamespace(constraint_name="outra_coisa"))
        )

        with patch.object(self.db, "commit", side_effect=outro):
            with self.assertRaises(IntegrityError):
                self._excluir(SETOR_VAZIO)


if __name__ == "__main__":
    unittest.main()
