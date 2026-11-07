"""De-para IATA <-> ICAO dos aeroportos brasileiros.

As cotações do Trivio usam o código IATA (CGH, GRU, SDU...), enquanto os
microdados da ANAC identificam os aeroportos pelo código ICAO (SBSP, SBGR,
SBRJ...). Os dois não são deriváveis um do outro (CGH é SBSP, GIG é SBGL),
então mantemos a tabela explícita.

A cobertura privilegia os aeroportos com voos regulares das grandes
companhias. Aeroportos fora da tabela simplesmente não têm régua oficial: o
Termômetro recorre ao histórico próprio.
"""

from typing import Dict, Optional

IATA_TO_ICAO: Dict[str, str] = {
    # Sudeste
    "CGH": "SBSP",  # São Paulo - Congonhas
    "GRU": "SBGR",  # São Paulo - Guarulhos
    "VCP": "SBKP",  # Campinas - Viracopos
    "SDU": "SBRJ",  # Rio de Janeiro - Santos Dumont
    "GIG": "SBGL",  # Rio de Janeiro - Galeão
    "CNF": "SBCF",  # Belo Horizonte - Confins
    "PLU": "SBBH",  # Belo Horizonte - Pampulha
    "VIX": "SBVT",  # Vitória
    "UDI": "SBUL",  # Uberlândia
    "RAO": "SBRP",  # Ribeirão Preto
    "SJP": "SBSR",  # São José do Rio Preto
    "SJK": "SBSJ",  # São José dos Campos
    "JTC": "SBAE",  # Bauru/Arealva
    "PPB": "SBDN",  # Presidente Prudente
    "ARU": "SBAU",  # Araçatuba
    "UBA": "SBUR",  # Uberaba
    "MOC": "SBMK",  # Montes Claros
    "GVR": "SBGV",  # Governador Valadares
    "IPN": "SBIP",  # Ipatinga
    "IZA": "SBZM",  # Juiz de Fora - Zona da Mata
    "CFB": "SBCB",  # Cabo Frio
    "MEA": "SBME",  # Macaé
    # Sul
    "POA": "SBPA",  # Porto Alegre
    "CWB": "SBCT",  # Curitiba
    "FLN": "SBFL",  # Florianópolis
    "NVT": "SBNF",  # Navegantes
    "JOI": "SBJV",  # Joinville
    "IGU": "SBFI",  # Foz do Iguaçu
    "LDB": "SBLO",  # Londrina
    "MGF": "SBMG",  # Maringá
    "CXJ": "SBCX",  # Caxias do Sul
    "PET": "SBPK",  # Pelotas
    "RIG": "SBRG",  # Rio Grande
    "PFB": "SBPF",  # Passo Fundo
    "GEL": "SBNM",  # Santo Ângelo
    "CAC": "SBCA",  # Cascavel
    "XAP": "SBCH",  # Chapecó
    "CCM": "SBCM",  # Criciúma/Forquilhinha
    "JJG": "SBJA",  # Jaguaruna
    # Centro-Oeste
    "BSB": "SBBR",  # Brasília
    "GYN": "SBGO",  # Goiânia
    "CGB": "SBCY",  # Cuiabá
    "CGR": "SBCG",  # Campo Grande
    "OPS": "SBSI",  # Sinop
    "SMT": "SBSO",  # Sorriso
    "AFL": "SBAT",  # Alta Floresta
    "DOU": "SBDO",  # Dourados
    "CMG": "SBCR",  # Corumbá
    "BYO": "SBDB",  # Bonito
    "CLV": "SBCN",  # Caldas Novas
    "ROO": "SBRD",  # Rondonópolis
    # Nordeste
    "SSA": "SBSV",  # Salvador
    "REC": "SBRF",  # Recife
    "FOR": "SBFZ",  # Fortaleza
    "NAT": "SBSG",  # Natal - São Gonçalo do Amarante
    "MCZ": "SBMO",  # Maceió
    "AJU": "SBAR",  # Aracaju
    "JPA": "SBJP",  # João Pessoa
    "THE": "SBTE",  # Teresina
    "SLZ": "SBSL",  # São Luís
    "BPS": "SBPS",  # Porto Seguro
    "IOS": "SBIL",  # Ilhéus
    "VDC": "SBVC",  # Vitória da Conquista
    "JDO": "SBJU",  # Juazeiro do Norte
    "PNZ": "SBPL",  # Petrolina
    "CPV": "SBKG",  # Campina Grande
    "IMP": "SBIZ",  # Imperatriz
    "FEN": "SBFN",  # Fernando de Noronha
    "JJD": "SBJE",  # Jericoacoara
    "LEC": "SBLE",  # Lençóis
    "BRA": "SNBR",  # Barreiras
    "FEC": "SNJD",  # Feira de Santana
    "MVF": "SBMS",  # Mossoró
    "PHB": "SBPB",  # Parnaíba
    # Norte
    "BEL": "SBBE",  # Belém
    "MAO": "SBEG",  # Manaus
    "PMW": "SBPJ",  # Palmas
    "PVH": "SBPV",  # Porto Velho
    "RBR": "SBRB",  # Rio Branco
    "MCP": "SBMQ",  # Macapá
    "BVB": "SBBV",  # Boa Vista
    "STM": "SBSN",  # Santarém
    "MAB": "SBMA",  # Marabá
    "ATM": "SBHT",  # Altamira
    "CKS": "SBCJ",  # Carajás/Parauapebas
    "TBT": "SBTT",  # Tabatinga
    "TFF": "SBTF",  # Tefé
    "JPR": "SBJI",  # Ji-Paraná
    "BVH": "SBVH",  # Vilhena
}

ICAO_TO_IATA: Dict[str, str] = {icao: iata for iata, icao in IATA_TO_ICAO.items()}


def to_icao(code: Optional[str]) -> Optional[str]:
    """Converte um código de aeroporto para ICAO.

    Aceita IATA (3 letras, pela tabela) ou um ICAO brasileiro já pronto
    (4 letras começando por 'S'). Devolve None quando não reconhece o código.
    """
    if not code:
        return None
    clean = str(code).strip().upper()
    if len(clean) == 4 and clean.startswith("S") and clean.isalpha():
        return clean
    return IATA_TO_ICAO.get(clean)


def to_iata(icao: Optional[str]) -> Optional[str]:
    """Converte ICAO para IATA, quando o aeroporto está na tabela."""
    if not icao:
        return None
    return ICAO_TO_IATA.get(str(icao).strip().upper())
