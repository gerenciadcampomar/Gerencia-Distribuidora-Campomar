import campomar_compras.provider_parsers as provider_parsers
from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.provider_parsers import (
    DOLZ_IVA_RE,
    DOLZ_LINE_RE,
    DOLZ_PERCEPCION_RE,
    DOLZ_SUBTOTAL_RE,
    DOLZ_TOTAL_RE,
    MD_LINE_RE,
    MD_TOTALES_RE,
    _normalizar_codigo_md,
    extraer_comprobante_md,
    extraer_fecha_md,
    NOSSO_ALICUOTA_RE,
    NOSSO_LINE_RE,
    RALUX_LINE_RE,
    RALUX_PERCEPCION_RE,
    RALUX_SUBTOTAL_RE,
    RALUX_TOTAL_RE,
    _num_eu,
    _num_md,
    _num_us,
    _sin_duplicado,
    _unir_celda_bronco,
    parse_lineas_bronco,
    parse_lineas_md,
    parse_totales_bronco,
    parse_totales_md,
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


# --- MD (Montpres S.A.) -------------------------------------------------
# Texto real extraído con pdfplumber de una factura MD (comprobante
# 00003-00013991) -- confirmado al centavo contra los 96 artículos y los
# totales reales del PDF.


def test_md_num_decide_separador_segun_lo_que_aparece():
    # Cantidad y los números grandes del bloque de totales solo traen
    # punto, que ahí ES el decimal (no hay miles) -- confirmado real que
    # tratarlos como formato europeo (_num_eu, que siempre saca el punto)
    # los multiplicaba por 100.
    assert _num_md("5. 00") == 5.0
    assert _num_md("11295671. 85") == 11295671.85
    # Precio/importe con los dos separadores sí son formato europeo normal.
    assert _num_md("11. 505, 49") == 11505.49
    # Precio chico, solo coma.
    assert _num_md("762, 90") == 762.90


def test_md_linea_simple():
    texto = "50. 00 15003/ 1 BULBO DE ELECTROVENT. 12. 070, 09 603. 504, 28"
    m = next(MD_LINE_RE.finditer(texto))
    assert _num_md(m.group("cantidad")) == 50.0
    assert m.group("codigo") == "15003/ 1"
    assert _num_md(m.group("precio")) == 12070.09
    assert _num_md(m.group("importe")) == 603504.28


def test_md_sufijo_de_letra_real_en_el_codigo():
    # Confirmado real: "17001" y "17001 I" son dos artículos DISTINTOS,
    # con precio distinto -- el sufijo "I" acá es parte real del código.
    texto = "3. 00 17001 I SOLENOI DE 12V 24. 011, 10 72. 033, 29"
    m = next(MD_LINE_RE.finditer(texto))
    assert m.group("codigo") == "17001 I"


def test_md_i_de_interruptor_no_es_sufijo_de_codigo():
    # Bug real encontrado: el PDF separa a veces la primera letra de una
    # palabra con un espacio de más (ej. "INT." -> "I NT."), que se podía
    # confundir con un sufijo de código de una letra como el de 17001 I.
    # Confirmado contra 32 casos reales: siempre que el patrón es
    # "<código> I NT." es la palabra "INT." partida, nunca un sufijo real.
    texto = "5. 00 14713 I NT. LUZ DE STOP 11. 505, 49 57. 527, 45"
    m = next(MD_LINE_RE.finditer(texto))
    assert m.group("codigo") == "14713"
    assert m.group("descripcion") == "I NT. LUZ DE STOP"


def test_md_normaliza_codigos_con_variante_de_precio_conocida():
    # Bug real: 15000-15004 y 15772 se facturan a veces con "/N" (lote más
    # barato) y a veces sin barra, pero en Centum es el mismo "Código propio
    # Proveedor" (confirmado real: SIEMPRE sin barra) -- si no se normaliza,
    # el "/N" nunca matchea contra el remito y se carga como faltante
    # aunque el artículo sí esté suscripto.
    assert _normalizar_codigo_md("15003/1") == "15003"
    assert _normalizar_codigo_md("15772/1") == "15772"


def test_md_no_normaliza_codigos_con_barra_fuera_de_la_lista_conocida():
    # Otros códigos con barra en la misma factura (ej. "24096/1") son
    # artículos distintos de verdad, con su propio "Código propio
    # Proveedor" con barra en Centum -- no hay que tocarlos.
    assert _normalizar_codigo_md("24096/1") == "24096/1"


def test_md_linea_con_variante_de_precio_se_parsea_con_codigo_normalizado(tmp_path, monkeypatch):
    texto = "50. 00 15003/ 1 BULBO DE ELECTROVENT. 12. 070, 09 603. 504, 28\n"
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    lineas = parse_lineas_md(tmp_path / "md_prueba.pdf")
    assert len(lineas) == 1
    assert lineas[0].codigo_proveedor == "15003"


def test_md_totales_sin_descuento_general(tmp_path, monkeypatch):
    # Bug real encontrado con la factura 992 (sin descuento general):
    # cuando no hay descuento, el bloque de totales imprime el mismo
    # subtotal DOS veces ("1363024.38 1363024.38 I VA...") en vez de las
    # tres columnas de una factura con descuento. El regex, al exigir
    # siempre un tercer número pegado a "IVA", retrocedía hasta la frase
    # en letras de la línea anterior ("...CON 50/1") y agarraba ese "1"
    # como si fuera el subtotal bruto -- multiplicaba todos los precios
    # por un factor disparatado (~1.345.134, confirmado real).
    texto = (
        "SON pesos : UN MILLÓN SEISCIENTOS CUARENTA Y NUEVE MIL DOSCIENTOS "
        "CINCUENTA Y NUEVE CON 50/1\n"
        "1363024. 38 1363024. 38 I VA 21, 0% 286. 235, 12\n"
        "1649259. 50\n"
    )
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    totales = parse_totales_md(tmp_path / "md_prueba.pdf")
    assert totales.subtotal_neto == 1363024.38
    assert totales.iva == 286235.12
    assert totales.total == 1649259.50


def test_md_sin_descuento_general_no_altera_precios_de_linea(tmp_path, monkeypatch):
    texto = (
        "5. 00 KI T3 KI T TERMOSTATO 39. 001, 30 195. 006, 50\n"
        "SON pesos : UN MILLÓN SEISCIENTOS CUARENTA Y NUEVE MIL DOSCIENTOS "
        "CINCUENTA Y NUEVE CON 50/1\n"
        "1363024. 38 1363024. 38 I VA 21, 0% 286. 235, 12\n"
        "1649259. 50\n"
    )
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    lineas = parse_lineas_md(tmp_path / "md_prueba.pdf")
    assert len(lineas) == 1
    assert lineas[0].codigo_proveedor == "KIT3"
    assert lineas[0].precio_unitario == 39001.30
    assert lineas[0].subtotal == 195006.50


def test_md_totales_no_se_cuela_como_linea_de_articulo(tmp_path, monkeypatch):
    # Bug real encontrado: la fila final de totales (mismo patrón de 5
    # números en columna que una línea de artículo) se colaba como si
    # fuera un artículo más -- ahora se recorta el texto antes de esa
    # fila.
    texto = (
        "5. 00 14713 I NT. LUZ DE STOP 11. 505, 49 57. 527, 45\n"
        "SON pesos :\n"
        "11295671. 85 6822585. 80 4473086. 05 I VA 21, 0% 939. 348, 07\n"
        "5412434. 12\n"
    )
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    lineas = parse_lineas_md(tmp_path / "md_prueba.pdf")
    assert len(lineas) == 1
    assert lineas[0].codigo_proveedor == "14713"


def test_md_aplica_el_descuento_general_del_pie_a_cada_linea(tmp_path, monkeypatch):
    # Bug real explicado por el usuario: a diferencia de los demás
    # proveedores, el precio impreso en cada línea de MD es de LISTA (sin
    # descuento) -- el descuento comercial general (60,4% en esta factura
    # real) se aplica una sola vez, al pie. Si no se aplica acá por línea,
    # se termina comparando precio de lista contra el neto que ya trae el
    # remito suscripto en Centum, y todo da precio_difiere.
    texto = (
        "5. 00 14713 I NT. LUZ DE STOP 11. 505, 49 57. 527, 45\n"
        "SON pesos :\n"
        "11295671. 85 6822585. 80 4473086. 05 I VA 21, 0% 939. 348, 07\n"
        "5412434. 12\n"
    )
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    lineas = parse_lineas_md(tmp_path / "md_prueba.pdf")
    fraccion_neta = 4473086.05 / 11295671.85
    assert lineas[0].precio_unitario == round(11505.49 * fraccion_neta, 2)
    assert lineas[0].subtotal == round(57527.45 * fraccion_neta, 2)


def test_md_totales_reales(tmp_path, monkeypatch):
    texto = (
        "SON pesos : CINCO MILLONES CUATROCIENTOS DOCE MIL CUATROCIENTOS "
        "TREINTA Y CUATRO CON 12/100\n"
        "11295671. 85 6822585. 80 4473086. 05 I VA 21, 0% 939. 348, 07\n"
        "5412434. 12\n"
    )
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    totales = parse_totales_md(tmp_path / "md_prueba.pdf")
    assert totales.subtotal_neto == 4473086.05
    assert totales.iva == 939348.07
    assert totales.total == 5412434.12


def test_md_fecha_sin_etiqueta_ni_barras(tmp_path, monkeypatch):
    # Bug real encontrado: el extractor de fecha genérico espera la
    # palabra "Fecha" pegada al número y con barras ("Fecha: 04/11/2026")
    # -- el PDF de MD no trae la etiqueta adyacente (queda en un cuadro
    # de texto separado) y usa espacios en vez de barras ("04 11 26").
    # Se la ubica por posición fija, justo después del número de
    # comprobante.
    texto = "A F A C T U R A\nCod. 001\nNº 00003- 00013991\n04 11 26\nResponsabl e i nscr i pt o"
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    fecha = extraer_fecha_md(tmp_path / "md_prueba.pdf")
    assert fecha.isoformat() == "2026-11-04"


def test_md_comprobante_punto_venta_y_numero(tmp_path, monkeypatch):
    texto = "A F A C T U R A\nCod. 001\nNº 00003- 00013991\n04 11 26\n"
    monkeypatch.setattr(provider_parsers, "extraer_texto", lambda pdf_path: texto)
    assert extraer_comprobante_md(tmp_path / "md_prueba.pdf") == ("00003", "00013991")
