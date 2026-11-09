"""Validación final antes de que un humano guarde la compra en Centum.

Compara el "Subtotal sin Impuestos con Descuento" que quedaría en el sistema
(según los MatchResult ya resueltos por matcher.py) contra el neto declarado
en el PDF del proveedor. Sólo si esto da OK se le indica al usuario que puede
guardar la compra manualmente.
"""

from __future__ import annotations

from campomar_compras.matcher import subtotal_propuesto
from campomar_compras.models import InvoiceTotals, MatchResult, ValidationResult

TOLERANCIA_PESOS_MINIMA = 1.0  # piso para facturas chicas
TOLERANCIA_PESOS_POR_LINEA = 0.005  # cada línea redondea a centavos (medio centavo de margen por línea)


def validar_subtotal(
    resultados: list[MatchResult],
    totales_factura: InvoiceTotals,
    tolerancia: float | None = None,
) -> ValidationResult:
    """tolerancia: si no se pasa, se calcula sola como el máximo entre un
    piso fijo y el redondeo esperado por línea -- confirmado con una
    factura real de 388 líneas que el redondeo de a centavos por línea
    puede acumular más de $1 de diferencia sin que sea un error real."""
    if tolerancia is None:
        tolerancia = max(TOLERANCIA_PESOS_MINIMA, TOLERANCIA_PESOS_POR_LINEA * len(resultados))

    subtotal_sistema = subtotal_propuesto(resultados)
    diferencia = round(subtotal_sistema - totales_factura.subtotal_neto, 2)

    # Confirmado con error real: si el parser no extrajo ninguna línea de
    # la factura (subtotal_neto=0) y el remito entero queda como
    # excedente, subtotal_sistema también da 0 y "0 coincide con 0"
    # pasaba la validación -- un PASS falso peligrosísimo, nunca hay que
    # guardar algo así. Se corta acá, antes de comparar por tolerancia.
    hay_algun_match_con_factura = any(r.invoice_line is not None for r in resultados)
    if resultados and not hay_algun_match_con_factura:
        return ValidationResult(
            ok=False,
            subtotal_sistema=subtotal_sistema,
            subtotal_factura=totales_factura.subtotal_neto,
            diferencia=diferencia,
            detalle=(
                "NO coincide: ningún artículo de la factura matcheó con nada del "
                "remito (todo quedó como excedente) -- probablemente el parser no "
                "leyó bien la factura. Revisar antes de continuar, NO guardar."
            ),
        )
    if totales_factura.subtotal_neto == 0:
        return ValidationResult(
            ok=False,
            subtotal_sistema=subtotal_sistema,
            subtotal_factura=totales_factura.subtotal_neto,
            diferencia=diferencia,
            detalle=(
                "NO coincide: el subtotal de la factura dio $0 -- probablemente el "
                "parser no extrajo ninguna línea del PDF. Revisar antes de "
                "continuar, NO guardar."
            ),
        )

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
