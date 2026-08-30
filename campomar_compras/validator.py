"""Validación final antes de que un humano guarde la compra en Centum.

Compara el "Subtotal sin Impuestos con Descuento" que quedaría en el sistema
(según los MatchResult ya resueltos por matcher.py) contra el neto declarado
en el PDF del proveedor. Sólo si esto da OK se le indica al usuario que puede
guardar la compra manualmente.
"""

from __future__ import annotations

from campomar_compras.matcher import subtotal_propuesto
from campomar_compras.models import InvoiceTotals, MatchResult, ValidationResult

TOLERANCIA_PESOS = 1.0  # margen para redondeos de centavos


def validar_subtotal(
    resultados: list[MatchResult],
    totales_factura: InvoiceTotals,
    tolerancia: float = TOLERANCIA_PESOS,
) -> ValidationResult:
    subtotal_sistema = subtotal_propuesto(resultados)
    diferencia = round(subtotal_sistema - totales_factura.subtotal_neto, 2)

    if abs(diferencia) <= tolerancia:
        return ValidationResult(
            ok=True,
            subtotal_sistema=subtotal_sistema,
            subtotal_factura=totales_factura.subtotal_neto,
            diferencia=diferencia,
            detalle="Coincide dentro de la tolerancia: se puede guardar en Centum.",
        )

    return ValidationResult(
        ok=False,
        subtotal_sistema=subtotal_sistema,
        subtotal_factura=totales_factura.subtotal_neto,
        diferencia=diferencia,
        detalle=(
            "NO coincide: revisar antes de guardar. Diferencia de "
            f"${diferencia:.2f} (sistema {'excede' if diferencia > 0 else 'falta'})."
        ),
    )
