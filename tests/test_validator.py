from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import match_remitos_contra_factura
from campomar_compras.models import InvoiceLine, InvoiceTotals, RemitoArticleLine
from campomar_compras.validator import validar_subtotal


def _mapping(tmp_path) -> CodeMappingStore:
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    store.learn("RNMI 37A859", "RERNMI37A859")
    return store


def test_subtotal_sistema_real_pisa_el_calculo_propio(tmp_path):
    # Caso real reportado: nuestra propia cuenta en Python daba un
    # subtotal muy cercano a la factura (diferencia silenciosa, <$1),
    # pero el subtotal REAL que Centum calculó (con su propio redondeo
    # al aplicar el descuento) estaba a ~$164 de la factura -- más de
    # $1, había que aclararlo. Si se pasa subtotal_sistema_real, tiene
    # que usarse ESE en vez de recalcular con subtotal_propuesto().
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 184590.58)]  # igual a la factura
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 184590.58)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=184590.58)
    resultado = validar_subtotal(resultados, totales, subtotal_sistema_real=184426.63)
    assert resultado.ok
    assert resultado.subtotal_sistema == 184426.63
    assert "$163" in resultado.detalle


def test_validacion_ok_diferencia_chica_no_se_menciona(tmp_path):
    # Pedido del usuario: hasta $1 de diferencia, el mensaje no tiene que
    # aclarar nada -- es tan chico que no vale la pena mencionarlo.
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 100.0)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 100.5)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=100.5)
    resultado = validar_subtotal(resultados, totales, tolerancia=1.0)
    assert resultado.ok
    assert "$" not in resultado.detalle


def test_validacion_ok_diferencia_mayor_a_un_peso_se_aclara(tmp_path):
    # Pedido del usuario: más de $1 (pero dentro de tolerancia), el
    # mensaje tiene que decir OK pero aclarando cuánto no cierra.
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 100.0)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 102.0)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=102.0)
    resultado = validar_subtotal(resultados, totales, tolerancia=5.0)
    assert resultado.ok
    assert "$2.00" in resultado.detalle


def test_tolerancia_por_porcentaje_cubre_redondeo_de_centum(tmp_path):
    # Caso real reportado: Centum calcula descuentos con un redondeo
    # propio (un 20% nominal que en la práctica da ~20,08%), generando
    # una diferencia chica pero real en facturas grandes -- no es un
    # error de verdad. La tolerancia por peso fijo no alcanzaba para una
    # factura de ~$184.000 con ~$164 de diferencia (0,09%).
    remitos = [RemitoArticleLine("RERNMI37A859", "Regulador", "r1", 1, 184426.63)]
    facturas = [InvoiceLine("RNMI 37A859", "Regulador", 1, 184590.58)]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))

    totales = InvoiceTotals(subtotal_neto=184590.58)
    resultado = validar_subtotal(resultados, totales)  # tolerancia automática
    assert resultado.ok
    assert "0.09%" in resultado.detalle or "0.08%" in resultado.detalle


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
