from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import match_remitos_contra_factura
from campomar_compras.models import InvoiceLine, InvoiceTotals, RemitoArticleLine
from campomar_compras.validator import validar_subtotal


def _mapping(tmp_path) -> CodeMappingStore:
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    store.learn("RNMI 37A859", "RERNMI37A859")
    return store


def test_validacion_ok_dentro_de_tolerancia(tmp_path):
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 100.0)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 100.5)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=100.5)
    resultado = validar_subtotal(resultados, totales, tolerancia=1.0)
    assert resultado.ok


def test_validacion_falla_fuera_de_tolerancia(tmp_path):
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 100.0)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 100.0)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=250.0)
    resultado = validar_subtotal(resultados, totales, tolerancia=1.0)
    assert not resultado.ok
    assert resultado.diferencia == -150.0


def test_falla_si_nada_matchea_con_la_factura_aunque_de_cero_contra_cero(tmp_path):
    # Caso real reportado: el parser no leyó ninguna línea de la factura
    # (subtotal 0) y el remito entero quedó como excedente -- "0 contra
    # 0" pasaba la tolerancia y daba un PASS falso. Nunca tiene que
    # pasar, aunque los números den iguales.
    remitos = [RemitoArticleLine("XYZ", "Artículo cualquiera", "r1", 1, 100.0)]
    facturas: list[InvoiceLine] = []
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=0.0)
    resultado = validar_subtotal(resultados, totales, tolerancia=1.0)
    assert not resultado.ok
    assert "excedente" in resultado.detalle.lower()


def test_falla_si_subtotal_factura_da_cero(tmp_path):
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 100.0)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 0.0)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=0.0)
    resultado = validar_subtotal(resultados, totales, tolerancia=1.0)
    assert not resultado.ok
    assert "$0" in resultado.detalle
