"""Canonicalizacao de respostas categoricas nos mapas (HOTFIX G.1).

Causa raiz comprovada em PROD: a engine dos mapas compara o valor persistido
com o texto da opcao LITERALMENTE. "ACACIO FAVACHO" != "Acacio Favacho" e
"BRANCO NULO" != "BRANCO/NULO", entao respostas validas somem do mapa.

Estes testes reproduzem o defeito com dados sinteticos ANTES do patch (devem
falhar) e passam a proteger a regra depois: para pergunta categorica nao
espontanea, a resposta e associada a opcao cadastrada por chave normalizada e
o valor reportavel e SEMPRE `Opcao.texto`.

Fixture SQLite espelhada na suite pytest dos mapas (que nao roda no runtime
oficial), com as funcoes PostGIS necessarias implementadas em Shapely.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-canonicalizacao-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.services import lideranca_analytics
from tests.acl_fixture import criar_tabelas_acl

ROTA_GEO = "/relatorios/pesquisas/{}/mapas/respostas-georreferenciadas/"
PESQUISA = 1000
VOTO = 100          # ESCOLHA_SIMPLES: Candidato A / Candidato B / BRANCO/NULO
ESPONTANEA = 101    # eh_resposta_espontanea
TEXTO = 102         # TEXTO livre
COLISAO = 103       # opcoes "A/B" e "A B" -> mesma chave normalizada


def usuario(user_id=1, company_id=10):
    return SimpleNamespace(
        id=user_id, company_id=company_id, ativo=True,
        perfil=SimpleNamespace(nome="Gerente"),
    )


class CanonicalizacaoTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(self.engine, "connect")
        def _espaciais(conexao, _):
            def geom(valor):
                if valor is None:
                    return None
                if isinstance(valor, bytes):
                    valor = valor.decode()
                return shapely_wkt.loads(str(valor).split(";", 1)[-1])

            conexao.create_function("AsEWKB", 1, lambda v: v)
            conexao.create_function("ST_GeomFromText", -1, lambda *a: a[0])
            conexao.create_function("ST_AsGeoJSON", 1, lambda v: json.dumps(mapping(geom(v))) if v else None)
            conexao.create_function("AsGeoJSON", 1, lambda v: json.dumps(mapping(geom(v))) if v else None)
            conexao.create_function(
                "ST_Covers", 2,
                lambda pol, pt: int(geom(pol).covers(geom(pt))) if pol and pt else 0,
            )
            conexao.create_function("ST_X", 1, lambda v: geom(v).x if v else None)
            conexao.create_function("ST_Y", 1, lambda v: geom(v).y if v else None)

        criar_tabelas_acl(self.engine)

        criar_tabelas_acl(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for ddl in (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT, is_active BOOLEAN, created_at DATETIME)",
                "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
                "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
                "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER)",
                "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN, papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}', ativo BOOLEAN, pesquisa_id INTEGER, aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL')",
                "CREATE TABLE opcoes (id INTEGER PRIMARY KEY, texto TEXT, ordem INTEGER, pergunta_id INTEGER, proxima_pergunta_id INTEGER)",
                "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER, company_id INTEGER, client_uuid TEXT, foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT, data_inicio_coleta DATETIME, data_fim_coleta DATETIME, localizacao_inicio TEXT, localizacao_fim TEXT, inconformidade_localizacao BOOLEAN, setor_id INTEGER)",
                "CREATE TABLE respostas (id INTEGER PRIMARY KEY, pergunta_id INTEGER, coleta_id INTEGER, valor_resposta TEXT)",
                "CREATE TABLE categorias_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT, nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
                "CREATE TABLE mapeamentos_resposta_espontanea (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER, chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN, criado_por_id INTEGER, atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME)",
                "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT DEFAULT 'OPERACAO' NOT NULL, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
                "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER, territorio_eleitoral_id INTEGER, criado_em DATETIME)",
                "CREATE TABLE territorio_eleitoral (id INTEGER PRIMARY KEY, base_eleitoral_id INTEGER, parent_id INTEGER, tipo TEXT, nome TEXT, nome_normalizado TEXT, municipio_id INTEGER, eleitorado_apto INTEGER)",
            ):
                c.execute(text(ddl))
            c.execute(text("INSERT INTO companies VALUES (10,'A',NULL,NULL,1,NULL)"))
            c.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL)"))
            c.execute(text("INSERT INTO usuarios VALUES (1,'g@a','G','x',1,1,10),(2,'a@a','A','x',1,2,10)"))
            c.execute(text("INSERT INTO projetos VALUES (100,'P',NULL,'Ativo',NULL,NULL,1,10)"))
            c.execute(text(f"INSERT INTO pesquisas VALUES ({PESQUISA},'Q',NULL,1,100,NULL,NULL)"))
            c.execute(text(f"""
                INSERT INTO perguntas (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria,
                    eh_resposta_espontanea, ativo, pesquisa_id) VALUES
                  ({VOTO},'Senador','ESCOLHA_SIMPLES',1,1,0,1,{PESQUISA}),
                  ({ESPONTANEA},'Espontanea','TEXTO',2,0,1,1,{PESQUISA}),
                  ({TEXTO},'Comentario','TEXTO',3,0,0,1,{PESQUISA}),
                  ({COLISAO},'Colisao','ESCOLHA_SIMPLES',4,0,0,1,{PESQUISA})
            """))
            c.execute(text(f"""
                INSERT INTO opcoes (id, texto, ordem, pergunta_id) VALUES
                  (1,'Acácio Favacho',1,{VOTO}),
                  (2,'Candidato B',2,{VOTO}),
                  (3,'BRANCO/NULO',3,{VOTO}),
                  (4,'A/B',1,{COLISAO}),
                  (5,'A B',2,{COLISAO})
            """))
            # Um setor analitico cobrindo todos os pontos.
            c.execute(text(f"""
                INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) VALUES
                  (11,'Setor X',0,0,'RELATORIO','POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))',{PESQUISA},NULL)
            """))
            inicio = datetime(2026, 1, 10, tzinfo=timezone.utc).isoformat()
            # 10 coletas: 4 x "Candidato A" em variacoes, 3 x B, 2 x branco em variacoes, 1 historico desconhecido
            votos = [
                "ACÁCIO FAVACHO", "Acácio Favacho", "acacio favacho", "ACACIO FAVACHO",
                "Candidato B", "CANDIDATO B", "Candidato B",
                "BRANCO NULO", "BRANCO/NULO",
                "VALOR HISTORICO X",
            ]
            for i, voto in enumerate(votos, start=1):
                c.execute(text(f"""
                    INSERT INTO coletas VALUES
                    ({i},{PESQUISA},2,10,'uuid-{i}',0,NULL,'ok','{inicio}','{inicio}','POINT ({i} {i})',NULL,0,NULL)
                """))
                c.execute(text("INSERT INTO respostas VALUES (:id,:p,:c,:v)"),
                          {"id": i, "p": VOTO, "c": i, "v": voto})
            # Espontanea mapeada: "Lula" -> categoria "PT"
            c.execute(text(f"INSERT INTO categorias_resposta_espontanea VALUES (1,{PESQUISA},'PT','pt',1,1,1,NULL,NULL)"))
            c.execute(text(f"INSERT INTO mapeamentos_resposta_espontanea VALUES (1,{PESQUISA},1,'lula','Lula',1,1,1,NULL,NULL)"))
            c.execute(text(f"INSERT INTO respostas VALUES (100,{ESPONTANEA},1,'LULA')"))
            c.execute(text(f"INSERT INTO respostas VALUES (101,{ESPONTANEA},2,'Ciro')"))
            c.execute(text(f"INSERT INTO respostas VALUES (102,{TEXTO},1,'ACÁCIO FAVACHO')"))
            c.execute(text(f"INSERT INTO respostas VALUES (103,{COLISAO},1,'a b')"))

        self.db = self.Session()
        self.addCleanup(self.db.close)
        app = FastAPI()
        app.include_router(relatorios.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: usuario()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def pergunta(self, pid):
        return self.db.get(crud.models.Pergunta, pid)

    def reportavel(self, pid, valor):
        return crud.resolve_reportable_response_value(
            pergunta=self.pergunta(pid), valor_resposta=valor, spontaneous_mapping={"lula": "PT"}
        )

    def geo(self, **payload):
        corpo = {"pergunta_id": VOTO, "valores": ["Acácio Favacho"]}
        corpo.update(payload)
        return self.client.post(ROTA_GEO.format(PESQUISA), json=corpo)

    def preview(self, **payload):
        corpo = {"tipo_mapa": "RESULTADO_SETOR", "pergunta_id": VOTO}
        corpo.update(payload)
        return crud.get_mapa_preview(
            self.db, PESQUISA, usuario(), schemas.MapaPreviewRequest(**corpo)
        )

    # --- helper (as tres variacoes comprovadas em PROD) ---------------------

    def test_case_diferente_devolve_rotulo_canonico(self):
        self.assertEqual(self.reportavel(VOTO, "ACÁCIO FAVACHO"), "Acácio Favacho")

    def test_acentuacao_ausente_devolve_rotulo_canonico(self):
        # Regra deliberada: a chave ignora acentos, o ROTULO devolvido nao.
        self.assertEqual(self.reportavel(VOTO, "ACACIO FAVACHO"), "Acácio Favacho")

    def test_separador_diferente_devolve_rotulo_canonico(self):
        self.assertEqual(self.reportavel(VOTO, "BRANCO NULO"), "BRANCO/NULO")

    def test_valor_ja_canonico_permanece(self):
        self.assertEqual(self.reportavel(VOTO, "Acácio Favacho"), "Acácio Favacho")
        self.assertEqual(self.reportavel(VOTO, "BRANCO/NULO"), "BRANCO/NULO")

    def test_valor_sem_opcao_preserva_o_bruto(self):
        # "Outros" e responsabilidade do relatorio, nunca do helper.
        self.assertEqual(self.reportavel(VOTO, "VALOR HISTORICO X"), "VALOR HISTORICO X")

    def test_multipla_escolha_nao_e_canonicalizada(self):
        # Fora do escopo do hotfix: lista persistida segue como esta.
        self.db.execute(text(f"UPDATE perguntas SET tipo_pergunta='MULTIPLA_ESCOLHA' WHERE id={COLISAO}"))
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(self.reportavel(COLISAO, '["a b"]'), '["a b"]')
        self.assertEqual(self.reportavel(COLISAO, "a b"), "a b")

    def test_texto_livre_nao_e_canonicalizado(self):
        self.assertEqual(self.reportavel(TEXTO, "ACÁCIO FAVACHO"), "ACÁCIO FAVACHO")

    def test_espontanea_segue_o_mapeamento_e_nao_a_opcao(self):
        self.assertEqual(self.reportavel(ESPONTANEA, "LULA"), "PT")
        self.assertEqual(self.reportavel(ESPONTANEA, "Ciro"), crud.NAO_CATEGORIZADA)

    def test_colisao_de_chave_nao_associa_arbitrariamente(self):
        """'A/B' e 'A B' viram a mesma chave: o valor fica como esta."""
        self.assertEqual(self.reportavel(COLISAO, "a b"), "a b")
        self.assertEqual(self.reportavel(COLISAO, "A/B"), "A/B")
        self.assertEqual(self.reportavel(COLISAO, "A B"), "A B")

    def test_mapa_de_opcoes_e_calculado_uma_vez_por_pergunta(self):
        pergunta = self.pergunta(VOTO)
        primeiro = crud.mapa_opcoes_canonicas(pergunta)
        segundo = crud.mapa_opcoes_canonicas(pergunta)
        self.assertIs(primeiro, segundo)
        self.assertEqual(primeiro["acacio favacho"], "Acácio Favacho")
        self.assertEqual(primeiro["branco nulo"], "BRANCO/NULO")

    # --- Respostas Georreferenciadas -----------------------------------------

    def test_geo_filtro_pelo_rotulo_canonico_encontra_as_variacoes(self):
        corpo = self.geo().json()
        self.assertEqual(corpo["resumo"]["total_universo"], 10)
        self.assertEqual(corpo["resumo"]["total_filtrado"], 4, corpo)
        self.assertEqual(corpo["resumo"]["total_com_coordenada"], 4)
        self.assertEqual([c["valor"] for c in corpo["categorias"]], ["Acácio Favacho"])
        self.assertEqual(corpo["categorias"][0]["total"], 4)
        self.assertEqual({p["valor"] for p in corpo["pontos"]}, {"Acácio Favacho"})

    def test_geo_branco_nulo_com_barra(self):
        corpo = self.geo(valores=["BRANCO/NULO"]).json()
        self.assertEqual(corpo["resumo"]["total_filtrado"], 2)
        self.assertEqual([c["valor"] for c in corpo["categorias"]], ["BRANCO/NULO"])

    def test_geo_outros_off_e_on(self):
        off = self.geo().json()
        self.assertEqual(off["resumo"]["total_filtrado"], 4)
        self.assertEqual(len(off["pontos"]), 4)

        on = self.geo(agrupar_nao_selecionadas=True).json()
        por_valor = {c["valor"]: c["total"] for c in on["categorias"]}
        self.assertEqual(por_valor["Acácio Favacho"], 4)
        # Outros = B(3) + BRANCO/NULO(2) + historico desconhecido(1) = 6
        self.assertEqual(por_valor["Outros"], 6)
        self.assertEqual(on["resumo"]["total_filtrado"], 10)
        self.assertEqual(len(on["pontos"]), 10)
        self.assertNotIn("ACÁCIO FAVACHO", por_valor)

    # --- Resultado por Setor ---------------------------------------------------

    def test_resultado_setor_conta_as_variacoes_e_preserva_o_denominador(self):
        dados = self.preview(resposta="Acácio Favacho")["dados"]
        setor = next(d for d in dados if d["setor_id"] == 11)
        self.assertEqual(setor["total_respostas_validas"], 10, "denominador inalterado")
        self.assertEqual(setor["valor"], 4)
        self.assertEqual(setor["percentual"], 40.0)

    def test_resultado_setor_branco_nulo(self):
        setor = next(d for d in self.preview(resposta="BRANCO/NULO")["dados"] if d["setor_id"] == 11)
        self.assertEqual(setor["valor"], 2)
        self.assertEqual(setor["percentual"], 20.0)

    # --- Lideranca por Setor / Comparativo -------------------------------------

    def test_lideranca_setor_usa_rotulos_canonicos_sem_duplicar(self):
        setor = next(
            d for d in self.preview(tipo_mapa="LIDERANCA_SETOR")["dados"] if d["setor_id"] == 11
        )
        self.assertEqual(setor["lider"], "Acácio Favacho")
        self.assertEqual(setor["segundo"], "Candidato B")
        self.assertEqual(setor["lider_percentual"], 40.0)
        self.assertEqual(setor["segundo_percentual"], 30.0)
        self.assertEqual(setor["total_respostas_validas"], 10)

    def test_lideranca_setor_empate_continua_detectado(self):
        # Duas variacoes de B a mais: 4 A x 4 B... acrescenta 1 "candidato b".
        self.db.execute(text(f"INSERT INTO respostas VALUES (200,{VOTO},10,'candidato b')"))
        self.db.commit()
        setor = next(
            d for d in self.preview(tipo_mapa="LIDERANCA_SETOR")["dados"] if d["setor_id"] == 11
        )
        self.assertEqual(setor["lider"], "Empate")

    def test_comparativo_duas_respostas_normalizadas(self):
        a = next(d for d in self.preview(resposta="Acácio Favacho")["dados"] if d["setor_id"] == 11)
        b = next(d for d in self.preview(resposta="Candidato B")["dados"] if d["setor_id"] == 11)
        self.assertEqual((a["valor"], b["valor"]), (4, 3))
        self.assertEqual((a["percentual"], b["percentual"]), (40.0, 30.0))

    # --- Lideranca politica (analytics) ----------------------------------------

    def test_lideranca_analytics_mede_pelo_rotulo_canonico(self):
        valores = lideranca_analytics._valores_reportaveis(
            self.db,
            pesquisa_id=PESQUISA,
            coleta_ids=[1, 2, 3, 4, 5],
            perguntas_por_id={VOTO: self.pergunta(VOTO)},
            spontaneous_mapping={},
        )
        medido = lideranca_analytics._medir([1, 2, 3, 4, 5], valores, VOTO, {"Acácio Favacho"})
        self.assertEqual(medido.respostas_alvo, 4)
        self.assertEqual(medido.base_valida, 5)


if __name__ == "__main__":
    unittest.main()
