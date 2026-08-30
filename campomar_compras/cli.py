"""Punto de entrada del flujo completo, modo manual.

Modo manual (hoy): vos ya suscribiste los remitos en Centum a mano (como en
las capturas) y exportás/copiás esas líneas a un JSON simple. Este comando
las compara contra el PDF real de la factura y te dice qué tildar, qué
Concepto cargar, y si podés guardar o no.

Modo automático (cuando tengamos la API): el mismo `matcher`/`validator` se
reutiliza, sólo cambia de dónde salen las líneas de remito (en vez de este
JSON manual, las trae `CentumClient.buscar_remitos_pendientes` +
`suscribir_remitos`).

Uso:
    python -m campomar_compras.cli \\
        --proveedor nossovitch \\
        --remitos ejemplo_remitos.json \\
        --factura factura_nossovitch.pdf

Formato de --remitos (lista de líneas ya suscriptas en Centum):
    [
      {"codigo_articulo": "RERNMI37A859", "descripcion": "Regulador...",
       "numero_remito": "00001-04062026", "cantidad": 1,
       "precio_unitario": 27762.2, "dto1": 30, "dto2": 5}
    ]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import match_remitos_contra_factura, proponer_conceptos_faltantes
from campomar_compras.models import MatchStatus, RemitoArticleLine
from campomar_compras.pdf_extractor import extraer_lineas_factura, extraer_totales_factura
from campomar_compras.validator import validar_subtotal

ESTADO_LABEL = {
    MatchStatus.OK: "OK — tildar",
    MatchStatus.CANTIDAD_DIFIERE: "REVISAR — cantidad difiere",
    MatchStatus.PRECIO_DIFIERE: "REVISAR — precio difiere",
    MatchStatus.EXCEDENTE: "NO TILDAR — remitido y no facturado",
    MatchStatus.FALTANTE: "CARGAR CONCEPTO — facturado y no remitido",
}


def _cargar_remitos(path: Path) -> list[RemitoArticleLine]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [RemitoArticleLine(**linea) for linea in data]


def ejecutar(proveedor_slug: str, remitos_path: Path, factura_path: Path) -> int:
    mapping = CodeMappingStore(proveedor_slug)
    remitos = _cargar_remitos(remitos_path)
    facturas = extraer_lineas_factura(factura_path)
    totales = extraer_totales_factura(factura_path)

    resultados = match_remitos_contra_factura(remitos, facturas, mapping)

    print(f"\n=== Conciliación proveedor '{proveedor_slug}' ===\n")
    for r in resultados:
        codigo = r.remito_line.codigo_articulo if r.remito_line else r.invoice_line.codigo_proveedor
        print(f"  [{ESTADO_LABEL[r.status]}] {codigo}  {r.notes}")

    conceptos = proponer_conceptos_faltantes(resultados)
    if conceptos:
        print("\n--- Conceptos a cargar (02 - Mercaderías varias) ---")
        for c in conceptos:
            print(f"  Observaciones: {c.observaciones} | Cantidad: {c.cantidad} | Precio: {c.precio}")

    validacion = validar_subtotal(resultados, totales)
    print("\n--- Validación final ---")
    print(f"  Subtotal sistema (propuesto): ${validacion.subtotal_sistema:,.2f}")
    print(f"  Subtotal factura (PDF):       ${validacion.subtotal_factura:,.2f}")
    print(f"  {validacion.detalle}")

    if validacion.ok:
        print("\n>>> PASS: podés guardar la compra en Centum. <<<\n")
        return 0

    print("\n>>> FAIL: NO guardes todavía, revisá las diferencias arriba. <<<\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proveedor", required=True, help="Slug del proveedor (ej. nossovitch)")
    parser.add_argument("--remitos", required=True, type=Path, help="JSON con las líneas ya suscriptas en Centum")
    parser.add_argument("--factura", required=True, type=Path, help="PDF/imagen de la factura real del proveedor")
    args = parser.parse_args(argv)
    return ejecutar(args.proveedor, args.remitos, args.factura)


if __name__ == "__main__":
    sys.exit(main())
