import datetime

import campomar_compras.provider_identificacion as provider_identificacion
from campomar_compras.provider_identificacion import CUIT_RE, FECHA_RE, _normalizar_cuit, identificar_proveedor


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


def test_identificar_por_nombre_archivo_cuando_no_hay_cuit(tmp_path, monkeypatch):
    """Algunos comprobantes (remitos/vales escaneados) no traen CUIT ni
    ningún dato identificatorio -- el nombre del archivo sirve de
    respaldo, sin importar mayúsculas/minúsculas ni qué más diga."""
    monkeypatch.setattr(provider_identificacion, "extraer_cuit_proveedor", lambda pdf_path: None)
    pdf_falso = tmp_path / "Nosso_factura123.pdf"
    pdf_falso.write_bytes(b"contenido falso, no se usa")
    assert identificar_proveedor(pdf_falso) == "nosso"


def test_identificar_por_nombre_archivo_sin_coincidencia(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_identificacion, "extraer_cuit_proveedor", lambda pdf_path: None)
    pdf_falso = tmp_path / "comprobante_generico.pdf"
    pdf_falso.write_bytes(b"x")
    assert identificar_proveedor(pdf_falso) is None
