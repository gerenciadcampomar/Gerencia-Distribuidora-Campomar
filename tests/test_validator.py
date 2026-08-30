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
