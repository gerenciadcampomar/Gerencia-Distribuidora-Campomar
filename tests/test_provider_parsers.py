from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.provider_parsers import (
    NOSSO_ALICUOTA_RE,
    NOSSO_LINE_RE,
    RALUX_LINE_RE,
    RALUX_PERCEPCION_RE,
    RALUX_SUBTOTAL_RE,
    RALUX_TOTAL_RE,
    _num_eu,
    _num_us,
    _sin_duplicado,
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
