import datetime

from campomar_compras.provider_identificacion import CUIT_RE, FECHA_RE, _normalizar_cuit


def test_cuit_con_guiones():
    m = CUIT_RE.search("CUIT: 30-50404289-4 IVA - Responsable Inscripto")
    assert _normalizar_cuit(m.group(1)) == "30504042894"


def test_cuit_sin_guiones():
    m = CUIT_RE.search("C.U.I.T.: 30539258660")
    assert _normalizar_cuit(m.group(1)) == "30539258660"


def test_cuit_con_nro():
    m = CUIT_RE.search("C.U.I.T. Nro: 30-64698450-1")
    assert _normalizar_cuit(m.group(1)) == "30646984501"


def test_fecha_nosso_sin_cero_a_la_izquierda():
    m = FECHA_RE.search("Fecha: 28/8/2026")
    assert m.groups() == ("28", "8", "2026")
    assert datetime.date(2026, 8, 28) == datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def test_fecha_ralux_sin_espacio_tras_dos_puntos():
    m = FECHA_RE.search("FECHA:06/08/2026")
    assert m.groups() == ("06", "08", "2026")
