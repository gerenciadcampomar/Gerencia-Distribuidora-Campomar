from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import (
    match_remitos_contra_factura,
    proponer_conceptos_faltantes,
    subtotal_propuesto,
)
from campomar_compras.models import InvoiceLine, MatchStatus, RemitoArticleLine


def _mapping(tmp_path) -> CodeMappingStore:
    store = CodeMappingStore("nossovitch", providers_dir=tmp_path)
    store.learn("RNMI 37A859", "RERNMI37A859")
    store.learn("ZEN 5562", "Z5562")
    store.learn("837", "Z837")
    return store


def test_todo_coincide(tmp_path):
    remitos = [
        RemitoArticleLine("RERNMI37A859", "Regulador", "00001-04062026", 1, 27762.2, dto1=30, dto2=5),
        RemitoArticleLine("Z5562", "Polea", "00009-01489137", 1, 60660.1, dto1=30, dto2=20),
    ]
    facturas = [
        InvoiceLine("RNMI 37A859", "Regulador", 1, 27762.2 * 0.665),
        InvoiceLine("ZEN 5562", "Polea", 1, 60660.1 * 0.56),
    ]

    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    assert all(r.status == MatchStatus.OK for r in resultados)
    assert proponer_conceptos_faltantes(resultados) == []


def test_matching_directo_por_codigo_propio_proveedor(tmp_path):
    """Cuando Centum trae el "Código propio Proveedor" (confirmado real
    para Nosso: BNBJ 12, remito 00009-00150930, factura 0015-00141664),
    el matching usa ese código directo y no necesita la tabla aprendida."""
    remitos = [
        RemitoArticleLine(
            "BNBJ12V",
            "Juego bocinas disco 12V",
            "00009-00150930",
            3,
            25037.94,
            dto1=33.5,
            codigo_propio_proveedor="BNBJ 12",
        ),
    ]
    facturas = [
        InvoiceLine("BNBJ 12", "Bocina blindada", 3, 16650.23),
    ]

    # Mapping vacío a propósito: no tiene aprendido "BNBJ 12" -> "BNBJ12V",
    # así que si el match funcionara por la tabla aprendida, fallaría.
    resultados = match_remitos_contra_factura(remitos, facturas, CodeMappingStore("nosso", providers_dir=tmp_path))
    assert resultados[0].status == MatchStatus.OK


def test_excedente_y_faltante(tmp_path):
    remitos = [
        RemitoArticleLine("Z837", "Impulsor", "00009-01489138", 1, 16386.1, dto1=30, dto2=20),
    ]
    facturas = [
        InvoiceLine("XYZ999", "Repuesto no remitido", 2, 500.0),
    ]

    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    statuses = {r.status for r in resultados}
    assert MatchStatus.EXCEDENTE in statuses
    assert MatchStatus.FALTANTE in statuses

    conceptos = proponer_conceptos_faltantes(resultados)
    assert len(conceptos) == 1
    assert conceptos[0].cantidad == 2
    assert conceptos[0].precio == 500.0
    assert "XYZ999" in conceptos[0].observaciones


def test_excedente_parcial_remito_trae_mas_que_la_factura(tmp_path):
    # Confirmado con un comprobante real de prueba (Autopartes Bronco,
    # código STOP937): se facturan 10, el proveedor entrega 12 -> hay que
    # ajustar la cantidad a mano a lo facturado, dejando 2 pendientes.
    remitos = [RemitoArticleLine("Z837", "Impulsor", "r1", 3, 100.0)]
    facturas = [InvoiceLine("837", "Impulsor", 2, 100.0)]

    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    assert resultados[0].status == MatchStatus.EXCEDENTE_PARCIAL
    assert resultados[0].cantidad_diferencia == 1


def test_faltante_parcial_remito_trae_menos_que_la_factura(tmp_path):
    # Confirmado con el mismo comprobante de prueba (código STOP936): se
    # facturan 10, el proveedor entrega 8 -> la diferencia se carga como
    # concepto "Mercaderías varias", igual que un faltante completo pero
    # solo por lo que falta.
    remitos = [RemitoArticleLine("Z837", "Impulsor", "r1", 2, 100.0)]
    facturas = [InvoiceLine("837", "Impulsor", 3, 100.0)]

    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    assert resultados[0].status == MatchStatus.FALTANTE_PARCIAL
    assert resultados[0].cantidad_diferencia == 1

    conceptos = proponer_conceptos_faltantes(resultados)
    assert len(conceptos) == 1
    assert conceptos[0].cantidad == 1
    assert conceptos[0].precio == 100.0


def test_faltante_con_descuento_comercial_usa_precio_neto(tmp_path):
    # Caso real reportado: factura de prueba con descuento comercial del
    # 20% entre precio unitario y precio total (STOP935: precio unitario
    # 3063.62, cantidad 12, total facturado 29410.79 -- NO 36763.44 que
    # daría cantidad*precio_unitario sin descontar). Conceptos no tiene
    # columna de descuento aparte como el remito, así que el precio que
    # se carga ahí tiene que ser el neto (subtotal/cantidad), si no
    # Centum calcula un subtotal inflado.
    facturas = [InvoiceLine("STOP935", "Interruptor", 12, 3063.62, subtotal=29410.79)]
    resultados = match_remitos_contra_factura([], facturas, _mapping(tmp_path))
    assert resultados[0].status == MatchStatus.FALTANTE

    conceptos = proponer_conceptos_faltantes(resultados)
    assert len(conceptos) == 1
    assert conceptos[0].cantidad == 12
    assert round(conceptos[0].precio, 2) == round(29410.79 / 12, 2)
    # Verifica que cantidad * precio reconstruye el total facturado, no
    # el inflado sin descuento.
    assert round(conceptos[0].cantidad * conceptos[0].precio, 2) == 29410.79


def test_mismo_codigo_en_dos_renglones_del_remito_se_suma(tmp_path):
    # Bug real encontrado con un comprobante de prueba (código YNT722): el
    # proveedor entrega el mismo artículo en dos renglones separados del
    # remito (5 + 5), pero la factura lo pide en un solo renglón de 10.
    # Antes de agrupar por código, esto daba un falso FALTANTE_PARCIAL (la
    # primera línea) y un EXCEDENTE espurio (la segunda, porque la factura
    # ya se había "gastado" en la primera).
    remitos = [
        RemitoArticleLine("Z837", "Impulsor", "r1", 5, 100.0),
        RemitoArticleLine("Z837", "Impulsor", "r1", 5, 100.0),
    ]
    facturas = [InvoiceLine("837", "Impulsor", 10, 100.0)]

    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    assert len(resultados) == 1
    assert resultados[0].status == MatchStatus.OK
    assert len(resultados[0].remito_lines) == 2


def test_subtotal_propuesto_suma_ok_y_faltantes(tmp_path):
    remitos = [RemitoArticleLine("Z837", "Impulsor", "r1", 1, 100.0)]
    facturas = [
        InvoiceLine("837", "Impulsor", 1, 100.0),
        InvoiceLine("XYZ999", "No remitido", 1, 50.0),
    ]
    resultados = match_remitos_contra_factura(remitos, facturas, _mapping(tmp_path))
    assert subtotal_propuesto(resultados) == 150.0
