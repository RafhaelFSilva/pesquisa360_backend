"""API analitica da Apuracao TSE e paineis multitenant.

A aplicacao de teste monta apenas o router da apuracao, com `get_db` e
`get_current_user` substituidos. Os dados vem da ingestao das fixtures
SIMULADO_* (mesma base de test_tse_persistencia).
"""

import copy
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

from tests.test_tse_persistencia import CANDIDATO, DADOS, ESCOPO, _TseFixture

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


class FiltrosTerritoriaisTest(_ApiFixture):
    """Municipio e zona recalculam a apuracao inteira a partir do EA20 do recorte.

    Deputado Estadual (0007) e montado a partir dos arquivos do Federal: mesmo
    contrato, `sqcand` prefixado com "7" e os votos dobrados, para que um filtro
    que confundisse os cargos fosse detectado.
    """

    FEDERACAO = "100"

    def setUp(self):
        super().setUp()
        for url, doc in list(self.tse.arquivos.items()):
            if "-c0006-" not in url:
                continue
            estadual = copy.deepcopy(doc)
            cargo = estadual["carg"][0]
            cargo.update({"cd": "7", "nmn": "Deputado Estadual", "nv": "24"})
            for agr in cargo["agr"]:
                for par in agr["par"]:
                    par["tvtl"] = str(int(par["tvtl"]) * 2)
                    for cand in par["cand"]:
                        cand["sqcand"] = "7" + cand["sqcand"]
                        cand["vap"] = str(int(cand["vap"]) * 2)
            self.tse.arquivos[url.replace("-c0006-", "-c0007-")] = estadual
        # O EA11 simulado ja declara o cargo 7 na eleicao estadual.
        self.ingerir(type(ESCOPO)(**{**ESCOPO.__dict__, "cargos": ("0006", "0007")}))
        self.base = f"/apuracao/tse/eleicoes/{self.eleicao_id}"

    def nominatas(self, cargo="0006", **filtro):
        r = self.client.get(f"{self.base}/cargos/{cargo}/nominatas", params={"uf": "ap", **filtro})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def federacao(self, corpo):
        return next(n for n in corpo["nominatas"]
                    if n["tipo"] == "FEDERACAO" and n["numero"] == self.FEDERACAO)

    def votos(self, corpo, sqcand=CANDIDATO):
        return next(c["votos"] for n in corpo["nominatas"] for c in n["candidatos"]
                    if c["sqcand"] == sqcand)

    # 1 -------------------------------------------------------------- sem filtros
    def test_sem_filtros_o_contrato_e_os_numeros_nao_mudam(self):
        uf = self.nominatas()
        self.assertEqual(sorted(uf), ["abrangencia", "cargo", "eleicao", "nominatas", "origem",
                                      "totalizacao"])
        self.assertEqual(sorted(uf["nominatas"][0]), [
            "candidatos", "nome", "numero", "partidos", "sigla", "tipo", "total",
            "votos_legenda", "votos_nominais", "votos_nominais_validos"])
        self.assertEqual((uf["abrangencia"]["tipo"], self.votos(uf)), ("UF", 3115))
        self.assertEqual(sum(n["votos_nominais_validos"] for n in uf["nominatas"]), 378662)
        self.assertEqual(sum(n["votos_legenda"] for n in uf["nominatas"]), 66579)
        # Filtro vazio nao e "sem filtro": o formato e validado (o Web omite o parametro).
        self.assertEqual(self.client.get(
            f"{self.base}/cargos/0006/nominatas",
            params={"uf": "ap", "municipio": ""}).status_code, 422)

    # 2, 3 ------------------------------------------------------ municipio e zona
    def test_municipio_recalcula_pelo_ea20_do_municipio(self):
        uf, mun = self.nominatas(), self.nominatas(municipio="06050")
        self.assertEqual((mun["abrangencia"]["tipo"], mun["abrangencia"]["municipio_codigo"],
                          mun["abrangencia"]["municipio_nome"]), ("MUNICIPIO", "06050", "MACAPÁ"))
        self.assertEqual(self.votos(mun), 1612)
        validos = sum(n["votos_nominais_validos"] for n in mun["nominatas"])
        self.assertEqual(validos, mun["totalizacao"]["votos_nominais"])
        self.assertLess(validos, sum(n["votos_nominais_validos"] for n in uf["nominatas"]))
        self.assertEqual(sum(n["votos_legenda"] for n in mun["nominatas"]),
                         mun["totalizacao"]["votos_legenda"])

    def test_zona_recalcula_pelo_ea20_da_zona(self):
        mun = self.nominatas(municipio="06050")
        zonas = {z: self.nominatas(municipio="06050", zona=z) for z in ("0002", "0010", "0014")}
        self.assertEqual({z: self.votos(c) for z, c in zonas.items()},
                         {"0002": 676, "0010": 427, "0014": 509})
        for zona, corpo in zonas.items():
            self.assertEqual((corpo["abrangencia"]["tipo"], corpo["abrangencia"]["zona"],
                              corpo["abrangencia"]["municipio_codigo"]), ("ZONA", zona, "06050"))
            self.assertEqual(sum(n["votos_nominais_validos"] for n in corpo["nominatas"]),
                             corpo["totalizacao"]["votos_nominais"])
        # As tres zonas somam o municipio: nominal, legenda e total da nominata.
        for campo in ("votos_nominais_validos", "votos_legenda", "total"):
            self.assertEqual(sum(self.federacao(c)[campo] for c in zonas.values()),
                             self.federacao(mun)[campo], campo)

    # 9-13 ------------------------------- nominal, legenda, total, %, ranking
    def test_nominata_do_recorte_tem_totais_percentuais_e_ranking_proprios(self):
        uf = self.federacao(self.nominatas())
        zona_corpo = self.nominatas(municipio="06050", zona="0002")
        zona = self.federacao(zona_corpo)
        self.assertEqual(zona["total"], zona["votos_nominais_validos"] + zona["votos_legenda"])
        self.assertLess(zona["votos_legenda"], uf["votos_legenda"])
        self.assertLess(zona["total"], uf["total"])
        votos = [c["votos"] for c in zona["candidatos"]]
        self.assertEqual(votos, sorted(votos, reverse=True))
        self.assertEqual([c["posicao"] for c in zona["candidatos"]],
                         list(range(1, len(votos) + 1)))
        # Percentual e o do arquivo da zona, nao o da UF.
        alvo_uf = next(c for c in uf["candidatos"] if c["sqcand"] == CANDIDATO)
        alvo_zona = next(c for c in zona["candidatos"] if c["sqcand"] == CANDIDATO)
        bruto = self.tse.arquivos[f"{DADOS}/ap/ap06050-z0002-c0006-e021272-u.json"]
        esperado = next(c for a in bruto["carg"][0]["agr"] for p in a["par"] for c in p["cand"]
                        if c["sqcand"] == CANDIDATO)
        self.assertEqual(alvo_zona["percentual"], float(esperado["pvap"].replace(",", ".")))
        self.assertNotEqual(alvo_zona["percentual"], alvo_uf["percentual"])
        # O ranking de partidos/federacoes tambem e o do recorte.
        totais = [n["total"] for n in zona_corpo["nominatas"]]
        self.assertEqual(totais, sorted(totais, reverse=True))
        self.assertNotEqual([n["sigla"] for n in zona_corpo["nominatas"]],
                            [n["sigla"] for n in self.nominatas()["nominatas"]])

    # 14 -------------------------------------------------------------- progresso
    def test_progresso_usa_o_universo_do_recorte(self):
        def secoes(**filtro):
            t = self.nominatas(**filtro)["totalizacao"]
            return t["secoes_totalizadas"], t["secoes_total"], t["percentual_secoes"], t["idg"]

        self.assertEqual(secoes(), (2177, 2177, 100.0, "176223611"))
        self.assertEqual(secoes(municipio="06050"), (1071, 1071, 100.0, "176248737"))
        self.assertEqual(secoes(municipio="06050", zona="0002"), (395, 395, 100.0, "176247751"))
        self.assertEqual(secoes(municipio="06050", zona="0010")[:2], (255, 255))
        self.assertEqual(secoes(municipio="06050", zona="0014")[:2], (421, 421))

    def test_progresso_parcial_da_zona_nao_usa_denominador_estadual(self):
        url = f"{DADOS}/ap/ap06050-z0014-c0006-e021272-u.json"
        self.tse.arquivos = copy.deepcopy(self.tse.arquivos)
        self.tse.arquivos[url].update({"and": "p", "tf": "n"})
        self.tse.arquivos[url]["s"]["st"] = "42"
        self.ingerir(type(ESCOPO)(**{**ESCOPO.__dict__, "force": True}))
        t = self.nominatas(municipio="06050", zona="0014")["totalizacao"]
        self.assertEqual((t["secoes_totalizadas"], t["secoes_total"], t["andamento"],
                          t["totalizacao_final"]), (42, 421, "p", False))
        self.assertEqual(t["percentual_secoes"], 9.98)
        self.assertEqual(self.nominatas()["totalizacao"]["secoes_total"], 2177)

    # 8 ------------------------------------------------------ federal e estadual
    def test_filtros_funcionam_para_federal_e_estadual(self):
        for filtro in ({}, {"municipio": "06050"}, {"municipio": "06050", "zona": "0010"}):
            federal, estadual = self.nominatas("0006", **filtro), self.nominatas("0007", **filtro)
            self.assertEqual((federal["cargo"]["codigo"], estadual["cargo"]["codigo"]),
                             ("0006", "0007"))
            self.assertEqual(estadual["abrangencia"], federal["abrangencia"])
            self.assertEqual(self.votos(estadual, "7" + CANDIDATO), 2 * self.votos(federal))
            self.assertTrue(all(c["sqcand"].startswith("7")
                                for n in estadual["nominatas"] for c in n["candidatos"]))
            self.assertEqual(self.federacao(estadual)["votos_legenda"],
                             2 * self.federacao(federal)["votos_legenda"])
        cargo = self.client.get(f"{self.base}/cargos/0007",
                                params={"uf": "ap", "municipio": "06050", "zona": "0002"}).json()
        self.assertEqual((cargo["cargo"]["nome"], cargo["abrangencia"]["zona"]),
                         ("Deputado Estadual", "0002"))

    # 4 ------------------------------------------------------------------- secao
    def test_secao_sem_bu_nunca_devolve_outro_recorte(self):
        """Fase 3: a secao e aceita; sem BU ingerido nao ha resultado -- nem o da zona."""
        zona = self.client.get(f"{self.base}/cargos/0006/nominatas",
                               params={"uf": "ap", "municipio": "06050", "zona": "0002"}).json()
        self.assertTrue(zona["nominatas"])
        for rota, lista in (("cargos/0006/nominatas", "nominatas"), ("cargos/0006", "candidatos")):
            r = self.client.get(f"{self.base}/{rota}",
                                params={"uf": "ap", "municipio": "06050", "zona": "0002",
                                        "secao": "0055"})
            self.assertEqual(r.status_code, 200, rota)
            corpo = r.json()
            self.assertEqual((corpo["abrangencia"]["tipo"], corpo["abrangencia"]["secao"],
                              corpo["secao"]["status"], corpo["totalizacao"], corpo[lista]),
                             ("SECAO", "0055", "AGUARDANDO_BU", None, []), rota)
        # Hierarquia e formato continuam recusados.
        for filtro in ({"secao": "0055"}, {"municipio": "06050", "secao": "0055"},
                       {"municipio": "06050", "zona": "0002", "secao": "55"}):
            self.assertEqual(self.client.get(
                f"{self.base}/cargos/0006/nominatas", params={"uf": "ap", **filtro}).status_code,
                422, filtro)

    # 5, 6, 7 --------------------------------------------- combinacoes invalidas
    def test_combinacao_territorial_inexistente_e_404(self):
        def status(**filtro):
            return self.client.get(f"{self.base}/cargos/0006/nominatas",
                                   params={"uf": "ap", **filtro}).status_code

        self.assertEqual(status(municipio="99999"), 404)                  # municipio inexistente
        self.assertEqual(status(municipio="06050", zona="0001"), 404)     # zona de outro municipio
        self.assertEqual(status(municipio="06009", zona="0002"), 404)     # idem, invertido
        self.assertEqual(status(municipio="06009"), 404)                  # cadastrado, sem resultado
        self.assertEqual(self.client.get(
            f"{self.base}/cargos/0006/nominatas",
            params={"uf": "pa", "municipio": "06050"}).status_code, 404)  # municipio fora da UF
        self.assertEqual(status(zona="0002"), 422)                        # zona sem municipio
        self.assertEqual(status(municipio="6050"), 422)                   # formato

    # 15, 16, 17 ------------------------------------------- descoberta territorial
    def opcoes(self, **filtro):
        return self.client.get(f"{self.base}/territorio", params={"uf": "ap", **filtro})

    def test_municipios_sao_os_que_tem_resultado_na_uf(self):
        corpo = self.opcoes().json()
        self.assertEqual((corpo["nivel"], corpo["uf"], corpo["origem"], corpo["municipio"],
                          corpo["zona"]), ("municipios", "ap", "SIMULADO", None, None))
        # O EA12 cadastrou 16 municipios; so Macapa tem resultado ingerido.
        self.assertEqual(corpo["itens"], [{"codigo": "06050", "nome": "MACAPÁ"}])
        self.assertTrue(corpo["votos_por_secao_disponiveis"])
        self.assertEqual(self.opcoes().status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/territorio",
                                         params={"uf": "pa"}).status_code, 404)

    def test_municipios_vem_ordenados_por_nome(self):
        self.tse.arquivos = copy.deepcopy(self.tse.arquivos)
        for codigo in ("06157", "06009"):          # SANTANA e PRACUUBA
            for cargo in ("0006", "0007"):
                origem = self.tse.arquivos[f"{DADOS}/ap/ap06050-c{cargo}-e021272-u.json"]
                clone = copy.deepcopy(origem)
                clone["cdabr"] = codigo
                self.tse.arquivos[f"{DADOS}/ap/ap{codigo}-c{cargo}-e021272-u.json"] = clone
        self.ingerir(type(ESCOPO)(**{**ESCOPO.__dict__, "cargos": ("0006", "0007"),
                                    "municipios": ("06050", "06157", "06009")}))
        nomes = [m["nome"] for m in self.opcoes().json()["itens"]]
        self.assertEqual(nomes, ["MACAPÁ", "PRACUÚBA", "SANTANA"])

    def test_zonas_respeitam_o_municipio(self):
        corpo = self.opcoes(municipio="06050").json()
        self.assertEqual((corpo["nivel"], corpo["municipio"]),
                         ("zonas", {"codigo": "06050", "nome": "MACAPÁ"}))
        self.assertEqual(corpo["itens"], [{"zona": "0002"}, {"zona": "0010"}, {"zona": "0014"}])
        self.assertEqual(self.opcoes(municipio="99999").status_code, 404)
        # Municipio cadastrado mas sem zona ingerida: lista vazia, nao as zonas de outro.
        self.assertEqual(self.opcoes(municipio="06009").json()["itens"], [])

    def test_secoes_respeitam_municipio_e_zona(self):
        corpo = self.opcoes(municipio="06050", zona="0002").json()
        self.assertEqual((corpo["nivel"], corpo["zona"]), ("secoes", "0002"))
        secoes = [s["secao"] for s in corpo["itens"]]
        self.assertEqual(len(secoes), 10)
        self.assertEqual(secoes, sorted(secoes))
        self.assertTrue(all(s["zona"] == "0002" for s in corpo["itens"]))
        self.assertEqual(sorted(corpo["itens"][0]),
                         ["agregadas", "bu_status", "local_votacao", "principal", "recebida", "resultado_agregado",
                          "resultado_disponivel", "secao", "secao_principal", "zona"])
        self.assertEqual({s["bu_status"] for s in corpo["itens"]}, {"AGUARDANDO_BU"})
        # O filtro por secao existe; o voto de cada uma depende do seu BU.
        self.assertTrue(corpo["votos_por_secao_disponiveis"])
        self.assertFalse(any(s["resultado_disponivel"] for s in corpo["itens"]))
        outras = [s["secao"] for s in self.opcoes(municipio="06050", zona="0010").json()["itens"]]
        self.assertNotEqual(outras, secoes)
        self.assertEqual(self.opcoes(municipio="06050", zona="0001").status_code, 404)
        self.assertEqual(self.opcoes(zona="0002").status_code, 422)

    def test_territorio_exige_permissao_e_e_global(self):
        url = f"{self.base}/territorio?uf=ap"
        self.assertEqual(self.como(self.cliente_a).get(url).status_code, 200)
        self.assertEqual(self.como(self.agente_a).get(url).status_code, 403)
        self.assertEqual(self.como(self.gerente_a).get(url).json(),
                         self.como(self.gerente_b).get(url).json())

    # 18 ----------------------------------------------------- GET nao altera nada
    def test_leituras_nao_modificam_dados_do_tse(self):
        antes = self.contagens()
        ultima = self.session.query(models_tse_totalizacao()).order_by(
            models_tse_totalizacao().id.desc()).first().id
        for filtro in ({}, {"municipio": "06050"}, {"municipio": "06050", "zona": "0002"}):
            for cargo in ("0006", "0007"):
                self.nominatas(cargo, **filtro)
                self.client.get(f"{self.base}/cargos/{cargo}", params={"uf": "ap", **filtro})
            self.opcoes(**filtro)
        self.client.get(f"{self.base}/cargos/0006/nominatas",
                        params={"uf": "ap", "municipio": "99999"})
        self.session.expire_all()
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(self.session.query(models_tse_totalizacao()).order_by(
            models_tse_totalizacao().id.desc()).first().id, ultima)


def models_tse_totalizacao():
    from pesquisa360.db.models_tse import TseTotalizacao
    return TseTotalizacao
