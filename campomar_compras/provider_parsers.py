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

# "Artículo" trae 3 campos pegados: código con cero a la izquierda (4
# dígitos) + variante de packaging (2 dígitos) + el mismo código sin cero a
# la izquierda (3 o 4 dígitos) — confirmado con ejemplos reales:
#   1349111349 -> 1349 | 11 | 1349   (variante 11 = "1X")
#   094712947  -> 0947 | 12 | 947    (variante 12 = "2X")
#   094710947  -> 0947 | 10 | 947    (variante 10 = "STD")
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


PROVIDER_PARSERS = {
    "nosso": (parse_lineas_nosso, parse_totales_nosso),
    "ralux": (parse_lineas_ralux, parse_totales_ralux),
}
