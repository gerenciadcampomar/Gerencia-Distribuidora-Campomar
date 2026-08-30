from campomar_compras.provider_parsers import BYC_LINE_RE, decode_codigo_byc


def test_decode_codigo_casos_reales():
    assert decode_codigo_byc("1349111349") == ("1349", "11")
    assert decode_codigo_byc("094712947") == ("947", "12")
    assert decode_codigo_byc("094710947") == ("947", "10")
    assert decode_codigo_byc("098110981") == ("981", "10")
    assert decode_codigo_byc("098212982") == ("982", "12")


def test_linea_con_variante_pegada_sin_espacio():
    m = BYC_LINE_RE.match(
        "27873 01BR1349111349 1X* 12/08/2026 10.00 4046.18 1416.16 14161.63"
    )
    assert m
    assert m.group("digitos") == "1349111349"
    assert m.group("cantidad") == "10.00"
    assert m.group("precio_neto") == "1416.16"
    assert m.group("neto_pedido") == "14161.63"


def test_linea_con_prefijo_bim_pegado_a_la_fecha():
    m = BYC_LINE_RE.match(
        "27873 01BI1343101343 STD BIM12/08/2026 50.00 12586.93 4405.43 220271.28"
    )
    assert m
    assert decode_codigo_byc(m.group("digitos")) == ("1343", "10")
