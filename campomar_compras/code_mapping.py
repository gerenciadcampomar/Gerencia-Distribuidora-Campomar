"""Mapea el código de artículo del proveedor (como figura en su PDF) contra el
código interno de Centum Suite.

Cada proveedor tiene su propia convención (ej. Nossovitch antepone "RE" a un
código de repuestos, pero antepone "Z" en lugar de "ZEN" a otros). En vez de
adivinar una regla única, este módulo:

1. Busca primero en una tabla de mapeos ya confirmados para ese proveedor
   (persistida en JSON, `providers/<proveedor>.json`).
2. Si no hay mapeo confirmado, sugiere candidatos por similitud de texto
   (normalizando espacios/mayúsculas) entre las líneas del remito y las de
   la factura, para que un humano confirme antes de guardar la compra.
3. Cuando se confirma un match (automático de alta confianza, o manual),
   `learn()` lo persiste para que la próxima factura del mismo proveedor
   ya lo resuelva solo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rapidfuzz import fuzz

PROVIDERS_DIR = Path(__file__).parent / "providers"


def _normalize(code: str) -> str:
    return re.sub(r"[\s\-]", "", code).upper()


class CodeMappingStore:
    def __init__(self, proveedor_slug: str, providers_dir: Path = PROVIDERS_DIR):
        self.proveedor_slug = proveedor_slug
        self.path = providers_dir / f"{proveedor_slug}.json"
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def get_centum_code(self, codigo_proveedor: str) -> str | None:
        return self._data.get(_normalize(codigo_proveedor))

    def learn(self, codigo_proveedor: str, codigo_centum: str) -> None:
        self._data[_normalize(codigo_proveedor)] = codigo_centum
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def suggest(self, codigo_proveedor: str, candidatos_centum: list[str], threshold: int = 60) -> list[tuple[str, int]]:
        """Devuelve candidatos (codigo_centum, score 0-100) ordenados por similitud,
        para cuando todavía no hay un mapeo confirmado."""
        norm = _normalize(codigo_proveedor)
        scored = [
            (c, fuzz.partial_ratio(norm, _normalize(c)))
            for c in candidatos_centum
        ]
        scored = [s for s in scored if s[1] >= threshold]
        return sorted(scored, key=lambda s: s[1], reverse=True)
