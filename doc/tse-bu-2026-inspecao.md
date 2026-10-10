# Boletim de Urna 2026 — inspeção da especificação oficial

Registro da inspeção feita em 2026-10-04, antes de qualquer modelagem ou
código. O decoder, as tabelas e os contratos da fase 3 derivam deste documento.
Decisão arquitetural: ADR-090 (`10-decisoes-arquiteturais.md`).

## 1. Arquivo oficial usado

Pacote do TSE "Formato dos arquivos de BU, RDV e assinatura digital", Eleições
2026, baixado manualmente pelo navegador (a página bloqueia clientes
automatizados) e guardado sem alteração em:

`docs/tse2026/formato-bu-rdv-2026.zip` — 1.265.306 bytes
SHA-256 `b5ac413e81121d53de2bb1657af611f84a45fdc84388e9d9821872994bbfbafd`

Conteúdo (24 entradas, datadas de 10–11/09/2026):

| Arquivo | SHA-256 | Uso |
|---|---|---|
| `spec/bu.asn1` (24.450 bytes) | `ef64bf723f774403b423ed0b617f5a83a3f50c0023d0003e18cd29170404ab81` | **usado**: schema do BU e do envelope |
| `spec/rdv.asn1` | `282dcde0c7ba1f1bb128fe27443ad8d18f7490416c9546d033910a7ec557a465` | não usado (RDV fora do escopo) |
| `spec/assinatura.asn1` | `86a0edf579b91e0a6df9f0981521e0229f41cb6cab0ed8a66d8a56f68c1f396f` | não usado (ver §6) |
| `python/bu_dump.py`, `python/lib/mr_util.py` | — | código de referência do TSE: definiu o procedimento de decodificação |
| `README.pdf`, `diagramas/*.puml` | — | documentação |

O `bu.asn1` foi copiado byte a byte para
`pesquisa360/services/tse/asn1/bu-2026.asn1`; o decoder confere o SHA-256
antes de compilar e recusa um arquivo diferente. Nenhuma especificação de 2022
ou 2024 foi usada.

O pacote foi extraído só para leitura, fora do repositório, com proteção
contra Zip Slip (cada caminho é resolvido e precisa ficar dentro do destino) e
contra zip bomb (limite de entradas, de tamanho total e por arquivo).

## 2. Codificação e procedimento

- **Codec: BER**, módulo `ModuloBU DEFINITIONS IMPLICIT TAGS`.
- O arquivo `*-bu.dat` é uma `EntidadeEnvelopeGenerico`; o campo `conteudo`
  (OCTET STRING) é a `EntidadeBoletimUrna`, também em BER.
- Procedimento do código de referência (`bu_dump.py`):
  `asn1tools.compile_files([bu.asn1], codec="ber", numeric_enums=True)` →
  `decode("EntidadeEnvelopeGenerico", bytes)` →
  `decode("EntidadeBoletimUrna", envelope["conteudo"])`.
- Biblioteca: **`asn1tools`** (a mesma do `requirements.txt` do pacote; MIT,
  Python puro; depende de `pyparsing` e `bitstruct`). `pyasn1` exigiria
  reescrever o schema à mão como classes — fonte de erro que o `asn1tools`
  elimina ao compilar o arquivo oficial. O schema é compilado **uma vez por
  processo** (~200 ms); cada boletim decodifica em 2–3 ms.

## 3. Estrutura relevante

```
EntidadeEnvelopeGenerico
  cabecalho { dataGeracao, idEleitoral }      fase            identificacao (CHOICE: seção | contingência)
  tipoEnvelope (1 = BU)                       seguranca?      conteudo OCTET STRING

EntidadeBoletimUrna
  cabecalho { dataGeracao DataHoraJE, idEleitoral CHOICE { idProcessoEleitoral | idPleito | idEleicao } }
  fase                 ENUMERATED { simulado(1), oficial(2), treinamento(3) }
  urna                 { tipoUrna, versaoVotacao, correspondenciaResultado { identificacao, carga {
                           numeroInternoUrna, numeroSerieFC, identificadorGeradorMidia, dataHoraCarga,
                           codigoCarga } }, tipoArquivo, numeroSerieFV, motivoUtilizacaoSA? }
  identificacaoSecao   { municipioZona { municipio, zona }, local, secao }
  dataHoraEmissao      DataHoraJE
  dadosSecaoSA         CHOICE { dadosSecao { dataHoraAbertura, dataHoraEncerramento, ... } | dadosSA { ... } }
  qtdEleitoresCompareceram
  detalhamentoComparecimento?
  resultadosVotacaoPorEleicao  SEQUENCE OF {
      idEleicao, qtdEleitoresAptos, qtdEleitoresAptosSecao, qtdEleitoresAptosTTE,
      resultadosVotacao SEQUENCE OF {
          tipoCargo  ENUMERATED { majoritario(1), proporcional(2), consulta(3) }
          qtdComparecimento
          totaisVotosCargo SEQUENCE OF {
              codigoCargo  CHOICE { cargoConstitucional | numeroCargoConsultaLivre }
              ordemImpressao
              votosVotaveis SEQUENCE OF {
                  tipoVoto  ENUMERATED { nominal(1), branco(2), nulo(3), legenda(4), cargoSemCandidato(5) }
                  quantidadeVotos
                  identificacaoVotavel? { partido, codigo }     -- omitido em branco e nulo
                  ordemGeracaoHash, hash } } }
      ultimoHashVotosVotavel, assinaturaUltimoHashVotosVotavel }
  historicoCodigosCarga, historicoVotoImpresso?
```

## 4. Mapeamento ASN.1 → domínio Pesquisa360

| Conceito | Campo do BU | No Pesquisa360 |
|---|---|---|
| Origem | `fase` | 2 → `OFICIAL`, 1 → `SIMULADO`; 3 (treinamento) nunca é ingerido |
| Pleito | `cabecalho.idEleitoral` (`idPleito`) | `tse_boletins_urna.pleito`; conferido com a seção |
| Eleição | `resultadosVotacaoPorEleicao[].idEleicao` | `tse_eleicoes` (um BU traz as duas: 6257 e 6259) |
| Turno | — (não existe no BU) | o da eleição (`tse_eleicoes.turno`, do EA11) |
| UF | — (não existe no BU) | a da seção (`tse_secoes.uf`, do EA16) |
| Município, zona, seção | `identificacaoSecao` | conferidos com a seção em que o BU foi pedido |
| Local de votação | `identificacaoSecao.local` | `local_votacao` |
| Cargo | `codigoCargo.cargoConstitucional` | `tse_cargos.codigo` (1 → `0001` … 7 → `0007`) |
| Candidato | `identificacaoVotavel.codigo` (voto nominal) | **número** → `tse_candidatos` por (eleição, cargo, UF, número) |
| Partido | `identificacaoVotavel.partido` | `tse_partidos.numero` |
| Voto nominal | `tipoVoto = nominal` | `tse_bu_votos` (`NOMINAL`) |
| Voto de legenda | `tipoVoto = legenda` (`codigo` = `partido`) | `tse_bu_votos` (`LEGENDA`) — nunca derivado de candidato |
| Brancos / nulos | `tipoVoto = branco` / `nulo` | `tse_bu_cargos.votos_brancos` / `votos_nulos` |
| Comparecimento | `qtdEleitoresCompareceram`, `qtdComparecimento` | `comparecimento` (boletim e cargo) |
| Eleitores aptos | `qtdEleitoresAptos` | `tse_bu_cargos.eleitores_aptos` |
| Datas | `DataHoraJE` (`YYYYMMDDThhmmss`, sem fuso) | colunas `*_local`, sem fuso (ver §5) |
| Urna | `urna.*`, `carga.*` | `tipo_urna`, `tipo_arquivo`, `versao_votacao`, `numero_interno_urna`, `codigo_carga` |
| Hashes / assinatura | `hash`, `ultimoHashVotosVotavel`, `assinatura…` | não persistidos (ver §6) |

Resposta à dúvida registrada em `11-apuracao-tse.md` §10: **o BU identifica o
candidato pelo número**, não pelo `sqcand`. O vínculo com o cadastro é feito
só quando o número identifica um único candidato; senão o voto fica gravado
sem vínculo.

## 5. Ambiguidades encontradas e como foram resolvidas

1. **Seção agregada.** O schema **não** tem campo para seções agregadas: um BU
   identifica uma única seção. Confirmado em arquivo real — o BU da seção 0014
   de Amapá (que agrega a 0088 no EA16) traz só "seção 14" e 327 eleitores
   aptos, os das duas. Logo: a relação principal → agregadas vem do EA16; o
   boletim pertence à principal e representa o grupo. Não há como atribuir
   votos a cada seção do grupo, e o sistema não tenta (`resultado_agregado`).
2. **Múltiplas seções por urna.** Mesma resposta: o formato não permite listar
   várias; a agregação é externa ao BU.
3. **Fuso das datas.** `DataHoraJE` não traz fuso: é a hora local da urna. As
   datas do BU são gravadas sem fuso e exibidas como "hora local". Para
   ordenar e mostrar "última atualização" usa-se o `recebido_em` do EA18, que
   tem fuso.
4. **Votos válidos.** O BU não tem o conceito: traz nominais, legenda, brancos
   e nulos. No nível seção, "válidos" = nominais + legenda **do boletim**. Isso
   difere da totalização do TSE em dois casos, medidos nos dados reais (§7).
5. **Candidato sem voto.** O BU lista só os votáveis que receberam voto.
   Candidato ausente de um BU ingerido tem zero voto naquela urna (dado);
   seção sem BU não tem dado nenhum (nunca zero).
6. **Consulta popular e cargo livre** (`numeroCargoConsultaLivre`): fora do
   escopo; não são gravados.

Nenhum campo essencial ficou ambíguo a ponto de impedir a implementação.

## 6. Assinatura e hashes

O `assinatura.asn1` e os scripts `assinatura_*.py` / `verifica_qrcode*.py`
verificam a assinatura digital da urna; exigem os certificados da urna, o
`asn1crypto`, o `pyOpenSSL` e uma versão fixada do `ECPy`. **Não foi
implementado**: a integridade do que é ingerido é garantida por (a) HTTPS com
o host oficial do TSE fixado no cliente, (b) o hash de diretório publicado no
EA18, que endereça o arquivo, e (c) a conferência BU × EA20. A verificação
criptográfica fica como pendência (ver `08-roadmap.md`).

## 7. Validação com dados oficiais

- 1.914 BUs do AP (todas as urnas do estado, pleito 3220) baixados e
  decodificados com este schema: 0 falhas.
- Conferência BU × EA20 nas 18 zonas e nos 5 cargos (90 conferências):
  **67 MATCH, 23 PARTIAL, 0 DIVERGENT**. Os PARTIAL são zonas em que o EA20
  ingerido ainda não estava em 100% das seções (janelas diferentes).
- Na zona 0002 de Macapá (451 urnas), os cinco cargos fecham **candidato a
  candidato**, além de comparecimento, brancos, nulos e legenda.

Duas regras de totalização do TSE ficaram demonstradas por identidade exata:

| Regra | Evidência (zona 0002 de Macapá) |
|---|---|
| Voto nominal do BU em votável **fora da lista de candidatos** do EA20 entra nos **nulos** | Presidente: nulos EA20 1.633 = BU 1.630 + 3; Dep. Estadual: 1.380 = 1.353 + 27 |
| Voto de candidato **anulado sub judice** segue no candidato, mas sai de nominais/válidos | Dep. Federal: nominais BU 107.737 = EA20 107.429 + 5 fora da lista + 303 sub judice |

Por isso a seção nunca é comparada com a zona pelo total de válidos, e sim
candidato a candidato (ver `bu_reconciliation.py`).
