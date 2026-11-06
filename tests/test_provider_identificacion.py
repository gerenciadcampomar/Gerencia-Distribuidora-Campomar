from campomar_compras.provider_identificacion import CUIT_RE, _normalizar_cuit


def test_cuit_con_guiones():
    m = CUIT_RE.search("CUIT: 30-50404289-4 IVA - Responsable Inscripto")
    assert _normalizar_cuit(m.group(1)) == "30504042894"


def test_cuit_sin_guiones():
    m = CUIT_RE.search("C.U.I.T.: 30539258660")
    assert _normalizar_cuit(m.group(1)) == "30539258660"


def test_cuit_con_nro():
    m = CUIT_RE.search("C.U.I.T. Nro: 30-64698450-1")
    assert _normalizar_cuit(m.group(1)) == "30646984501"
