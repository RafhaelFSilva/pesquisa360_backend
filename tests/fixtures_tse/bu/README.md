# Boletins de Urna oficiais (fixtures)

Arquivos `*-bu.dat` REAIS das Eleições 2026, 1º turno (pleito 3220, AP), baixados
em 2026-10-04 da infraestrutura oficial de divulgação do TSE
(`https://resultados.tse.jus.br/oficial/ele2026/arquivo-urna/3220/dados/ap/...`),
exatamente como publicados. São dados públicos. Não editar: o SHA-256 é conferido
nos testes.

| Arquivo | Seção | SHA-256 |
|---|---|---|
| `o03220ap0605000020069-bu.dat` | Macapá (06050), zona 0002, seção 0069 | `7b88007c975d155ffffacc6aa3ac603542f921ca58ad931b4317b786de0a35ce` |
| `o03220ap0600900010014-bu.dat` | Amapá (06009), zona 0001, seção 0014 — principal da agregada 0088 | `d2b3034951713bf33573e7fbbdd4a70039dcf103562e807cbeeb9a3e37d9105a` |

`ea18_0069.json` é o EA18 oficial da seção 0069 (o arquivo que aponta para o BU).

Especificação usada para decodificar: `pesquisa360/services/tse/asn1/bu-2026.asn1`
(SHA-256 `ef64bf723f774403b423ed0b617f5a83a3f50c0023d0003e18cd29170404ab81`), do
pacote oficial "formato-arquivos-de-bu-rdv-e-assinatura-digital" de 2026.
