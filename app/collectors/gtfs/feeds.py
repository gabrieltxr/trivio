"""Feeds GTFS conhecidos, por chave. Adicionar uma fonte é adicionar uma entrada aqui.

`default_state` vale para todas as cidades do feed (o GTFS não traz UF).
`license` e `source_url` vão para a documentação da fonte, não para o código.
"""

FEEDS = {
    "artesp": {
        "name": "ARTESP (regiões metropolitanas e intermunicipais de SP)",
        "default_state": "SP",
        "license": "CC-BY",
        "source_url": "https://dadosabertos.artesp.sp.gov.br/dataset/gtfs",
        "fare_kind": "tarifa regulada por linha (não é preço de venda)",
    },
}
