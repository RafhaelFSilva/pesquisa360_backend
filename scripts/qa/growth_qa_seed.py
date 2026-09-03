# -*- coding: utf-8 -*-
"""QA independente do MVP Potencial de Crescimento — seed do banco DESCARTÁVEL.

DADOS 100% SINTETICOS DE TESTE (empresas, usuários, senha e coletas fictícios).
Nunca executar contra banco compartilhado, DEV, staging ou produção.

Uso reproduzível (ver doc/20-inteligencia-eleitoral-potencial-crescimento-qa.md):
  1. docker run -d --name p360qa -e POSTGRES_PASSWORD=qa -e POSTGRES_DB=p360qa \
       -p 55432:5432 postgis/postgis:16-3.4
  2. set P360_QA_DATABASE_URL=postgresql://postgres:qa@localhost:55432/p360qa
  3. alembic upgrade head           (com DATABASE_URL=P360_QA_DATABASE_URL)
  4. python scripts/qa/growth_qa_seed.py
  5. subir a API contra o mesmo banco e rodar scripts/qa/growth_qa_run.py
  6. destruir o container ao final (docker rm -f p360qa)

A variável P360_QA_DATABASE_URL é OBRIGATÓRIA — não há default — justamente
para impedir a execução acidental contra um DATABASE_URL real herdado do shell.

Dataset oráculo principal (Pesquisa 1001, cenário SINGLE, alvo 'Candidato A'):

id    intencao(1101)       sexo   idade 2a(1103)     rejeicao(1102)              decisao(1104) espont(1107)   ponto
9001  Candidato A          F      25    -            -                           -             -              S1 (0.5,0.5)
9002  Candidato A          M      40    -            -                           -             -              S2 (2.5,0.5)
9003  Candidato B          F      20    A. da Silva  [Fulano B]                  Pode mudar    'candidato a'  S1
9004  Candidato B          F      30    B. de Souza  [Fulano A]                  Definitivo    'zzz'          S1
9005  Candidato B          F      22    A. da Silva  [Fulano A,Fulano B,Fulano C] Talvez       -              S1
9006  Candidato C          F      35    -            -                           Pode mudar    -              BORDA S1 (0,0.5)
9007  Candidato B          M      45    A. da Silva  [Fulano B]                  Definitivo    'CANDIDATO A'  S1
9008  Candidato B          M      28    B. de Souza  [Fulano B,Fulano B] (dup)   Pode mudar    -              S2
9009  Candidato C          M      50    A. da Silva  -                           -             'candidato a'  S2
9010  Candidato B          M      33    B. de Souza  [Fulano A]                  -             -              SOBREPOSTO (0.9,0.5)
9011  Candidato B          F      60    -            [Fulano C]                  -             -              SO S3 (1.2,0.5)
9012  Candidato C          F      18    A. da Silva  -                           Definitivo    -              FORA (5,5)
9013  Candidato B          M      24    B. de Souza  -                           -             -              SEM COORDENADA
9014  Indeciso             F      21    -            -                           -             -              S1
9015  Indeciso             M      55    -            -                           -             -              S2
9016  Branco/Nulo          F      47    -            -                           -             -              S1
9017  NS/NR                M      29    -            -                           -             -              S1
9018  Não pretende votar   F      31    -            -                           -             -              S2
9019  (sem intencao)       F      26    A. da Silva  -                           -             -              S1
9020  (sem intencao)       M      -     -            -                           -             -              S2

Totais manuais (policy indeciso=IN, branco=IN, nsnr=OUT, npv=OUT):
survey=20 analytical=20 supporters=2 missing=2 excluded_special=2 eligible=14
FEM={9003,9004,9005,9006,9011,9012,9014,9016}=8  MASC={9007,9008,9009,9010,9013,9015}=6
"""
import os
import sys
from pathlib import Path

_qa_db_url = os.environ.get("P360_QA_DATABASE_URL")
if not _qa_db_url:
    sys.exit(
        "Defina P360_QA_DATABASE_URL apontando para o banco QA DESCARTÁVEL "
        "(ex.: postgresql://postgres:qa@localhost:55432/p360qa). Abortado."
    )
os.environ["DATABASE_URL"] = _qa_db_url
os.environ.setdefault("SECRET_KEY", "qa-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from geoalchemy2.elements import WKTElement
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pesquisa360.core.security import get_password_hash
from pesquisa360.db import models

engine = create_engine(os.environ["DATABASE_URL"])
Session = sessionmaker(bind=engine)
db = Session()

SENHA = "QA-senha-123!"
HASH = get_password_hash(SENHA)

# --- tenants / usuarios ------------------------------------------------------
db.add_all([
    models.Company(id=11, name="QA Empresa A", is_active=True),
    models.Company(id=12, name="QA Empresa B", is_active=True),
])
db.flush()
db.add_all([
    models.Perfil(id=5, nome="Gerente"),
    models.Perfil(id=6, nome="Agente"),
])
db.flush()
db.add_all([
    models.Usuario(id=1, email="gerente.a@qa-p360.com.br", nome="Gerente QA A", senha_hash=HASH, ativo=True, perfil_id=5, company_id=11),
    models.Usuario(id=2, email="gerente.b@qa-p360.com.br", nome="Gerente QA B", senha_hash=HASH, ativo=True, perfil_id=5, company_id=12),
    models.Usuario(id=3, email="agente.a@qa-p360.com.br", nome="Agente QA A", senha_hash=HASH, ativo=True, perfil_id=6, company_id=11),
])
db.flush()
db.add_all([
    models.UsuarioEmpresaAcesso(usuario_id=1, company_id=11, acesso_todos_projetos=True, ativo=True, principal=True),
    models.UsuarioEmpresaAcesso(usuario_id=2, company_id=12, acesso_todos_projetos=True, ativo=True, principal=True),
    models.UsuarioEmpresaAcesso(usuario_id=3, company_id=11, acesso_todos_projetos=True, ativo=True, principal=True),
])

# --- projetos / pesquisas ----------------------------------------------------
db.add_all([
    models.Projeto(id=101, nome="QA Projeto A", coordenador_id=1, company_id=11),
    models.Projeto(id=201, nome="QA Projeto B", coordenador_id=2, company_id=12),
])
db.flush()
db.add_all([
    models.Pesquisa(id=1001, titulo="QA Oraculo A1", ativo=True, projeto_id=101),
    models.Pesquisa(id=1002, titulo="QA Ballot A2", ativo=True, projeto_id=101),
    models.Pesquisa(id=1003, titulo="QA Stress A3", ativo=True, projeto_id=101),
    models.Pesquisa(id=2001, titulo="QA B1", ativo=True, projeto_id=201),
])
db.flush()

# --- perguntas / opcoes ------------------------------------------------------
def pergunta(pid, pesquisa, texto, tipo, ordem, opcoes=(), espontanea=False, papel=None):
    db.add(models.Pergunta(
        id=pid, pesquisa_id=pesquisa, texto_pergunta=texto, tipo_pergunta=tipo,
        ordem=ordem, eh_obrigatoria=False, eh_resposta_espontanea=espontanea,
        papel_analitico=papel, metadados_analiticos={}, ativo=True,
        aplicabilidade="GLOBAL",
    ))
    db.flush()
    for i, opt in enumerate(opcoes, start=1):
        db.add(models.Opcao(texto=opt, ordem=i, pergunta_id=pid))
    db.flush()

pergunta(1101, 1001, "Em quem você votaria?", "ESCOLHA_SIMPLES", 1,
         ["Candidato A", "Candidato B", "Candidato C", "Indeciso", "Branco/Nulo", "NS/NR", "Não pretende votar"],
         papel="INTENCAO_VOTO")
pergunta(1102, 1001, "Em quem você NÃO votaria de jeito nenhum?", "MULTIPLA_ESCOLHA", 2,
         ["Fulano A", "Fulano B", "Fulano C"], papel="REJEICAO")
pergunta(1103, 1001, "Quem seria sua segunda opção?", "ESCOLHA_SIMPLES", 3,
         ["A. da Silva", "B. de Souza", "NS/NR"], papel="SEGUNDA_OPCAO")
pergunta(1104, 1001, "Seu voto é definitivo?", "ESCOLHA_SIMPLES", 4,
         ["Definitivo", "Pode mudar", "Talvez"], papel="DECISAO_VOTO")
pergunta(1105, 1001, "Sexo", "ESCOLHA_SIMPLES", 5, ["Feminino", "Masculino"], papel="PERFIL")
pergunta(1106, 1001, "Idade", "NUMERO", 6, [], papel="PERFIL")
pergunta(1107, 1001, "Cite espontaneamente uma segunda opção", "TEXTO", 7, [], espontanea=True)
pergunta(1110, 1001, "Qual candidato você rejeita?", "TEXTO", 8, [])  # texto enganoso, tipo TEXTO
pergunta(1114, 1001, "Segunda opção alternativa (sem respostas)", "ESCOLHA_SIMPLES", 9,
         ["A. da Silva", "Sem uso"])
pergunta(1115, 1001, "Observações", "TEXTO", 10, [])  # nao-analitica p/ fingerprint

pergunta(1201, 1002, "Escolha até dois candidatos", "MULTIPLA_ESCOLHA", 1,
         ["Candidato A", "Candidato B", "Indeciso", "NS/NR"], papel="INTENCAO_VOTO")
pergunta(1202, 1002, "Primeiro voto", "ESCOLHA_SIMPLES", 2, ["Candidato A", "Candidato B", "Candidato C"])
pergunta(1203, 1002, "Segundo voto", "ESCOLHA_SIMPLES", 3, ["Candidato A", "Candidato B", "Candidato C"])
pergunta(1205, 1002, "Sexo", "ESCOLHA_SIMPLES", 4, ["Feminino", "Masculino"], papel="PERFIL")

pergunta(1301, 1003, "Intenção (stress)", "ESCOLHA_SIMPLES", 1, ["Candidato A", "Candidato B"])
pergunta(1302, 1003, "Perfil P1 (25 valores)", "ESCOLHA_SIMPLES", 2, [f"V{i:02d}" for i in range(25)])
pergunta(1303, 1003, "Perfil P2 (20 valores)", "ESCOLHA_SIMPLES", 3, [f"W{i:02d}" for i in range(20)])

pergunta(2101, 2001, "Pergunta da empresa B", "ESCOLHA_SIMPLES", 1, ["X", "Y"])

# --- espontanea: categoria ativa + mapping -----------------------------------
db.add(models.CategoriaRespostaEspontanea(id=1, pesquisa_id=1001, nome="Candidato A",
                                          nome_normalizado="candidato a", ativo=True,
                                          criado_por_id=1, atualizado_por_id=1))
db.flush()
db.add(models.MapeamentoRespostaEspontanea(pesquisa_id=1001, categoria_id=1,
                                           chave_normalizada="candidato a",
                                           texto_referencia="Candidato A", ativo=True,
                                           criado_por_id=1, atualizado_por_id=1))

# --- base eleitoral (VALIDADA) + territorios ---------------------------------
db.add(models.BaseEleitoral(id=1, nome="QA Base AP", ano=2026, uf="AP", fonte="TSE",
                            versao="v1", data_referencia="2026-01-01", status="VALIDADA",
                            company_id=None, criado_por_id=1))
db.flush()
db.add(models.TerritorioEleitoral(id=890, base_eleitoral_id=1, tipo="ESTADO", nome="Amapá",
                                  nome_normalizado="amapa"))
db.flush()
db.add_all([
    models.TerritorioEleitoral(id=900, base_eleitoral_id=1, tipo="MUNICIPIO", nome="Macapá",
                               nome_normalizado="macapa", eleitorado_apto=50000, parent_id=890),
    models.TerritorioEleitoral(id=901, base_eleitoral_id=1, tipo="MUNICIPIO", nome="Santana",
                               nome_normalizado="santana", eleitorado_apto=30000, parent_id=890),
])
db.flush()
db.add(models.ProjetoBaseEleitoral(projeto_id=101, base_eleitoral_id=1, principal=True))

# --- setores -----------------------------------------------------------------
def setor(sid, pesquisa, nome, finalidade, wkt, municipio=None):
    db.add(models.Setor(id=sid, nome=nome, meta=10, tolerancia=50, finalidade=finalidade,
                        geometria=WKTElement(wkt, srid=4326), pesquisa_id=pesquisa,
                        municipio_territorio_id=municipio))
    db.flush()

setor(5001, 1001, "QA Centro", "AMBOS", "POLYGON((0 0,1 0,1 1,0 1,0 0))", 900)
setor(5002, 1001, "QA Leste", "AMBOS", "POLYGON((2 0,3 0,3 1,2 1,2 0))", 901)
setor(5003, 1001, "QA Sobreposto", "AMBOS", "POLYGON((0.8 0,1.5 0,1.5 1,0.8 1,0.8 0))", None)
setor(5004, 1001, "QA Norte", "OPERACAO", "POLYGON((0 2,1 2,1 3,0 3,0 2))", None)
setor(5101, 2001, "QA Setor B", "AMBOS", "POLYGON((10 10,11 10,11 11,10 11,10 10))", None)

# --- coletas + respostas -----------------------------------------------------
P_S1, P_S2 = "POINT(0.5 0.5)", "POINT(2.5 0.5)"
P_BORDA, P_OVER, P_S3 = "POINT(0 0.5)", "POINT(0.9 0.5)", "POINT(1.2 0.5)"
P_FORA = "POINT(5 5)"

def coleta(cid, pesquisa, point, company=11, agente=1):
    db.add(models.Coleta(
        id=cid, pesquisa_id=pesquisa, agente_id=agente, company_id=company,
        client_uuid=f"qa-{cid}", status_sincronizacao="sincronizado",
        inconformidade_localizacao=False,
        data_inicio_coleta="2026-08-01T10:00:00+00:00",
        data_fim_coleta="2026-08-01T10:10:00+00:00",
        localizacao_inicio=WKTElement(point, srid=4326) if point else None,
    ))

def resp(cid, qid, valor):
    db.add(models.Resposta(pergunta_id=qid, coleta_id=cid, valor_resposta=valor))

import json as _json
ORACULO = [
    (9001, "Candidato A", "Feminino", "25", None, None, None, None, P_S1),
    (9002, "Candidato A", "Masculino", "40", None, None, None, None, P_S2),
    (9003, "Candidato B", "Feminino", "20", "A. da Silva", ["Fulano B"], "Pode mudar", "candidato a", P_S1),
    (9004, "Candidato B", "Feminino", "30", "B. de Souza", ["Fulano A"], "Definitivo", "zzz", P_S1),
    (9005, "Candidato B", "Feminino", "22", "A. da Silva", ["Fulano A", "Fulano B", "Fulano C"], "Talvez", None, P_S1),
    (9006, "Candidato C", "Feminino", "35", None, None, "Pode mudar", None, P_BORDA),
    (9007, "Candidato B", "Masculino", "45", "A. da Silva", ["Fulano B"], "Definitivo", "CANDIDATO A", P_S1),
    (9008, "Candidato B", "Masculino", "28", "B. de Souza", ["Fulano B", "Fulano B"], "Pode mudar", None, P_S2),
    (9009, "Candidato C", "Masculino", "50", "A. da Silva", None, None, "candidato a", P_S2),
    (9010, "Candidato B", "Masculino", "33", "B. de Souza", ["Fulano A"], None, None, P_OVER),
    (9011, "Candidato B", "Feminino", "60", None, ["Fulano C"], None, None, P_S3),
    (9012, "Candidato C", "Feminino", "18", "A. da Silva", None, "Definitivo", None, P_FORA),
    (9013, "Candidato B", "Masculino", "24", "B. de Souza", None, None, None, None),
    (9014, "Indeciso", "Feminino", "21", None, None, None, None, P_S1),
    (9015, "Indeciso", "Masculino", "55", None, None, None, None, P_S2),
    (9016, "Branco/Nulo", "Feminino", "47", None, None, None, None, P_S1),
    (9017, "NS/NR", "Masculino", "29", None, None, None, None, P_S1),
    (9018, "Não pretende votar", "Feminino", "31", None, None, None, None, P_S2),
    (9019, None, "Feminino", "26", "A. da Silva", None, None, None, P_S1),
    (9020, None, "Masculino", None, None, None, None, None, P_S2),
]
for cid, intent, sexo, idade, segunda, rejeicao, decisao, espont, point in ORACULO:
    coleta(cid, 1001, point)
    if intent: resp(cid, 1101, intent)
    if sexo: resp(cid, 1105, sexo)
    if idade: resp(cid, 1106, idade)
    if segunda: resp(cid, 1103, segunda)
    if rejeicao: resp(cid, 1102, _json.dumps(rejeicao, ensure_ascii=False))
    if decisao: resp(cid, 1104, decisao)
    if espont: resp(cid, 1107, espont)
# resposta nao-analitica (fingerprint irrelevante) na coleta 9003
resp(9003, 1115, "observacao original")

# --- pesquisa 1002: ballot modes ---------------------------------------------
BALLOT = [
    (9101, {"m": ["Candidato B", "Candidato A"], "sexo": "Feminino"}),
    (9102, {"m": ["Candidato B"], "sexo": "Feminino"}),
    (9103, {"m": ["Indeciso", "NS/NR"], "sexo": "Feminino"}),
    (9104, {"v1": "Candidato B", "v2": "Candidato A", "sexo": "Feminino"}),
    (9105, {"v1": "Candidato B", "v2": "Candidato C", "sexo": "Feminino"}),
    (9106, {"v1": "Candidato A", "sexo": "Feminino"}),
]
for cid, answers in BALLOT:
    coleta(cid, 1002, P_S1)
    if "m" in answers: resp(cid, 1201, _json.dumps(answers["m"], ensure_ascii=False))
    if "v1" in answers: resp(cid, 1202, answers["v1"])
    if "v2" in answers: resp(cid, 1203, answers["v2"])
    resp(cid, 1205, answers["sexo"])

# --- pesquisa 1003: stress ----------------------------------------------------
for i in range(2400):
    cid = 20000 + i
    coleta(cid, 1003, None)
    resp(cid, 1301, "Candidato A" if i % 40 == 0 else "Candidato B")
    resp(cid, 1302, f"V{i % 25:02d}")
    resp(cid, 1303, f"W{i % 20:02d}")

# --- pesquisa 2001 (empresa B) -----------------------------------------------
coleta(9201, 2001, None, company=12, agente=2)
resp(9201, 2101, "X")

db.commit()

# --- catalogo: confirmar estado inicial (feature INATIVA) ---------------------
rows = db.execute(text(
    "SELECT m.chave, m.ativo, f.chave, f.ativo FROM modulos m JOIN modulo_funcionalidades f ON f.modulo_id=m.id"
)).fetchall()
print("CATALOGO:", rows)
counts = db.execute(text(
    "SELECT (SELECT count(*) FROM coletas), (SELECT count(*) FROM respostas), (SELECT count(*) FROM setores)"
)).fetchone()
print("SEED OK — coletas/respostas/setores:", counts)
db.close()
