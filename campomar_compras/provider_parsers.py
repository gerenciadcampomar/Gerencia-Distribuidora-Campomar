"""Parsers específicos por proveedor, calibrados con facturas reales.

Cada proveedor imprime su factura con un layout distinto (columna de código
separada vs. código pegado a la descripción, formato numérico
estadounidense vs. europeo, bloque de totales prolijo vs. desordenado por la
extracción de texto). El regex genérico de pdf_extractor.py no alcanza para
ninguno de estos casos reales, así que cada proveedor tiene su propio par de
funciones (parse_lineas, parse_totales) registradas en PROVIDER_PARSERS.

Limitación conocida: si una descripción se corta en dos líneas del PDF (pasa
en algunas facturas de Ralux), esa línea se pierde silenciosamente en vez de
reconstruirse — falta ver más ejemplos para resolverlo bien.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from campomar_compras.models import InvoiceLine, InvoiceTotals, PedidoProveedorLine, RegimenEspecial
from campomar_compras.pdf_extractor import extraer_texto


def _num_us(raw: str) -> float:
    """Formato de Nosso: miles con coma, decimales con punto (33,969.66)."""
    return float(raw.replace(",", ""))


def _num_eu(raw: str) -> float:
    """Formato de Ralux: miles con punto, decimales con coma (17.635.335,36)."""
    return float(raw.replace(".", "").replace(",", "."))


# --- Nosso ------------------------------------------------------------

_CODE_TOKEN = r"[A-Z0-9][A-Z0-9\-]*"
NOSSO_LINE_RE = re.compile(
    rf"^(?P<codigo>{_CODE_TOKEN}(?:\s+{_CODE_TOKEN})*)\s+(?P<cantidad>\d+)\s+"
    r"(?P<resto>[A-Z][a-z].*?)\s+(?P<precio>[\d,]+\.\d{2})\s+"
    r"(?P<ivapct>\d{1,2}\.\d)\s+(?P<total>[\d,]+\.\d{2})\s*$"
)
NOSSO_ALICUOTA_RE = re.compile(
    r"IVA\s*-\s*[\d,]+%\s+[\d.]+%\s+(?P<gravado>[\d,]+\.\d{2})\s+(?P<importe>[\d,]+\.\d{2})"
)


def _sin_duplicado(texto: str) -> str:
    """Nosso imprime la factura dos veces en el mismo PDF (Original +
    Duplicado) — sin recortar, cada línea y cada total se contaría el doble."""
    return texto.split("Duplicado", 1)[0]


def parse_lineas_nosso(pdf_path: Path) -> list[InvoiceLine]:
    texto = _sin_duplicado(extraer_texto(pdf_path))
    lineas = []
    for raw_line in texto.splitlines():
        m = NOSSO_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        lineas.append(
            InvoiceLine(
                codigo_proveedor=m.group("codigo").strip(),
                descripcion=m.group("resto").strip(),
                cantidad=_num_us(m.group("cantidad")),
                precio_unitario=_num_us(m.group("precio")),
            )
        )
    return lineas


def parse_totales_nosso(pdf_path: Path) -> InvoiceTotals:
    texto = _sin_duplicado(extraer_texto(pdf_path))
    gravado_total = 0.0
    iva_total = 0.0
    for m in NOSSO_ALICUOTA_RE.finditer(texto):
        gravado_total += _num_us(m.group("gravado"))
        iva_total += _num_us(m.group("importe"))
    if gravado_total == 0.0:
        raise ValueError("No se encontró la tabla de Alícuota/Gravado en el PDF de Nosso.")
    return InvoiceTotals(
        subtotal_neto=round(gravado_total, 2),
        iva=round(iva_total, 2),
        total=round(gravado_total + iva_total, 2),
    )


# --- Ralux --------------------------------------------------------------

RALUX_LINE_RE = re.compile(
    r"^(?P<codigo>[A-Z0-9]+(?:-[A-Z0-9]+)+)\s+(?P<descripcion>.+?)\s+"
    r"(?P<cantidad>\d+,\d{3})\s+\$\s*(?P<precio>[\d.]+,\d{4})\s+"
    r"(?P<disc>\d+,\d{2})\s+\$\s*(?P<subtotal>[\d.]+,\d{2})\s+"
    r"(?P<ivapct>\d+\.\d)%\s+\$\s*(?P<total>[\d.]+,\d{2})\s*$"
)
RALUX_SUBTOTAL_RE = re.compile(r"\bSUBTOTAL\s*\$\s*([\d.]+,\d{2})")
RALUX_TOTAL_RE = re.compile(r"\bTOTAL\s*\$\s*([\d.]+,\d{2})")
RALUX_IVA_RE = re.compile(r"\bIVA\s+[\d,]+%\s*\$\s*([\d.]+,\d{2})")
RALUX_PERCEPCION_RE = re.compile(
    r"PERCEPCION\s+IIBB\s*\$\s*(?P<importe>[\d.]+,\d{2})\s*\n?\s*(?P<jurisdiccion>[A-ZÁÉÍÓÚÑ]+)?"
)


def parse_lineas_ralux(pdf_path: Path) -> list[InvoiceLine]:
    texto = extraer_texto(pdf_path)
    lineas = []
    for raw_line in texto.splitlines():
        m = RALUX_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        cantidad = _num_eu(m.group("cantidad"))
        subtotal = _num_eu(m.group("subtotal"))
        lineas.append(
            InvoiceLine(
                codigo_proveedor=m.group("codigo").strip(),
                descripcion=m.group("descripcion").strip(),
                cantidad=cantidad,
                precio_unitario=round(subtotal / cantidad, 4) if cantidad else 0.0,
                subtotal=subtotal,
            )
        )
    return lineas


def parse_totales_ralux(pdf_path: Path) -> InvoiceTotals:
    texto = extraer_texto(pdf_path)
    m_subtotal = RALUX_SUBTOTAL_RE.search(texto)
    m_total = RALUX_TOTAL_RE.search(texto)
    if not m_subtotal or not m_total:
        raise ValueError("No se pudo extraer SUBTOTAL/TOTAL del PDF de Ralux.")
    iva_total = sum(_num_eu(m.group(1)) for m in RALUX_IVA_RE.finditer(texto))
    return InvoiceTotals(
        subtotal_neto=_num_eu(m_subtotal.group(1)),
        iva=round(iva_total, 2),
        total=_num_eu(m_total.group(1)),
    )


def parse_percepciones_ralux(pdf_path: Path) -> list[RegimenEspecial]:
    """Percepciones de Ingresos Brutos por jurisdicción -> Regímenes Especiales."""
    texto = extraer_texto(pdf_path)
    percepciones = []
    for m in RALUX_PERCEPCION_RE.finditer(texto):
        jurisdiccion = (m.group("jurisdiccion") or "").strip() or "Sin especificar"
        percepciones.append(
            RegimenEspecial(jurisdiccion=jurisdiccion, importe=_num_eu(m.group("importe")))
        )
    return percepciones


def regla_codigo_ralux(codigo_proveedor: str) -> str:
    """Ralux: el código Centum es "RAL" + el último segmento tras el último
    guion del código propio (ej. "TEM043-T-9082" -> "RAL9082")."""
    ultimo_segmento = codigo_proveedor.strip().split("-")[-1]
    return f"RAL{ultimo_segmento}"


# --- ByC (reporte de Pedido, no es la factura AFIP en sí) ---------------
#
# Este proveedor factura siempre "en forma alternativa" (no manda comprobante
# oficial por estos artículos). El campo "Artículo" trae 3 valores pegados:
# código con cero a la izquierda (4 dígitos) + variante (2 dígitos) + el
# mismo código sin cero a la izquierda (3 o 4 dígitos):
#   1349111349 -> 1349 | 11 | 1349   (variante 11 = "1X")
#   094712947  -> 0947 | 12 | 947    (variante 12 = "2X")
#   094710947  -> 0947 | 10 | 947    (variante 10 = "STD")
#
# OJO: la variante NO es un packaging/cantidad por bulto — es el espesor de
# pared del buje (a mayor espesor, menor diámetro interno). Dos líneas con
# el mismo código base pero variante distinta son artículos DIFERENTES, no
# el mismo artículo con otro precio por volumen. El código real a mapear
# contra Centum es la combinación (código base, variante), y como no hay
# una fórmula conocida para ese mapeo, hace falta una tabla aprendida igual
# que con Nossovitch.
VARIANTES_BYC = {
    "10": "STD",
    "11": "1X",
    "12": "2X",
    "13": "3X",
    "14": "4X",
    "16": "6X",
    "17": "10X",
}

BYC_LINE_RE = re.compile(
    r"^(?P<comprobante>\d+)\s+01B[RI](?P<digitos>\d{9,10}).*?"
    r"\d{2}/\d{2}/\d{4}\s+(?P<cantidad>\d+\.\d{2})\s+"
    r"(?P<precio_bruto>\d+\.\d{2})\s+(?P<precio_neto>\d+\.\d{2})\s+"
    r"(?P<neto_pedido>\d+\.\d{2})\s*$"
)


def decode_codigo_byc(digitos: str) -> tuple[str, str]:
    """Devuelve (codigo_base, variante) a partir del bloque de dígitos
    pegados del campo Artículo. codigo_base es el código real (últimos 3 o
    4 dígitos, sin cero a la izquierda)."""
    return digitos[6:], digitos[4:6]


def parse_lineas_byc(pdf_path: Path) -> list[PedidoProveedorLine]:
    texto = extraer_texto(pdf_path)
    lineas = []
    for raw_line in texto.splitlines():
        m = BYC_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        codigo_base, variante = decode_codigo_byc(m.group("digitos"))
        lineas.append(
            PedidoProveedorLine(
                comprobante=m.group("comprobante"),
                codigo_base=codigo_base,
                variante=variante,
                cantidad=float(m.group("cantidad")),
                precio_bruto=float(m.group("precio_bruto")),
                precio_neto=float(m.group("precio_neto")),
                neto_pedido=float(m.group("neto_pedido")),
            )
        )
    return lineas


# --- DOLZ -----------------------------------------------------------

DOLZ_LINE_RE = re.compile(
    r"^(?P<codigo>\S+)\s+(?P<descripcion>.+?)\s+"
    r"(?P<cantidad>\d+,\d{2})\s+(?P<signo>[A-Z])\s+(?P<um>[A-Z]+)\s+"
    r"(?P<precio>[\d.]+,\d{2})\s+(?P<lista>\d+)\s+(?P<total>[\d.]+,\d{2})\s*$"
)
DOLZ_SUBTOTAL_RE = re.compile(r"^Subtotal\s+([\d.]+,\d{2})\s*$")
DOLZ_IVA_RE = re.compile(r"^I\.V\.A\.\s+[\d,]+\s*%\s+([\d.]+,\d{2})\s*$")
DOLZ_TOTAL_RE = re.compile(r"^TOTAL\s+([\d.]+,\d{2})\s*$")
DOLZ_PERCEPCION_RE = re.compile(
    r"^Percep\.\s*I{1,2}\.BB\.\s+(?P<jurisdiccion>.+?)\s+[\d,]+\s*%\s+(?P<importe>[\d.]+,\d{2})\s*$"
)


def parse_lineas_dolz(pdf_path: Path) -> list[InvoiceLine]:
    texto = extraer_texto(pdf_path)
    lineas = []
    for raw_line in texto.splitlines():
        m = DOLZ_LINE_RE.match(raw_line.strip())
        if not m:
            continue
        lineas.append(
            InvoiceLine(
                codigo_proveedor=m.group("codigo").strip(),
                descripcion=m.group("descripcion").strip(),
                cantidad=_num_eu(m.group("cantidad")),
                precio_unitario=_num_eu(m.group("precio")),
                subtotal=_num_eu(m.group("total")),
            )
        )
    return lineas


def parse_totales_dolz(pdf_path: Path) -> InvoiceTotals:
    texto = extraer_texto(pdf_path)
    m_subtotal = None
    m_total = None
    iva_total = 0.0
    for raw_line in texto.splitlines():
        linea = raw_line.strip()
        if m_subtotal is None:
            m_subtotal = DOLZ_SUBTOTAL_RE.match(linea)
        if m_total is None:
            m_total = DOLZ_TOTAL_RE.match(linea)
        m_iva = DOLZ_IVA_RE.match(linea)
        if m_iva:
            iva_total += _num_eu(m_iva.group(1))
    if not m_subtotal or not m_total:
        raise ValueError("No se pudo extraer Subtotal/TOTAL del PDF de DOLZ.")
    return InvoiceTotals(
        subtotal_neto=_num_eu(m_subtotal.group(1)),
        iva=round(iva_total, 2),
        total=_num_eu(m_total.group(1)),
    )


def parse_percepciones_dolz(pdf_path: Path) -> list[RegimenEspecial]:
    """Percepciones de Ingresos Brutos por jurisdicción (ej. "Bs. As.",
    "C.A.B.A.") -> Regímenes Especiales."""
    texto = extraer_texto(pdf_path)
    percepciones = []
    for raw_line in texto.splitlines():
        m = DOLZ_PERCEPCION_RE.match(raw_line.strip())
        if not m:
            continue
        percepciones.append(
            RegimenEspecial(
                jurisdiccion=m.group("jurisdiccion").strip(),
                importe=_num_eu(m.group("importe")),
            )
        )
    return percepciones


# Autopartes Bronco manda un comprobante "así nomás" (sin CUIT, sin
# Subtotal/IVA/Total) en una tabla con bordes reales. Se abandonó leerlo
# como texto plano con regex: confirmado con dos comprobantes de prueba
# reales que, según el programa de origen (Excel vs Word) y el ancho de
# columna, pdfplumber puede devolver el texto con los caracteres de
# columnas distintas ENTRELAZADOS letra por letra (irrecuperable, no es
# un problema de regex) o, con columnas angostas, cortando el contenido
# de una celda en varios renglones -- incluso en medio de un número
# ("10.665,0" en un renglón, "0" en el siguiente). Extrayendo por CELDA
# (pdfplumber extract_table(), que respeta los bordes reales de la
# tabla) se evita el primer problema, y el segundo se resuelve uniendo
# los saltos de línea de cada celda.
BRONCO_ENCABEZADO_PREFIJOS = ("artícul", "articul", "codigo", "código")


def _unir_celda_bronco(valor: str | None) -> str:
    """Saca los saltos de línea dentro de una celda sin partir un número:
    si el salto está entre dos dígitos (ej. "10.665,0\\n0") se saca sin
    dejar espacio; en cualquier otro caso se reemplaza por un espacio."""
    if not valor:
        return ""
    texto = re.sub(r"(?<=\d)\n(?=\d)", "", valor)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _extraer_filas_tabla(pdf_path: Path) -> list[list[str]]:
    import pdfplumber  # import diferido: sólo hace falta para leer PDFs reales

    filas: list[list[str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tabla = page.extract_table()
            if tabla:
                filas.extend(tabla)
    return filas


def parse_lineas_bronco(pdf_path: Path) -> list[InvoiceLine]:
    lineas = []
    for fila in _extraer_filas_tabla(pdf_path):
        if len(fila) < 5:
            continue
        codigo = _unir_celda_bronco(fila[0])
        if not codigo or codigo.lower().startswith(BRONCO_ENCABEZADO_PREFIJOS):
            continue  # fila vacía o encabezado de la tabla
        cantidad_txt = _unir_celda_bronco(fila[2])
        if not cantidad_txt or not cantidad_txt[0].isdigit():
            continue
        descripcion = _unir_celda_bronco(fila[1])
        precio_txt = _unir_celda_bronco(fila[3]).lstrip("$ ").strip()
        total_txt = _unir_celda_bronco(fila[4]).lstrip("$ ").strip()
        lineas.append(
            InvoiceLine(
                codigo_proveedor=codigo,
                descripcion=descripcion,
                cantidad=_num_eu(cantidad_txt),
                precio_unitario=_num_eu(precio_txt),
                subtotal=_num_eu(total_txt),
            )
        )
    return lineas


def parse_totales_bronco(pdf_path: Path) -> InvoiceTotals:
    """Este comprobante no trae renglones de Subtotal/IVA/Total -- se
    calcula el subtotal sumando el "Precio total" de cada línea. No hay
    forma de saber el IVA desde este papel, así que queda en 0 (si
    corresponde, hay que ajustarlo a mano después de ver un caso real)."""
    lineas = parse_lineas_bronco(pdf_path)
    subtotal = round(sum(linea.subtotal for linea in lineas), 2)
    return InvoiceTotals(subtotal_neto=subtotal, iva=0.0, total=subtotal)


# --- MD (Montpres S.A.) ------------------------------------------------

# Confirmado con error real: el PDF de MD tiene un problema de extracción
# de texto distinto a los demás proveedores -- no corta celdas ni entrelaza
# columnas, pero inserta un espacio de más justo después de cada "." o ","
# que separa miles/decimales (ej. "11.505,49" se extrae como "11. 505, 49"),
# y también separa a veces la primera letra de una palabra del resto (ej.
# "INT." se extrae como "I NT."). _MD_NUM tolera el espacio de más dentro de
# los números.
_MD_NUM = r"\d+(?:\.\s?\d+)*(?:,\s?\d+)?"

MD_LINE_RE = re.compile(
    # El código puede traer un sufijo de una sola letra pegado con espacio
    # (ej. "17001" y "17001 I" son dos artículos distintos, con precio
    # distinto, confirmado real) -- pero ese mismo espacio de más también
    # puede separar la "I" inicial de "INT." (interruptor) de la
    # descripción, así que se excluye específicamente ese caso con el
    # lookahead negativo, confirmado contra 32 casos reales de "I NT." que
    # NO son un código con sufijo.
    rf"^(?P<cantidad>{_MD_NUM})\s+(?P<codigo>{_MD_NUM}(?:/\s?\d+)?(?:\s+[A-Z]\b(?!\s*NT\.))?)\s+"
    rf"(?P<descripcion>.+?)\s+(?P<precio>{_MD_NUM})\s+(?P<importe>{_MD_NUM})\s*$",
    re.MULTILINE,
)

MD_TOTALES_RE = re.compile(
    rf"(?P<subtotal_bruto>{_MD_NUM})\s+(?P<descuentos>{_MD_NUM})\s+(?P<subtotal_neto>{_MD_NUM})\s+"
    rf"I\s*VA\s+(?P<alicuota>{_MD_NUM})\s*%\s+(?P<iva>{_MD_NUM})\s*\n\s*(?P<total>{_MD_NUM})",
)


def _num_md(raw: str) -> float:
    """Saca los espacios de más (ver _MD_NUM) y decide el separador
    decimal según cuáles aparecen -- confirmado con error real: no todos
    los números del PDF de MD usan el mismo formato. Cantidad (ej.
    "5.00") y los 4 números grandes del bloque de totales (ej.
    "11295671.85") solo traen punto, que ahí ES el separador decimal (no
    hay agrupación de miles) -- tratarlos como formato europeo (como hace
    _num_eu, que siempre saca el punto) los multiplicaba por 100. Precio,
    importe e IVA sí usan el formato europeo normal (punto de miles, coma
    decimal) cuando aparecen ambos separadores, o solo coma decimal en
    números chicos (ej. "762,90")."""
    s = re.sub(r"\s+", "", raw)
    if "," in s and "." in s:
        return _num_eu(s)
    if "," in s:
        return float(s.replace(",", "."))
    return float(s)


def _limpiar_codigo_md(raw: str) -> str:
    """Saca los espacios de más DENTRO del código (ej. "40265. 82 I" ->
    "40265.82 I"), pero conserva el espacio antes de un sufijo de letra
    real, que sí es parte del código."""
    return re.sub(r"(?<=[\d/.,])\s+(?=[\d/.,])", "", raw).strip()


def parse_lineas_md(pdf_path: Path) -> list[InvoiceLine]:
    texto = extraer_texto(pdf_path)
    # Recorta antes del bloque de totales del final -- confirmado con
    # error real que, si no, esa fila (mismo patrón de 5 números en
    # columna que una línea de artículo) se cuela como si fuera un
    # artículo más.
    m_totales = MD_TOTALES_RE.search(texto)
    if m_totales:
        texto = texto[: m_totales.start()]
    lineas = []
    for m in MD_LINE_RE.finditer(texto):
        lineas.append(
            InvoiceLine(
                codigo_proveedor=_limpiar_codigo_md(m.group("codigo")),
                descripcion=re.sub(r"\s+", " ", m.group("descripcion")).strip(),
                cantidad=_num_md(m.group("cantidad")),
                precio_unitario=_num_md(m.group("precio")),
                subtotal=_num_md(m.group("importe")),
            )
        )
    return lineas


def parse_totales_md(pdf_path: Path) -> InvoiceTotals:
    texto = extraer_texto(pdf_path)
    m = MD_TOTALES_RE.search(texto)
    if not m:
        raise ValueError("No se pudo extraer el bloque de totales del PDF de MD.")
    return InvoiceTotals(
        subtotal_neto=_num_md(m.group("subtotal_neto")),
        iva=_num_md(m.group("iva")),
        total=_num_md(m.group("total")),
    )


MD_FECHA_RE = re.compile(r"Nº\s*[\d\-\s]+\n(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})")
MD_COMPROBANTE_RE = re.compile(r"Nº\s*([0-9 ]+?)-\s*([0-9 ]+)")


def extraer_comprobante_md(pdf_path: Path) -> tuple[str, str] | None:
    """Punto de venta y número del comprobante (ej. "Nº 00003-00013991"
    -> ("00003", "00013991")), para no tener que tipearlos a mano."""
    texto = extraer_texto(pdf_path)
    m = MD_COMPROBANTE_RE.search(texto)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)), re.sub(r"\s+", "", m.group(2))


def extraer_fecha_md(pdf_path: Path) -> datetime.date | None:
    """La fecha de MD no trae la etiqueta "Fecha" pegada al lado en el
    texto extraído (está en un cuadro de texto separado del PDF que no
    queda adyacente en el orden de lectura) ni usa barras ("04 11 26" en
    vez de "04/11/26") -- el extractor de fecha genérico no la encuentra.
    Se la ubica por su posición fija, justo después del número de
    comprobante ("Nº 00003-00013991")."""
    texto = extraer_texto(pdf_path)
    m = MD_FECHA_RE.search(texto)
    if not m:
        return None
    dia, mes, anio = (int(x) for x in m.groups())
    if anio < 100:
        anio += 2000
    try:
        return datetime.date(anio, mes, dia)
    except ValueError:
        return None


# No todos los proveedores necesitan un extractor de fecha a medida --
# solo se registran acá los que sí (ver extraer_fecha_md); el resto sigue
# usando el genérico de provider_identificacion.py.
PROVIDER_FECHA_PARSERS = {
    "md": extraer_fecha_md,
}

# Idem para punto de venta/número: por ahora solo MD lo saca solo del PDF
# (ver extraer_comprobante_md) -- el resto de los proveedores sigue
# necesitando --punto-venta/--numero a mano.
PROVIDER_COMPROBANTE_PARSERS = {
    "md": extraer_comprobante_md,
}


PROVIDER_PARSERS = {
    "nosso": (parse_lineas_nosso, parse_totales_nosso),
    "ralux": (parse_lineas_ralux, parse_totales_ralux),
    "dolz": (parse_lineas_dolz, parse_totales_dolz),
    "bronco": (parse_lineas_bronco, parse_totales_bronco),
    "md": (parse_lineas_md, parse_totales_md),
}

# No todos los proveedores tienen percepciones/regímenes especiales en su
# factura (Nosso y Ralux no, hasta ahora) -- sólo se registran acá los que
# sí, para que watcher.py sepa cuándo buscarlos y cargarlos.
PROVIDER_REGIMENES_PARSERS = {
    "ralux": parse_percepciones_ralux,
    "dolz": parse_percepciones_dolz,
}
