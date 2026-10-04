"""API analitica da Apuracao TSE e paineis multitenant.

A aplicacao de teste monta apenas o router da apuracao, com `get_db` e
`get_current_user` substituidos. Os dados vem da ingestao das fixtures
SIMULADO_* (mesma base de test_tse_persistencia).
"""

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-only-tse-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import apuracao_tse
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.db.models_tse import TseEleicao
from pesquisa360.services.tse.normalization import OFICIAL

from tests.test_tse_persistencia import CANDIDATO, _TseFixture

ANULADO = "41609564"


class _ApiFixture(_TseFixture):
    def setUp(self):
        super().setUp()
        for tabela in ("apuracao_painel_itens", "apuracao_paineis", "usuarios", "perfis",
                       "companies"):
            self.session.execute(models.Base.metadata.tables[tabela].delete())
        self.session.commit()
        self.ingerir()
        self.eleicao_id = self.eleicao().id

        perfis = {nome: models.Perfil(nome=nome) for nome in ("Gerente", "Cliente", "Agente")}
        empresas = {"A": models.Company(id=10, name="Empresa A", cnpj="11111111000111"),
                    "B": models.Company(id=20, name="Empresa B", cnpj="22222222000122")}
        self.session.add_all([*perfis.values(), *empresas.values()])
        self.session.flush()

        def usuario(email, perfil, empresa):
            u = models.Usuario(nome=email, email=email, senha_hash="x", ativo=True,
                               perfil_id=perfis[perfil].id,
                               company_id=empresas[empresa].id if empresa else None)
            self.session.add(u)
            return u

        self.gerente_a = usuario("gerente.a@teste", "Gerente", "A")
        self.gerente_b = usuario("gerente.b@teste", "Gerente", "B")
        self.cliente_a = usuario("cliente.a@teste", "Cliente", "A")
        self.agente_a = usuario("agente.a@teste", "Agente", "A")
        self.session.commit()

        app = FastAPI()
        app.include_router(apuracao_tse.router)
        self.atual = self.gerente_a
        app.dependency_overrides[get_db] = lambda: self.session
        app.dependency_overrides[get_current_user] = lambda: self.atual
        self.client = TestClient(app)
        self.q = {"uf": "ap"}

    def como(self, usuario):
        self.atual = usuario
        return self.client

    def corpo_painel(self, **extra):
        return {"nome": "Geral AP", "eleicao_id": self.eleicao_id, "uf": "AP", "itens": [
            {"tipo": "CARGO", "cargo_codigo": "6"},
            {"tipo": "CANDIDATO", "cargo_codigo": "0006", "sqcand": CANDIDATO},
            {"tipo": "NOMINATA", "cargo_codigo": "0006", "federacao_numero": "100"},
        ], **extra}


class LeituraTseTest(_ApiFixture):
    def test_lista_eleicoes_com_resultado(self):
        r = self.client.get("/apuracao/tse/eleicoes")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)       # so a estadual tem totalizacao
        eleicao = r.json()[0]
        self.assertEqual((eleicao["origem"], eleicao["codigo_eleicao"], eleicao["ufs"]),
                         ("SIMULADO", "21272", ["ap"]))
        self.assertEqual(eleicao["cargos"], [{"codigo": "0006", "nome": "Deputado Federal"}])
        self.assertEqual(self.client.get("/apuracao/tse/eleicoes?origem=OFICIAL").json(), [])

    def test_resumo(self):
        r = self.client.get(f"/apuracao/tse/eleicoes/{self.eleicao_id}/resumo", params=self.q)
        self.assertEqual(r.status_code, 200)
        corpo = r.json()
        self.assertEqual((corpo["origem"], corpo["uf"]), ("SIMULADO", "ap"))
        total = corpo["totalizacao"]
        self.assertEqual((total["secoes_totalizadas"], total["secoes_total"],
                          total["percentual_secoes"], total["totalizacao_final"],
                          total["idg"]), (2177, 2177, 100.0, True, "176223611"))
        self.assertTrue(corpo["ultima_atualizacao"].endswith("Z"))
        self.assertEqual(corpo["cargos"][0]["vagas"], 8)

    def test_origem_divergente_e_404(self):
        base = f"/apuracao/tse/eleicoes/{self.eleicao_id}"
        self.assertEqual(self.client.get(f"{base}/resumo?uf=ap&origem=SIMULADO").status_code, 200)
        self.assertEqual(self.client.get(f"{base}/resumo?uf=ap&origem=OFICIAL").status_code, 404)
        self.assertEqual(self.client.get(f"{base}/resumo?uf=ap&origem=TESTE").status_code, 422)

    def test_resultado_por_cargo_uf_municipio_zona(self):
        base = f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006"
        uf = self.client.get(base, params=self.q).json()
        self.assertEqual((uf["cargo"]["nome"], uf["total_candidatos"]), ("Deputado Federal", 176))
        primeiro = uf["candidatos"][0]
        self.assertEqual((primeiro["posicao"], primeiro["sqcand"], primeiro["votos"],
                          primeiro["situacao"], primeiro["eleito"]),
                         (1, CANDIDATO, 3115, "Eleito por média", True))
        self.assertEqual(primeiro["federacao"]["numero"], "100")
        votos = [c["votos"] for c in uf["candidatos"]]
        self.assertEqual(votos, sorted(votos, reverse=True))
        self.assertNotIn("payload_json", str(uf))

        mun = self.client.get(base, params={**self.q, "municipio": "06050"}).json()
        self.assertEqual((mun["abrangencia"]["tipo"], mun["abrangencia"]["municipio_nome"]),
                         ("MUNICIPIO", "MACAPÁ"))
        zona = self.client.get(base, params={**self.q, "municipio": "06050",
                                             "zona": "0002", "limite": 5}).json()
        self.assertEqual((zona["abrangencia"]["zona"], len(zona["candidatos"])), ("0002", 5))
        alvo = next(c for c in mun["candidatos"] if c["sqcand"] == CANDIDATO)
        self.assertEqual(alvo["votos"], 1612)

    def test_status_oficial_nao_e_inferido(self):
        uf = self.client.get(f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006",
                             params=self.q).json()
        anulado = next(c for c in uf["candidatos"] if c["sqcand"] == ANULADO)
        self.assertEqual((anulado["destinacao_voto"], anulado["voto_valido"], anulado["eleito"]),
                         ("Anulado sub judice", False, False))
        eleitos = [c for c in uf["candidatos"] if c["eleito"]]
        self.assertEqual(len(eleitos), 8)           # exatamente o que o TSE marcou
        self.assertTrue(all(c["situacao"].startswith("Eleito") for c in eleitos))

    def test_abrangencia_ou_cargo_inexistente(self):
        base = f"/apuracao/tse/eleicoes/{self.eleicao_id}"
        self.assertEqual(self.client.get(f"{base}/cargos/0006?uf=sp").status_code, 404)
        self.assertEqual(self.client.get(f"{base}/cargos/0003?uf=ap").status_code, 404)
        self.assertEqual(self.client.get(f"{base}/cargos/0006?uf=ap&zona=0002").status_code, 422)
        self.assertEqual(self.client.get(f"{base}/cargos/0006?uf=ap&municipio=6050").status_code,
                         422)
        self.assertEqual(self.client.get("/apuracao/tse/eleicoes/999999/resumo?uf=ap").status_code,
                         404)

    def test_nominatas(self):
        r = self.client.get(f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006/nominatas",
                            params=self.q).json()
        nominatas = r["nominatas"]
        totais = [n["total"] for n in nominatas]
        self.assertEqual(totais, sorted(totais, reverse=True))
        federacao = next(n for n in nominatas if n["tipo"] == "FEDERACAO" and n["numero"] == "100")
        self.assertEqual({p["numero"] for p in federacao["partidos"]}, {"69", "84"})
        self.assertEqual([c["posicao"] for c in federacao["candidatos"]],
                         list(range(1, len(federacao["candidatos"]) + 1)))
        self.assertEqual(federacao["total"],
                         federacao["votos_nominais_validos"] + federacao["votos_legenda"])
        self.assertTrue(all(c["federacao"]["numero"] == "100" for c in federacao["candidatos"]))
        self.assertEqual(sum(len(n["candidatos"]) for n in nominatas), 176)
        self.assertEqual(sum(n["votos_legenda"] for n in nominatas), 66579)
        self.assertEqual(sum(n["votos_nominais_validos"] for n in nominatas), 378662)
        self.assertNotIn("vagas_previstas", str(r))

    def test_candidato(self):
        r = self.client.get(f"/apuracao/tse/candidatos/{CANDIDATO}",
                            params={"eleicao_id": self.eleicao_id}).json()
        self.assertEqual((r["candidato"]["numero"], r["candidato"]["cargo"]["codigo"],
                          r["candidato"]["partido"]["sigla"]), ("6903", "0006", "P 9985"))
        self.assertEqual((r["consolidado"]["votos"], r["consolidado"]["posicao"],
                          r["consolidado"]["percentual"]), (3115, 1, 0.59))
        self.assertEqual(r["totalizacao"]["secoes_totalizadas"], 2177)
        self.assertEqual(self.client.get("/apuracao/tse/candidatos/0",
                                         params={"eleicao_id": self.eleicao_id}).status_code, 404)
        self.assertEqual(self.client.get(f"/apuracao/tse/candidatos/{CANDIDATO}").status_code, 422)

    def test_territorio_por_municipio_e_zona(self):
        url = f"/apuracao/tse/candidatos/{CANDIDATO}/territorio"
        mun = self.client.get(url, params={"eleicao_id": self.eleicao_id,
                                           "group_by": "municipio"}).json()
        self.assertEqual((mun["total_votos_uf"], len(mun["itens"])), (3115, 1))
        self.assertEqual((mun["itens"][0]["municipio_nome"], mun["itens"][0]["votos"],
                          mun["itens"][0]["percentual_dos_votos_do_candidato"]),
                         ("MACAPÁ", 1612, 51.75))
        # So Macapa foi ingerido: a soma parcial nao e apresentada como conferida.
        self.assertEqual(mun["reconciliacao"]["status"], "INSUFFICIENT_DATA")
        self.assertFalse(mun["secao_disponivel"])

        zonas = self.client.get(url, params={"eleicao_id": self.eleicao_id, "group_by": "zona",
                                             "municipio": "06050"}).json()
        self.assertEqual([(z["zona"], z["votos"]) for z in zonas["itens"]],
                         [("0002", 676), ("0014", 509), ("0010", 427)])
        self.assertEqual(zonas["reconciliacao"]["status"], "CONSISTENT")
        self.assertEqual(self.client.get(url, params={"eleicao_id": self.eleicao_id,
                                                      "group_by": "secao"}).status_code, 422)

    def test_evolucao(self):
        r = self.client.get(f"/apuracao/tse/candidatos/{CANDIDATO}/evolucao",
                            params={"eleicao_id": self.eleicao_id}).json()
        self.assertEqual(len(r["pontos"]), 1)
        ponto = r["pontos"][0]
        self.assertEqual((ponto["votos"], ponto["secoes_totalizadas"], ponto["idg"]),
                         (3115, 2177, "176223611"))
        self.assertTrue(ponto["timestamp"].endswith("Z"))
        zona = self.client.get(f"/apuracao/tse/candidatos/{CANDIDATO}/evolucao",
                               params={"eleicao_id": self.eleicao_id, "municipio": "06050",
                                       "zona": "0014"}).json()
        self.assertEqual(zona["pontos"][0]["votos"], 509)

    def test_permissao_de_leitura(self):
        url = f"/apuracao/tse/eleicoes/{self.eleicao_id}/resumo?uf=ap"
        self.assertEqual(self.como(self.cliente_a).get(url).status_code, 200)
        self.assertEqual(self.como(self.agente_a).get(url).status_code, 403)

    def test_dados_tse_sao_os_mesmos_para_qualquer_empresa(self):
        url = f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006?uf=ap"
        self.assertEqual(self.como(self.gerente_a).get(url).json(),
                         self.como(self.gerente_b).get(url).json())


class PaineisMultitenantTest(_ApiFixture):
    def criar(self, usuario=None, **extra):
        r = self.como(usuario or self.gerente_a).post("/apuracao/paineis",
                                                      json=self.corpo_painel(**extra))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_cria_com_tenant_do_usuario_e_ordem_da_lista(self):
        painel = self.criar()
        self.assertEqual((painel["origem"], painel["pleito"], painel["codigo_eleicao"],
                          painel["uf"], painel["eleicao_id"]),
                         ("SIMULADO", "17801", "21272", "ap", self.eleicao_id))
        self.assertEqual([(i["tipo"], i["cargo_codigo"], i["ordem"]) for i in painel["itens"]],
                         [("CARGO", "0006", 0), ("CANDIDATO", "0006", 1), ("NOMINATA", "0006", 2)])
        self.assertNotIn("company_id", painel)
        gravado = self.session.get(models.ApuracaoPainel, painel["id"])
        self.assertEqual((gravado.company_id, gravado.criado_por_id),
                         (10, self.gerente_a.id))

    def test_company_id_no_corpo_e_recusado(self):
        r = self.client.post("/apuracao/paineis", json=self.corpo_painel(company_id=20))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.session.query(models.ApuracaoPainel).count(), 0)

    def test_empresa_b_nao_ve_nem_acessa_painel_da_a(self):
        painel = self.criar()
        self.assertEqual([p["id"] for p in self.como(self.gerente_a).get("/apuracao/paineis").json()],
                         [painel["id"]])
        b = self.como(self.gerente_b)
        self.assertEqual(b.get("/apuracao/paineis").json(), [])
        url = f"/apuracao/paineis/{painel['id']}"
        self.assertEqual(b.get(url).status_code, 404)
        self.assertEqual(b.put(url, json=self.corpo_painel(nome="Invadido")).status_code, 404)
        self.assertEqual(b.delete(url).status_code, 404)
        self.session.expire_all()
        self.assertEqual(self.session.get(models.ApuracaoPainel, painel["id"]).nome, "Geral AP")

    def test_cada_empresa_tem_os_seus(self):
        a = self.criar()
        b = self.criar(self.gerente_b, nome="Painel B")
        self.assertEqual([p["nome"] for p in self.como(self.gerente_a).get("/apuracao/paineis").json()],
                         ["Geral AP"])
        self.assertEqual([p["nome"] for p in self.como(self.gerente_b).get("/apuracao/paineis").json()],
                         ["Painel B"])
        self.assertNotEqual(a["id"], b["id"])

    def test_edita_reordena_e_exclui(self):
        painel = self.criar()
        url = f"/apuracao/paineis/{painel['id']}"
        corpo = self.corpo_painel(nome="Nominata Federal", descricao="x")
        corpo["itens"] = list(reversed(corpo["itens"]))[:2]
        r = self.client.put(url, json=corpo)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["nome"], r.json()["total_itens"]), ("Nominata Federal", 2))
        self.assertEqual([i["tipo"] for i in r.json()["itens"]], ["NOMINATA", "CANDIDATO"])
        self.assertEqual(self.session.query(models.ApuracaoPainelItem).count(), 2)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.session.query(models.ApuracaoPainelItem).count(), 0)

    def test_validacoes(self):
        def post(**extra):
            return self.client.post("/apuracao/paineis", json=self.corpo_painel(**extra))

        self.assertEqual(post(nome="   ").status_code, 422)
        self.assertEqual(post(eleicao_id=999999).status_code, 422)
        self.assertEqual(post(itens=[{"tipo": "CANDIDATO", "cargo_codigo": "0006"}]).status_code, 422)
        self.assertEqual(post(itens=[{"tipo": "CARGO", "cargo_codigo": "0006",
                                      "sqcand": "1"}]).status_code, 422)
        self.assertEqual(post(itens=[{"tipo": "SECAO", "cargo_codigo": "0006"}]).status_code, 422)

    def test_cliente_le_mas_nao_escreve_e_agente_nao_acessa(self):
        painel = self.criar()
        cliente = self.como(self.cliente_a)
        self.assertEqual(cliente.get(f"/apuracao/paineis/{painel['id']}").status_code, 200)
        self.assertEqual(cliente.post("/apuracao/paineis", json=self.corpo_painel()).status_code, 403)
        self.assertEqual(cliente.delete(f"/apuracao/paineis/{painel['id']}").status_code, 403)
        self.assertEqual(self.como(self.agente_a).get("/apuracao/paineis").status_code, 403)

    def test_painel_guarda_a_origem_da_eleicao(self):
        painel = self.criar()
        oficial = TseEleicao(origem=OFICIAL, ambiente="oficial", ciclo="ele2026", pleito="3220",
                             codigo_eleicao="6259", nome="Oficial", turno=1)
        self.session.add(oficial)
        self.session.commit()
        r = self.client.put(f"/apuracao/paineis/{painel['id']}",
                            json=self.corpo_painel(eleicao_id=oficial.id))
        self.assertEqual((r.json()["origem"], r.json()["pleito"], r.json()["eleicao_id"]),
                         ("OFICIAL", "3220", oficial.id))
