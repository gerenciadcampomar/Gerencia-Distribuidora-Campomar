"""Validación final antes de que un humano guarde la compra en Centum.

Compara el "Subtotal sin Impuestos con Descuento" -- preferentemente leído
de la pantalla real de Centum después de aplicar todos los cambios, o si no
está disponible, recalculado a partir de los MatchResult ya resueltos por
matcher.py -- contra el neto declarado en el PDF del proveedor (que manda:
es el comprobante real). Sólo si esto da OK se le indica al usuario que
puede guardar la compra manualmente.
"""

from __future__ import annotations

from campomar_compras.matcher import subtotal_propuesto
from campomar_compras.models import InvoiceTotals, MatchResult, ValidationResult

TOLERANCIA_PESOS_SILENCIOSA = 1.0  # hasta acá, ni se menciona la diferencia
TOLERANCIA_PORCENTAJE = 0.005  # 0,5% -- a partir de acá, NO coincide
# Confirmado con el usuario: entre $1 y 0,5% puede pasar perfecto (ej. Centum
# calculando descuentos con un redondeo propio que no es exactamente el %
# nominal -- un 20% nominal que en la práctica sale ~20,08% -- generó una
# diferencia real de ~$163 en una factura de ~$184.000, dentro de lo
# tolerable), pero el mensaje siempre tiene que aclarar cuánto es esa
# diferencia a partir de $1, no solo decir "OK" sin más.


def validar_subtotal(
    resultados: list[MatchResult],
    totales_factura: InvoiceTotals,
    tolerancia: float | None = None,
    subtotal_sistema_real: float | None = None,
) -> ValidationResult:
    """tolerancia: el límite de NO-coincide (más allá de esto, falla). Si
    no se pasa, se calcula sola como el máximo entre un piso fijo ($1,
    para facturas chicas) y el 0,5% del subtotal facturado.

    subtotal_sistema_real: el "Subtotal sin Impuestos con Descuento" leído
    de la pantalla real de Centum (ver
    CentumBrowserClient.leer_subtotal_con_descuento), después de aplicar
    todos los cambios (remitos, conceptos, regímenes, ajustes de
    cantidad). Si se pasa, se usa éste en vez de recalcularlo nosotros en
    Python -- confirmado con el usuario que Centum puede tener su propio
    redondeo al calcular descuentos que no siempre coincide con una
    cuenta simple hecha acá. El comprobante del proveedor manda: se
    compara su total contra lo que Centum REALMENTE calculó, no contra
    una predicción."""
    if tolerancia is None:
        tolerancia = max(TOLERANCIA_PESOS_SILENCIOSA, TOLERANCIA_PORCENTAJE * totales_factura.subtotal_neto)

    subtotal_sistema = subtotal_sistema_real if subtotal_sistema_real is not None else subtotal_propuesto(resultados)
    diferencia = round(subtotal_sistema - totales_factura.subtotal_neto, 2)
    porcentaje_diferencia = (
        abs(diferencia) / totales_factura.subtotal_neto * 100 if totales_factura.subtotal_neto else 0.0
    )

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
        if abs(diferencia) <= TOLERANCIA_PESOS_SILENCIOSA:
            # Hasta $1 de diferencia, ni se menciona -- confirmado con el
            # usuario, no hace falta aclarar nada para algo así de chico.
            detalle = "Coincide: se puede guardar en Centum."
        else:
            detalle = (
                "Coincide dentro de la tolerancia: se puede guardar en Centum. "
                f"(No cierra por ${abs(diferencia):.2f}, {porcentaje_diferencia:.2f}%.)"
            )
        return ValidationResult(
            ok=True,
            subtotal_sistema=subtotal_sistema,
            subtotal_factura=totales_factura.subtotal_neto,
            diferencia=diferencia,
            detalle=detalle,
        )

    return ValidationResult(
        ok=False,
        subtotal_sistema=subtotal_sistema,
        subtotal_factura=totales_factura.subtotal_neto,
        diferencia=diferencia,
        detalle=(
            "NO coincide: revisar antes de guardar. No cierra por "
            f"${abs(diferencia):.2f} ({porcentaje_diferencia:.2f}%, sistema "
            f"{'excede' if diferencia > 0 else 'falta'})."
        ),
    )
