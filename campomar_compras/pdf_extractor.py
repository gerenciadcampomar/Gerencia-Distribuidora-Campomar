"""Extrae líneas de artículos desde la factura del proveedor en PDF.

El formato varía por proveedor (columna de código separada, código pegado al
inicio de la descripción, PDF con texto nativo o PDF escaneado/imagen). Este
módulo intenta primero texto nativo (pdfplumber) y si la página no trae texto
extraíble, cae a OCR (pytesseract sobre la página rasterizada).

El parseo fila-a-fila de cada layout de proveedor es lo que hay que ir
ajustando con ejemplos reales: por eso `codigo_regex` es configurable por
proveedor en vez de estar hardcodeado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from campomar_compras.models import InvoiceLine

# Línea típica: "RNMI 37A859  REGULADOR DE VOLTAJE FIAT ...   1,00   27.762,20"
DEFAULT_LINE_RE = re.compile(
    r"^(?P<codigo>[A-Z]{2,5}\s?\d{2,6}[A-Z0-9]*)\s+(?P<descripcion>.+?)\s+"
    r"(?P<cantidad>\d+(?:[.,]\d+)?)\s+(?P<precio>\d+(?:[.,]\d{3})*(?:[.,]\d{2})?)\s*$"
)


@dataclass
class ProviderPdfProfile:
    """Config de parseo específica de un proveedor. Se ajusta con prueba y error."""

    codigo_al_inicio_de_descripcion: bool = True
    line_regex: re.Pattern = DEFAULT_LINE_RE


def _parse_numero(raw: str) -> float:
    raw = raw.strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return float(raw)


def _texto_nativo(pdf_path: Path) -> str:
    texto = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            texto.append(page_text)
    return "\n".join(texto)


def _texto_por_ocr(pdf_path: Path) -> str:
    # Import diferido: pytesseract/pdf2image sólo hacen falta si el PDF es
    # escaneado o es directamente una imagen.
    import pytesseract
    from pdf2image import convert_from_path

    paginas = convert_from_path(str(pdf_path))
    return "\n".join(pytesseract.image_to_string(p, lang="spa") for p in paginas)


def extraer_texto(pdf_o_imagen: Path) -> str:
    if pdf_o_imagen.suffix.lower() in (".png", ".jpg", ".jpeg"):
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(pdf_o_imagen), lang="spa")

    texto = _texto_nativo(pdf_o_imagen)
    if len(texto.strip()) < 20:  # sin texto extraíble -> es escaneado
        texto = _texto_por_ocr(pdf_o_imagen)
    return texto


def extraer_lineas_factura(
    pdf_o_imagen: Path, profile: ProviderPdfProfile | None = None
) -> list[InvoiceLine]:
    profile = profile or ProviderPdfProfile()
    texto = extraer_texto(pdf_o_imagen)

    lineas: list[InvoiceLine] = []
    for raw_line in texto.splitlines():
        m = profile.line_regex.match(raw_line.strip())
        if not m:
            continue
        lineas.append(
            InvoiceLine(
                codigo_proveedor=m.group("codigo").strip(),
                descripcion=m.group("descripcion").strip(),
                cantidad=_parse_numero(m.group("cantidad")),
                precio_unitario=_parse_numero(m.group("precio")),
            )
        )
    return lineas
