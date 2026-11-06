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

CUIT_RE = re.compile(
    r"C\.?U\.?I\.?T\.?\s*(?:Nro\.?|N[°º]?)?\s*[:\.]?\s*([\d][\d\-]{9,12}\d)",
    re.IGNORECASE,
)


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
