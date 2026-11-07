"""Compara las líneas de artículos suscriptas en Centum (desde remitos) contra
las líneas extraídas del PDF/imagen de la factura real del proveedor, usando
el mapeo de códigos aprendido para ese proveedor.

Clasifica cada línea en:
- OK: coincide código, cantidad y precio unitario -> se deja tildada en Centum.
- CANTIDAD_DIFIERE / PRECIO_DIFIERE: mismo artículo, valor distinto -> requiere
  revisión manual (no se decide solo, se marca para que un humano lo mire).
- EXCEDENTE: está en el remito pero no en la factura -> NO se tilda/carga.
- FALTANTE: está en la factura pero no en ningún remito suscripto -> se
  propone como ConceptoFaltante ("02 - Mercaderías varias").
"""

from __future__ import annotations

from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.models import (
    ConceptoFaltante,
    InvoiceLine,
    MatchResult,
    MatchStatus,
    RemitoArticleLine,
)

CANTIDAD_TOLERANCIA = 0.001
PRECIO_TOLERANCIA_PCT = 0.5  # % de diferencia tolerada (redondeos)


def _precio_coincide(a: float, b: float) -> bool:
    if a == 0 and b == 0:
        return True
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base * 100 <= PRECIO_TOLERANCIA_PCT


def match_remitos_contra_factura(
    remito_lines: list[RemitoArticleLine],
    invoice_lines: list[InvoiceLine],
    mapping: CodeMappingStore,
) -> list[MatchResult]:
    resultados: list[MatchResult] = []
    invoice_restantes = list(invoice_lines)

    for remito_line in remito_lines:
        match_encontrado: InvoiceLine | None = None
        for inv_line in invoice_restantes:
            if remito_line.codigo_propio_proveedor:
                # Centum ya guarda el código tal como lo usa el proveedor
                # (columna "Código propio Proveedor"), idéntico al de la
                # factura -- comparación directa, sin pasar por la tabla de
                # mapeo aprendida.
                coincide = remito_line.codigo_propio_proveedor == inv_line.codigo_proveedor
            else:
                centum_code = mapping.get_centum_code(inv_line.codigo_proveedor)
                coincide = centum_code == remito_line.codigo_articulo
            if coincide:
                match_encontrado = inv_line
                break

        if match_encontrado is None:
            resultados.append(
                MatchResult(
                    status=MatchStatus.EXCEDENTE,
                    remito_line=remito_line,
                    notes="Remitido, no aparece en la factura: no se carga.",
                )
            )
            continue

        invoice_restantes.remove(match_encontrado)

        if abs(remito_line.cantidad - match_encontrado.cantidad) > CANTIDAD_TOLERANCIA:
            resultados.append(
                MatchResult(
                    status=MatchStatus.CANTIDAD_DIFIERE,
                    remito_line=remito_line,
                    invoice_line=match_encontrado,
                    notes=(
                        f"Remito: {remito_line.cantidad} vs Factura: "
                        f"{match_encontrado.cantidad}"
                    ),
                )
            )
            continue

        precio_neto_remito = remito_line.subtotal / remito_line.cantidad
        if not _precio_coincide(precio_neto_remito, match_encontrado.precio_unitario):
            resultados.append(
                MatchResult(
                    status=MatchStatus.PRECIO_DIFIERE,
                    remito_line=remito_line,
                    invoice_line=match_encontrado,
                    notes=(
                        f"Remito (neto c/dto): {precio_neto_remito:.2f} vs Factura: "
                        f"{match_encontrado.precio_unitario}"
                    ),
                )
            )
            continue

        resultados.append(
            MatchResult(status=MatchStatus.OK, remito_line=remito_line, invoice_line=match_encontrado)
        )

    for inv_line in invoice_restantes:
        resultados.append(
            MatchResult(
                status=MatchStatus.FALTANTE,
                invoice_line=inv_line,
                notes="Facturado, no remitido: proponer como Concepto 'Mercaderías varias'.",
            )
        )

    return resultados


def proponer_conceptos_faltantes(resultados: list[MatchResult]) -> list[ConceptoFaltante]:
    return [
        ConceptoFaltante.desde_invoice_line(r.invoice_line)
        for r in resultados
        if r.status == MatchStatus.FALTANTE and r.invoice_line is not None
    ]


def subtotal_propuesto(resultados: list[MatchResult]) -> float:
    """Subtotal sin impuestos que quedaría cargado si se aplican estos resultados
    (OK + diferencias que igual se cargan al valor de la línea remitida;
    excedentes se excluyen; faltantes se suman como concepto al precio facturado)."""
    total = 0.0
    for r in resultados:
        if r.status in (MatchStatus.OK, MatchStatus.CANTIDAD_DIFIERE, MatchStatus.PRECIO_DIFIERE):
            total += r.remito_line.subtotal
        elif r.status == MatchStatus.FALTANTE:
            total += r.invoice_line.subtotal
    return round(total, 2)
