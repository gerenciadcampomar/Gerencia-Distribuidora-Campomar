import campomar_compras.extraccion_generica as extraccion_generica
from campomar_compras.extraccion_generica import (
    _buscar_codigos_sueltos,
    _es_token_codigo,
    _lineas_por_ancla_de_cantidad,
    extraer_lineas_generico,
)


def test_es_token_codigo_reconoce_alfanumerico_y_numerico():
    assert _es_token_codigo("P4117")
    assert _es_token_codigo("101162")
    assert _es_token_codigo("P2344C")
    assert _es_token_codigo("CS1009/1")
    assert not _es_token_codigo("TORNILLOS")
    assert not _es_token_codigo("12")  # muy corto para ser código real


def test_ancla_de_cantidad_codigo_despues(tmp_path, monkeypatch):
    # Caso real confirmado (Some): "cantidad código descripción precio importe".
    texto = "1.00 101162 CUBRE IMPULSOR TRAFIC 29,739.821 29,739.82\n"
    monkeypatch.setattr(extraccion_generica, "extraer_texto", lambda pdf_path: texto)
    lineas = _lineas_por_ancla_de_cantidad(tmp_path / "prueba.pdf")
    assert len(lineas) == 1
    assert lineas[0].codigo_proveedor == "101162"
    assert lineas[0].cantidad == 1.0


def test_ancla_de_cantidad_codigo_antes(tmp_path, monkeypatch):
    # Caso real: código y cantidad pegados uno al lado del otro, con la
    # descripción recién después (a diferencia del caso con la
    # descripción EN MEDIO de los dos, que la pasada por ancla no
    # resuelve -- ver test_buscar_codigos_sueltos_* para ese caso límite).
    texto = "P4117 3.00 PORTA ESCOBILLAS 4,884.92\n"
    monkeypatch.setattr(extraccion_generica, "extraer_texto", lambda pdf_path: texto)
    lineas = _lineas_por_ancla_de_cantidad(tmp_path / "prueba.pdf")
    assert len(lineas) == 1
    assert lineas[0].codigo_proveedor == "P4117"
    assert lineas[0].cantidad == 3.0


def test_ancla_de_cantidad_no_confunde_un_precio_con_cantidad():
    # "29,739.821" no debe interpretarse como un ancla de cantidad (no
    # termina en ",00"/" .00" exacto) -- confirmado con error real que
    # un precio con esos ceros de casualidad podía confundirse.
    texto = "1.00 101162 CUBRE IMPULSOR 29,739.821 29,739.82\n"
    import re

    from campomar_compras.extraccion_generica import _CANTIDAD_ANCLA_RE

    matches = [m.group(0) for m in _CANTIDAD_ANCLA_RE.finditer(texto)]
    assert matches == ["1.00"]


def test_buscar_codigos_sueltos_encuentra_codigo_pegado_en_descripcion(tmp_path, monkeypatch):
    # Caso límite confirmado por el usuario: a veces el código queda
    # dentro de la descripción, lejos de la cantidad -- la pasada por
    # ancla no lo encuentra, pero la búsqueda de texto sí.
    texto = "Repuesto RNMI37A859 para regulador, entregado en caja aparte\n"
    monkeypatch.setattr(extraccion_generica, "extraer_texto", lambda pdf_path: texto)
    lineas = _buscar_codigos_sueltos(tmp_path / "prueba.pdf", ["RNMI37A859", "OTROCODIGO"])
    codigos = [l.codigo_proveedor for l in lineas]
    assert codigos == ["RNMI37A859"]
    assert lineas[0].cantidad == 1.0  # sin confirmar, valor provisorio


def test_buscar_codigos_sueltos_no_matchea_substring_parcial(tmp_path, monkeypatch):
    # "101" no debería matchear dentro de "101162" (límite de palabra).
    texto = "Código 101162 en el remito\n"
    monkeypatch.setattr(extraccion_generica, "extraer_texto", lambda pdf_path: texto)
    lineas = _buscar_codigos_sueltos(tmp_path / "prueba.pdf", ["101"])
    assert lineas == []


def test_extraer_lineas_generico_combina_ancla_y_busqueda_de_texto(tmp_path, monkeypatch):
    texto = (
        "1.00 101162 CUBRE IMPULSOR 29,739.82 29,739.82\n"
        "Nota: también se incluyó CS1009/1 por separado, sin cantidad clara en tabla\n"
    )
    monkeypatch.setattr(extraccion_generica, "extraer_texto", lambda pdf_path: texto)
    lineas = extraer_lineas_generico(tmp_path / "prueba.pdf", codigos_remito=["101162", "CS1009/1"])
    codigos = {l.codigo_proveedor: l.cantidad for l in lineas}
    assert codigos["101162"] == 1.0
    assert codigos["CS1009/1"] == 1.0  # por búsqueda de texto, sin confirmar
