"""Identifica qué proveedor es una factura a partir del CUIT que trae el PDF,
para elegir automáticamente el parser y el mapeo de código correctos
(ver provider_parsers.PROVIDER_PARSERS).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from campomar_compras.pdf_extractor import extraer_texto

CUITS_PATH = Path(__file__).parent / "providers" / "cuits.json"
NOMBRES_CENTUM_PATH = Path(__file__).parent / "providers" / "nombres_centum.json"

CUIT_RE = re.compile(
    r"C\.?U\.?I\.?T\.?\s*(?:Nro\.?|N[°º]?)?\s*[:\.]?\s*([\d][\d\-]{9,12}\d)",
    re.IGNORECASE,
)

# "Fecha: 28/8/2026" (Nosso) o "FECHA:06/08/2026" (Ralux) -> acepta con o sin
# ceros a la izquierda en día/mes.
FECHA_RE = re.compile(r"FECHA\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)


def _normalizar_cuit(cuit: str) -> str:
    return re.sub(r"\D", "", cuit)


def extraer_cuit_proveedor(pdf_path: Path) -> str | None:
    """Busca el primer CUIT del documento. Ojo: la mayoría de las facturas
    traen dos CUIT (el del proveedor y el nuestro, Distribuidora Campomar);
    esto asume que el primero que aparece es el del emisor, que es lo que
    se vio en los ejemplos reales hasta ahora. Si un proveedor nuevo rompe
    ese supuesto, hay que ajustar esta heurística."""
    texto = extraer_texto(pdf_path)
    m = CUIT_RE.search(texto)
    return _normalizar_cuit(m.group(1)) if m else None


def _cargar_tabla_cuits() -> dict[str, str]:
    if not CUITS_PATH.exists():
        return {}
    return json.loads(CUITS_PATH.read_text(encoding="utf-8"))


def identificar_proveedor(pdf_path: Path) -> str | None:
    """Devuelve el slug de proveedor (ej. 'nosso', 'ralux') a partir del PDF,
    o None si el CUIT no está en la tabla todavía."""
    cuit = extraer_cuit_proveedor(pdf_path)
    if cuit is None:
        return None
    return _cargar_tabla_cuits().get(cuit)


def extraer_fecha_factura(pdf_path: Path) -> datetime.date | None:
    """Fecha del comprobante, para elegir los remitos pendientes más
    cercanos a esa fecha. Devuelve None si no se encuentra (hay que
    revisarlo a mano en ese caso)."""
    texto = extraer_texto(pdf_path)
    m = FECHA_RE.search(texto)
    if not m:
        return None
    dia, mes, anio = (int(x) for x in m.groups())
    try:
        return datetime.date(anio, mes, dia)
    except ValueError:
        return None


def nombre_centum(slug_proveedor: str) -> str | None:
    """Nombre tal como hay que buscarlo en el campo Proveedor de Centum.
    Hay que cargarlo a mano por proveedor la primera vez (no se puede
    inferir del PDF)."""
    if not NOMBRES_CENTUM_PATH.exists():
        return None
    tabla = json.loads(NOMBRES_CENTUM_PATH.read_text(encoding="utf-8"))
    return tabla.get(slug_proveedor)
