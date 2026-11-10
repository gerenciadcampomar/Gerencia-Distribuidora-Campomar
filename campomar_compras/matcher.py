"""Compara las líneas de artículos suscriptas en Centum (desde remitos) contra
las líneas extraídas del PDF/imagen de la factura real del proveedor, usando
el mapeo de códigos aprendido para ese proveedor.

Antes de comparar, agrupa las líneas del remito por código -- un mismo
artículo puede llegar en más de un renglón separado (ej. un envío
parcial del proveedor suscripto en dos veces) y hay que sumarlas, si no
el matching da resultados falsos (ver test_matcher.py, caso real
reportado: código repetido en dos líneas de 5, factura pedía 10 juntas).

Clasifica cada grupo en:
- OK: coincide código, cantidad y precio unitario -> se deja tildado en Centum.
- PRECIO_DIFIERE: mismo código y cantidad, precio distinto -> requiere
  revisión manual (no se decide solo, se marca para que un humano lo mire).
- EXCEDENTE: el código está remitido entero, pero no está en la factura
  para nada -> tachito, se elimina la línea completa.
- FALTANTE: está en la factura pero en ningún remito suscripto -> se
  propone como ConceptoFaltante ("02 - Mercaderías varias").
- FALTANTE_PARCIAL: mismo código en los dos, pero el remito trae MENOS
  cantidad que la factura (el proveedor entregó de menos) -> se propone
  la diferencia como ConceptoFaltante, igual que un faltante completo.
- EXCEDENTE_PARCIAL: mismo código en los dos, pero el remito trae MÁS
  cantidad que la factura (el proveedor entregó de más, o el remito juntó
  entregas de otra factura cercana en fecha) -> se baja la cantidad del
  artículo a la facturada. Si el código venía repetido en más de un
  renglón del remito, primero se busca un subconjunto exacto de esos
  renglones que sí coincida con lo facturado (ver _mejor_subconjunto);
  si se encuentra, el renglón sobrante se trata como un EXCEDENTE más
  (se elimina, queda pendiente para la próxima factura). Solo si no hay
  un subconjunto exacto se manda a revisión manual en el reporte.
"""

from __future__ import annotations

from itertools import combinations

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


def _clave_articulo(remito_line: RemitoArticleLine) -> tuple[str, str]:
    """Identifica a qué código corresponde una línea, para agrupar
    repeticiones del mismo artículo en distintos renglones del remito."""
    if remito_line.codigo_propio_proveedor:
        return ("propio", remito_line.codigo_propio_proveedor)
    return ("centum", remito_line.codigo_articulo)


def _agrupar_por_codigo(remito_lines: list[RemitoArticleLine]) -> list[list[RemitoArticleLine]]:
    grupos: dict[tuple[str, str], list[RemitoArticleLine]] = {}
    orden: list[tuple[str, str]] = []
    for linea in remito_lines:
        clave = _clave_articulo(linea)
        if clave not in grupos:
            grupos[clave] = []
            orden.append(clave)
        grupos[clave].append(linea)
    return [grupos[clave] for clave in orden]


def _mejor_subconjunto(grupo: list[RemitoArticleLine], cantidad_objetivo: float) -> list[RemitoArticleLine] | None:
    """Cuando un código viene repetido en más de un renglón del remito,
    no necesariamente todos esos renglones son de ESTA factura -- puede
    haber remitos juntados en la misma ventana de días que en realidad
    corresponden a otro comprobante del mismo proveedor (confirmado real:
    al traer remitos para dos facturas casi simultáneas, varios códigos
    terminaban con el doble de cantidad remitida). Si existe un
    subconjunto EXACTO de renglones cuya cantidad sumada coincide con lo
    facturado, se asume que ese subconjunto es el de esta factura y se
    dejan los demás renglones intactos (pendientes para la próxima), en
    vez de mandar todo a revisión manual."""
    n = len(grupo)
    if n > 10:  # cota de seguridad -- no se vieron casos reales con tantos renglones
        return None
    for r in range(1, n):
        for combo in combinations(range(n), r):
            cantidad = sum(grupo[i].cantidad for i in combo)
            if abs(cantidad - cantidad_objetivo) <= CANTIDAD_TOLERANCIA:
                return [grupo[i] for i in combo]
    return None


def _coincide_con_factura(representante: RemitoArticleLine, inv_line: InvoiceLine, mapping: CodeMappingStore) -> bool:
    if representante.codigo_propio_proveedor:
        return representante.codigo_propio_proveedor == inv_line.codigo_proveedor
    return mapping.get_centum_code(inv_line.codigo_proveedor) == representante.codigo_articulo


def _fusionar_lineas_factura(lineas: list[InvoiceLine]) -> InvoiceLine:
    """Combina varias líneas de factura que matchean el mismo código del
    remito en una sola (sumando cantidad y subtotal). Normalmente una sola
    línea de factura matchea por código, pero hay casos reales (ver MD,
    códigos 15000-15004/15772) donde el mismo artículo aparece facturado dos
    veces, a precios distintos, y Centum lo suscribe como un único artículo."""
    if len(lineas) == 1:
        return lineas[0]
    cantidad = sum(linea.cantidad for linea in lineas)
    subtotal = round(sum(linea.subtotal for linea in lineas), 2)
    precio_unitario = round(subtotal / cantidad, 2) if cantidad else lineas[0].precio_unitario
    return InvoiceLine(
        codigo_proveedor=lineas[0].codigo_proveedor,
        descripcion=lineas[0].descripcion,
        cantidad=cantidad,
        precio_unitario=precio_unitario,
        subtotal=subtotal,
    )


def match_remitos_contra_factura(
    remito_lines: list[RemitoArticleLine],
    invoice_lines: list[InvoiceLine],
    mapping: CodeMappingStore,
) -> list[MatchResult]:
    resultados: list[MatchResult] = []
    invoice_restantes = list(invoice_lines)

    for grupo in _agrupar_por_codigo(remito_lines):
        representante = grupo[0]
        matches_encontrados = [
            inv_line for inv_line in invoice_restantes if _coincide_con_factura(representante, inv_line, mapping)
        ]

        if not matches_encontrados:
            resultados.append(
                MatchResult(
                    status=MatchStatus.EXCEDENTE,
                    remito_lines=grupo,
                    notes="Remitido, no aparece en la factura: no se carga.",
                )
            )
            continue

        for inv_line in matches_encontrados:
            invoice_restantes.remove(inv_line)
        match_encontrado = _fusionar_lineas_factura(matches_encontrados)

        cantidad_remito = sum(linea.cantidad for linea in grupo)
        diferencia = cantidad_remito - match_encontrado.cantidad

        if len(grupo) > 1 and diferencia > CANTIDAD_TOLERANCIA:
            # El código vino repetido en más de un renglón del remito y
            # sobra cantidad -- antes de mandar todo a revisión manual,
            # se busca si algún subconjunto EXACTO de esos renglones
            # coincide con lo facturado (ej. remitos juntados de dos
            # facturas casi simultáneas: un renglón es de ESTA factura y
            # el otro es, confirmado con el usuario, "problema del
            # siguiente comprobante" -- se elimina igual que un excedente
            # normal, no se manda a mano).
            subconjunto = _mejor_subconjunto(grupo, match_encontrado.cantidad)
            if subconjunto is not None:
                sobrante = [linea for linea in grupo if linea not in subconjunto]
                resultados.append(
                    MatchResult(
                        status=MatchStatus.EXCEDENTE,
                        remito_lines=sobrante,
                        notes=(
                            "Venía agrupado con otro(s) renglón(es) del mismo código que sí "
                            "coinciden con la factura -- este renglón de más no se carga."
                        ),
                    )
                )
                grupo = subconjunto
                cantidad_remito = sum(linea.cantidad for linea in grupo)
                diferencia = cantidad_remito - match_encontrado.cantidad

        subtotal_remito = round(sum(linea.subtotal for linea in grupo), 2)

        if diferencia < -CANTIDAD_TOLERANCIA:
            falta = round(-diferencia, 3)
            resultados.append(
                MatchResult(
                    status=MatchStatus.FALTANTE_PARCIAL,
                    remito_lines=grupo,
                    invoice_line=match_encontrado,
                    cantidad_diferencia=falta,
                    notes=(
                        f"Remito: {cantidad_remito} vs Factura: {match_encontrado.cantidad} "
                        f"-- faltan {falta}, se cargan como concepto."
                    ),
                )
            )
            continue
        if diferencia > CANTIDAD_TOLERANCIA:
            sobra = round(diferencia, 3)
            resultados.append(
                MatchResult(
                    status=MatchStatus.EXCEDENTE_PARCIAL,
                    remito_lines=grupo,
                    invoice_line=match_encontrado,
                    cantidad_diferencia=sobra,
                    notes=(
                        f"Remito: {cantidad_remito} vs Factura: {match_encontrado.cantidad} "
                        f"-- sobran {sobra}, hay que ajustar la cantidad a mano."
                    ),
                )
            )
            continue

        precio_neto_remito = subtotal_remito / cantidad_remito
        # Precio NETO de la factura (subtotal facturado / cantidad), no el
        # precio unitario bruto -- confirmado con error real: cuando hay
        # descuento comercial, el remito ya viene con el descuento
        # aplicado ("neto c/dto"), pero se estaba comparando contra el
        # precio unitario bruto de la factura (sin descontar), dando
        # precio_difiere en casi todas las líneas aunque en realidad
        # coincidían perfecto (ej. 968,49 vs 1.210,61 = exactamente 20%
        # de diferencia en decenas de líneas, sistemático). Mismo criterio
        # que ya usa ConceptoFaltante para los faltantes.
        precio_neto_factura = match_encontrado.subtotal / match_encontrado.cantidad
        if not _precio_coincide(precio_neto_remito, precio_neto_factura):
            resultados.append(
                MatchResult(
                    status=MatchStatus.PRECIO_DIFIERE,
                    remito_lines=grupo,
                    invoice_line=match_encontrado,
                    notes=(
                        f"Remito (neto c/dto): {precio_neto_remito:.2f} vs Factura (neto): "
                        f"{precio_neto_factura:.2f}"
                    ),
                )
            )
            continue

        resultados.append(
            MatchResult(status=MatchStatus.OK, remito_lines=grupo, invoice_line=match_encontrado)
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
    propuestas = []
    for r in resultados:
        if r.status == MatchStatus.FALTANTE and r.invoice_line is not None:
            propuestas.append(ConceptoFaltante.desde_invoice_line(r.invoice_line))
        elif r.status == MatchStatus.FALTANTE_PARCIAL and r.invoice_line is not None:
            propuestas.append(ConceptoFaltante.desde_invoice_line(r.invoice_line, cantidad=r.cantidad_diferencia))
    return propuestas


def subtotal_propuesto(resultados: list[MatchResult]) -> float:
    """Subtotal sin impuestos que quedaría cargado si se aplican estos
    resultados: OK/PRECIO_DIFIERE -> el valor ya remitido; FALTANTE/
    FALTANTE_PARCIAL -> se suma lo remitido (si hay) más el concepto a
    precio facturado; EXCEDENTE -> se excluye (se elimina); EXCEDENTE_PARCIAL
    -> queda al valor facturado, una vez que se ajuste la cantidad a mano."""
    total = 0.0
    for r in resultados:
        if r.status in (MatchStatus.OK, MatchStatus.PRECIO_DIFIERE):
            total += sum(linea.subtotal for linea in r.remito_lines)
        elif r.status == MatchStatus.FALTANTE:
            total += r.invoice_line.subtotal
        elif r.status == MatchStatus.FALTANTE_PARCIAL:
            total += sum(linea.subtotal for linea in r.remito_lines)
            # Precio neto (subtotal facturado / cantidad), no el precio
            # unitario bruto -- mismo motivo que en ConceptoFaltante: si
            # hay descuento comercial, es el precio unitario el que no
            # refleja lo realmente facturado por unidad.
            precio_neto_factura = r.invoice_line.subtotal / r.invoice_line.cantidad
            total += round(r.cantidad_diferencia * precio_neto_factura, 2)
        elif r.status == MatchStatus.EXCEDENTE_PARCIAL:
            # Al ajustar la cantidad a la facturada, Centum recalcula el
            # subtotal con SU propio precio unitario (el del remito), no
            # el de la factura.
            cantidad_remito = sum(linea.cantidad for linea in r.remito_lines)
            subtotal_remito = sum(linea.subtotal for linea in r.remito_lines)
            precio_neto_remito = subtotal_remito / cantidad_remito
            total += round(r.invoice_line.cantidad * precio_neto_remito, 2)
    return round(total, 2)
