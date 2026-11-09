import campomar_compras.provider_parsers as provider_parsers
from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.provider_parsers import (
    DOLZ_IVA_RE,
    DOLZ_LINE_RE,
    DOLZ_PERCEPCION_RE,
    DOLZ_SUBTOTAL_RE,
    DOLZ_TOTAL_RE,
    NOSSO_ALICUOTA_RE,
    NOSSO_LINE_RE,
    RALUX_LINE_RE,
    RALUX_PERCEPCION_RE,
    RALUX_SUBTOTAL_RE,
    RALUX_TOTAL_RE,
    _num_eu,
    _num_us,
    _sin_duplicado,
    _unir_celda_bronco,
    parse_lineas_bronco,
    parse_totales_bronco,
    regla_codigo_ralux,
)


def test_nosso_linea_codigo_con_espacio():
    m = NOSSO_LINE_RE.match(
        "ZEN 5562 1 Polea rueda libre CBA - 30/07/26 26017IC050 33,969.66 21.0 33,969.66"
    )
    assert m
    assert m.group("codigo") == "ZEN 5562"
    assert m.group("cantidad") == "1"
    assert _num_us(m.group("precio")) == 33969.66
    assert _num_us(m.group("total")) == 33969.66


def test_nosso_linea_codigo_con_numero_embebido():
    m = NOSSO_LINE_RE.match("EF 12 26 Regulador de voltaje 25,573.60 10.5 664,913.60")
    assert m
    assert m.group("codigo") == "EF 12"
    assert m.group("cantidad") == "26"
    assert _num_us(m.group("total")) == 664913.60


def test_nosso_linea_con_hyphen_en_codigo():
    m = NOSSO_LINE_RE.match("SXB-A 2 Cortacorriente 84,557.74 10.5 169,115.48")
    assert m
    assert m.group("codigo") == "SXB-A"


def test_nosso_alicuota_suma_gravado():
    texto = (
        "IVA - 21% 21.00% 24,979,506.60 5,245,696.39\n"
        "IVA - 10,5% 10.50% 26,907,153.43 2,825,251.11\n"
    )
    matches = list(NOSSO_ALICUOTA_RE.finditer(texto))
    assert len(matches) == 2
    gravado = sum(_num_us(m.group("gravado")) for m in matches)
    importe = sum(_num_us(m.group("importe")) for m in matches)
    assert round(gravado, 2) == 51886660.03
    assert round(importe, 2) == 8070947.50


def test_ralux_linea_codigo_con_descuento():
    m = RALUX_LINE_RE.match(
        "REL247-129 RELAY UNA SALIDA FORD 5,000 $ 11.525,0000 52,50 $ 27.371,88 21.0% $ 33.119,97"
    )
    assert m
    assert m.group("codigo") == "REL247-129"
    assert _num_eu(m.group("cantidad")) == 5.0
    assert _num_eu(m.group("subtotal")) == 27371.88
    assert _num_eu(m.group("total")) == 33119.97


def test_ralux_totales():
    # Orden real observado en el PDF: el importe va antes de la jurisdicción.
    texto = (
        "SUBTOTAL $ 17.635.335,36\n"
        "IVA 10,5% $ 260.263,16\n"
        "IVA 21% $ 3.182.894,10\n"
        "PERCEPCION IIBB $ 529.060,06\nCABA\n"
        "TOTAL $ 21.607.552,68\n"
    )
    assert _num_eu(RALUX_SUBTOTAL_RE.search(texto).group(1)) == 17635335.36
    assert _num_eu(RALUX_TOTAL_RE.search(texto).group(1)) == 21607552.68
    percepcion = RALUX_PERCEPCION_RE.search(texto)
    assert percepcion.group("jurisdiccion") == "CABA"
    assert _num_eu(percepcion.group("importe")) == 529060.06


def test_ralux_regla_codigo():
    assert regla_codigo_ralux("TEM043-T-9082") == "RAL9082"
    assert regla_codigo_ralux("REL247-129") == "RAL129"


def test_dolz_linea():
    # Texto real extraído con pdfplumber de una factura DOLZ (comprobante
    # 0006-00022920).
    m = DOLZ_LINE_RE.match("KD230 FULLKIT A179 VW 1.6 N POLO-GOLF 15,00 D UN 48.260,00 2 723.900,00")
    assert m
    assert m.group("codigo") == "KD230"
    assert m.group("descripcion") == "FULLKIT A179 VW 1.6 N POLO-GOLF"
    assert _num_eu(m.group("cantidad")) == 15.0
    assert _num_eu(m.group("precio")) == 48260.0
    assert _num_eu(m.group("total")) == 723900.0


def test_dolz_totales():
    texto = (
        "Subtotal 723.900,00\n"
        "I.V.A. 21,00 % 152.019,00\n"
        "Percep. II.BB. Bs. As. 1,75 % 12.668,25\n"
        "Percep. II.BB. C.A.B.A. 6,00 % 43.434,00\n"
        "TOTAL 932.021,25\n"
    )
    subtotal = next(m for m in texto.splitlines() if DOLZ_SUBTOTAL_RE.match(m))
    assert _num_eu(DOLZ_SUBTOTAL_RE.match(subtotal).group(1)) == 723900.0
    total = next(m for m in texto.splitlines() if DOLZ_TOTAL_RE.match(m))
    assert _num_eu(DOLZ_TOTAL_RE.match(total).group(1)) == 932021.25
    iva = next(m for m in texto.splitlines() if DOLZ_IVA_RE.match(m))
    assert _num_eu(DOLZ_IVA_RE.match(iva).group(1)) == 152019.0

    percepciones = [DOLZ_PERCEPCION_RE.match(linea) for linea in texto.splitlines()]
    percepciones = [m for m in percepciones if m]
    assert len(percepciones) == 2
    assert percepciones[0].group("jurisdiccion") == "Bs. As."
    assert _num_eu(percepciones[0].group("importe")) == 12668.25
    assert percepciones[1].group("jurisdiccion") == "C.A.B.A."
    assert _num_eu(percepciones[1].group("importe")) == 43434.0
    assert regla_codigo_ralux("MOD027-M-8921") == "RAL8921"


def test_ralux_regla_codigo_via_mapping_store(tmp_path):
    store = CodeMappingStore("ralux", providers_dir=tmp_path, regla=regla_codigo_ralux)
    assert store.get_centum_code("TEM043-T-9082") == "RAL9082"
    # un mapeo aprendido/confirmado a mano tiene prioridad sobre la regla
    store.learn("TEM043-T-9082", "RAL-EXCEPCION")
    assert store.get_centum_code("TEM043-T-9082") == "RAL-EXCEPCION"


def test_nosso_sin_duplicado_recorta_segunda_copia():
    texto = "factura original\nDuplicado\nfactura repetida"
    assert _sin_duplicado(texto) == "factura original\n"


def test_bronco_unir_celda_corta_numero_sin_dejar_espacio():
    # Confirmado real: con columnas angostas (PDF exportado desde Word),
    # pdfplumber puede cortar el contenido de una celda en medio de un
    # número -- el salto hay que sacarlo sin dejar espacio ahí.
    assert _unir_celda_bronco("$\n10.665,0\n0") == "$ 10.665,00"
    assert _unir_celda_bronco("1401\nJRG") == "1401 JRG"
    assert _unir_celda_bronco("INTERRUPTOR DE FRENO DODGE 1500 -\nCITROEN 3CV") == (
        "INTERRUPTOR DE FRENO DODGE 1500 - CITROEN 3CV"
    )
    assert _unir_celda_bronco(None) == ""


def test_bronco_parsea_tabla_por_celdas(tmp_path, monkeypatch):
    # Tabla real (repr() de pdfplumber.extract_table()) de un comprobante
    # de prueba exportado desde Word con columnas angostas -- confirmado
    # que corta hasta los números en medio ("10.665,0" / "0").
    tabla = [
        ["Artícul\no", "Descripción", "Cantida\nd", "Precio\nunitario", "Precio\ntotal"],
        ["1934", "INTERRUPTOR DE FRENO DODGE 1500 -\nCITROEN 3CV", "10", "$\n1.333,13", "$\n10.665,0\n0"],
        ["1401\nJRG", "INTERRUPTOR DE RETROMARCHA FORD\nFALCON HASTA ´82", "10", "$\n3.851,52", "$\n30.812,1\n9"],
        ["RET405", "INTERRUPTOR DE RETROMARCHA FORD\nTAUNUS HASTA ´81", "5", "$\n2.329,72", "$\n9.318,90"],
    ]
    monkeypatch.setattr(provider_parsers, "_extraer_filas_tabla", lambda pdf_path: tabla)
    lineas = parse_lineas_bronco(tmp_path / "bronco_prueba.pdf")

    assert len(lineas) == 3  # la fila de encabezado se descarta

    assert lineas[0].codigo_proveedor == "1934"
    assert lineas[0].descripcion == "INTERRUPTOR DE FRENO DODGE 1500 - CITROEN 3CV"
    assert lineas[0].cantidad == 10.0
    assert lineas[0].precio_unitario == 1333.13
    assert lineas[0].subtotal == 10665.00

    assert lineas[1].codigo_proveedor == "1401 JRG"
    assert lineas[1].cantidad == 10.0
    assert lineas[1].subtotal == 30812.19

    assert lineas[2].codigo_proveedor == "RET405"
    assert lineas[2].subtotal == 9318.90


def test_bronco_totales_sin_subtotal_explicito(tmp_path, monkeypatch):
    # Autopartes Bronco no trae renglones de Subtotal/IVA/Total -- se
    # calcula sumando el "Precio total" de cada línea, con IVA en 0 (no
    # hay forma de saberlo desde este papel).
    tabla = [
        ["Artículo", "Descripción", "Cantidad", "Precio unitario", "Precio total"],
        ["1935", "INTERRUPTOR DE FRENO VW GACEL / MECANICO", "12", "$ 3.063,62", "$ 29.410,79"],
        ["1718", "INTERRUPTOR DE PUERTA OPEL K-180 / YNT718", "5", "$ 1.496,31", "$ 5.985,26"],
    ]
    monkeypatch.setattr(provider_parsers, "_extraer_filas_tabla", lambda pdf_path: tabla)
    totales = parse_totales_bronco(tmp_path / "bronco_prueba.pdf")
    assert totales.subtotal_neto == 35396.05
    assert totales.iva == 0.0
    assert totales.total == 35396.05
