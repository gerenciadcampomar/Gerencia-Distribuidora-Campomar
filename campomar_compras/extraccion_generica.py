"""Extractor de código y cantidad genérico, para proveedores que todavía no
tienen un parser calibrado en provider_parsers.py.

A diferencia de los parsers por proveedor (que necesitan ajustar a mano el
formato exacto de cada factura -- separador decimal, columnas, ruido de
OCR, etc.), este módulo solo intenta sacar lo mínimo indispensable para
poder comparar contra Centum: código y cantidad de cada línea. El precio
queda en 0.0 -- esta vía NO sirve para validar precio/descuento, solo
código y cantidad (ver matcher.match_remitos_contra_factura con
comparar_precio=False).

Confirmado con error real (factura de Some): muchos PDF no tienen una
tabla con bordes reales -- pdfplumber.extract_table() no encuentra nada,
da 0 filas. En vez de depender de una tabla, se usa la CANTIDAD como ancla
(un patrón bien distintivo: un número chico seguido de ",00"/".00", ya que
las facturas casi siempre facturan en unidades enteras) y se mira el
token pegado al lado (antes o después, según el proveedor) para
identificar el código -- funciona bien cuando código y cantidad están uno
al lado del otro en el renglón (confirmado con Some). Para el caso en que
el código queda lejos de la cantidad (con toda la descripción en el
medio), esta pasada no lo encuentra -- ahí entra la búsqueda inversa
(ver extraer_lineas_generico): confirma que el código SÍ está en algún
lado del texto, aunque no pueda saber la cantidad exacta de esa línea
puntual (queda marcado aparte para revisión manual, ver watcher.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from campomar_compras.models import InvoiceLine
from campomar_compras.pdf_extractor import extraer_texto

# Cantidad: número chico (hasta 4 cifras) con dos ceros decimales exactos --
# confirmado real que las facturas facturan en unidades enteras ("1.00",
# "170.00"), nunca fracciones, así que esto no se confunde con un precio
# (que casi siempre trae separador de miles y/o decimales distintos de 00).
_CANTIDAD_ANCLA_RE = re.compile(r"(?<![\d.,])(\d{1,4})[.,]00(?![\d.,])")

# Código: letras+dígitos (ej. "P4117", "RNMI37A859") o un número de 4 a 7
# cifras solo (ej. "101162"). Deliberadamente angosto -- mejor no
# reconocer un código raro que confundir una palabra cualquiera con uno.
_CODIGO_TOKEN_RE = re.compile(r"^[A-Z]{1,4}\d{2,7}(?:[A-Z]|/\d+)?$|^\d{4,7}$")


def _es_token_codigo(token: str) -> bool:
    return bool(_CODIGO_TOKEN_RE.match(token.strip(".,;:")))


def _lineas_por_ancla_de_cantidad(pdf_path: Path) -> list[InvoiceLine]:
    texto = extraer_texto(pdf_path)
    lineas: list[InvoiceLine] = []
    vistos: set[str] = set()
    for renglon in texto.splitlines():
        tokens = renglon.split()
        if not tokens:
            continue
        for i, token in enumerate(tokens):
            m = _CANTIDAD_ANCLA_RE.fullmatch(token.strip(".,;:"))
            if not m:
                continue
            cantidad = float(m.group(1))
            candidato_siguiente = tokens[i + 1] if i + 1 < len(tokens) else ""
            candidato_anterior = tokens[i - 1] if i > 0 else ""
            if _es_token_codigo(candidato_siguiente):
                codigo = candidato_siguiente.strip(".,;:")
            elif _es_token_codigo(candidato_anterior):
                codigo = candidato_anterior.strip(".,;:")
            else:
                continue
            clave = f"{codigo}|{i}|{renglon}"
            if clave in vistos:
                continue
            vistos.add(clave)
            lineas.append(
                InvoiceLine(
                    codigo_proveedor=codigo,
                    descripcion="",
                    cantidad=cantidad,
                    precio_unitario=0.0,
                    subtotal=0.0,
                )
            )
            break  # un ancla de cantidad por renglón alcanza para este renglón
    return lineas


def _buscar_codigos_sueltos(pdf_path: Path, codigos: list[str]) -> list[InvoiceLine]:
    """Para códigos del remito que la pasada por ancla de cantidad no
    encontró: busca cada uno como texto suelto en el PDF entero (ej.
    código lejos de la cantidad, con la descripción en el medio). No sabe
    la cantidad real en este caso -- queda en 1.0 como valor provisorio,
    a confirmar a mano (se marca en el reporte aparte, ver watcher.py)."""
    texto = extraer_texto(pdf_path)
    encontrados = []
    for codigo in codigos:
        if not codigo:
            continue
        patron = re.compile(rf"(?<!\w){re.escape(codigo)}(?!\w)")
        if patron.search(texto):
            encontrados.append(
                InvoiceLine(
                    codigo_proveedor=codigo,
                    descripcion="(encontrado por búsqueda de texto, cantidad sin confirmar)",
                    cantidad=1.0,
                    precio_unitario=0.0,
                    subtotal=0.0,
                )
            )
    return encontrados


def extraer_lineas_generico(pdf_path: Path, codigos_remito: list[str] | None = None) -> list[InvoiceLine]:
    """codigos_remito: lista de "Código propio Proveedor" ya leídos del
    remito en Centum -- si se pasa, cualquiera de esos códigos que NO
    haya aparecido por ancla de cantidad se busca también como texto
    suelto en el PDF (ver _buscar_codigos_sueltos); esas líneas quedan
    con cantidad=1.0 sin confirmar, para que watcher.py las marque aparte
    en el reporte."""
    lineas = _lineas_por_ancla_de_cantidad(pdf_path)
    if codigos_remito:
        ya_encontrados = {l.codigo_proveedor for l in lineas}
        faltan = [c for c in codigos_remito if c not in ya_encontrados]
        lineas.extend(_buscar_codigos_sueltos(pdf_path, faltan))
    return lineas
