# Importacao de setores por Shapefile

Ferramenta administrativa para Ubuntu/Linux que converte um Shapefile em
`Polygon` EPSG:4326 e cria um `Setor` pelas mesmas regras internas da aplicacao.
Ela nao cria endpoint, migration ou modelo paralelo.

## Preparacao

Instale as dependencias do `pyproject.toml` pelo gerenciador usado no ambiente.
O conjunto deve conter, com o mesmo nome-base, `.shp`, `.shx`, `.dbf` e `.prj`.
Arquivos em `data/imports/setores/` sao operacionais e ignorados pelo Git,
exceto o README e o `.gitkeep`.

```bash
chmod +x scripts/importar_setor_shapefile.sh
./scripts/importar_setor_shapefile.sh
```

O fluxo seleciona gerente/superadmin, projeto, pesquisa ativa, agente e arquivo;
solicita nome, cota e tolerancia; mostra o resumo e exige `IMPORTAR`.

Exemplo nao interativo no Ubuntu:

```bash
./scripts/importar_setor_shapefile.sh \
  --usuario-id 12 \
  --projeto-id 34 \
  --pesquisa-id 56 \
  --agente-id 78 \
  --arquivo /srv/pesquisa360/importacoes/setor-centro.shp \
  --nome "Setor Centro" \
  --meta 100 \
  --tolerancia 50 \
  --dry-run
```

Remova `--dry-run` e acrescente `--sim` somente depois de conferir o resumo.
Sem `--sim`, a ferramenta exige `IMPORTAR`. `--cota` e alias de `--meta`;
`--tolerancia-metros` permanece apenas como alias compativel.

## Regras e limites

- O tenant vem de `current_user.company_id`; projeto, pesquisa e agente sao
  revalidados imediatamente antes da gravacao.
- Cota e persistida como `meta`; tolerancia como `tolerancia`. A criacao usa
  `schemas.SetorCreate` e `crud.create_setor`.
- O CRUD existente cria o WKT, chama `ST_GeomFromText` e executa o commit. O CLI
  nao faz segundo commit e executa rollback quando ainda ha transacao aberta.
- O CRS do `.prj` e transformado para EPSG:4326 com `always_xy=True`.
- Somente `Polygon` simples, valido e sem aneis internos e aceito pelo contrato
  atual. `MultiPolygon` nunca e reduzido automaticamente.
- Com varias feicoes, a ferramenta oferece unir ou cancelar. `--unir-feicoes`
  autoriza a uniao em automacao. Uniao que resulte em `MultiPolygon` e abortada.
- Nomes duplicados usam `casefold`, remocao de espacos externos e colapso de
  espacos internos. `--permitir-nome-duplicado` libera somente essa verificacao.
- `--dry-run` abre o banco, valida contexto, arquivo, geometria e duplicidade,
  mas nao chama o CRUD nem grava.

O modelo atual usa `Geometry("POLYGON", srid=4326)`. Suporte a `MultiPolygon` ou
aneis internos exige decisao de contrato/modelagem fora desta ferramenta.

## Roadmap aprovado - finalidade territorial

A importacao interativa atual sera reutilizada em patch posterior para permitir
que o usuario escolha a finalidade do territorio:

```text
OPERACAO
RELATORIO
AMBOS
```

No estado atual, a ferramenta continua criando setores operacionais e nao altera
contrato, schema ou modelo. Na evolucao aprovada:

- setores existentes devem ser considerados `OPERACAO`;
- setores `RELATORIO` nao exigirao agente, meta/cota ou tolerancia operacional;
- setores `RELATORIO` exclusivos nao devem ser enviados ao Mobile como setores
  operacionais;
- setores `AMBOS` devem cumprir regras operacionais quando usados em operacao;
- o tenant continuara vindo de `current_user.company_id`;
- o frontend/mobile nao escolherao `company_id`.
