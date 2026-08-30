"""Data model shared by the extractor, the code mapper and the matcher."""

from dataclasses import dataclass, field
from enum import Enum


@dataclass
class RemitoArticleLine:
    """A line already loaded in Centum after 'suscribir' remitos de compra."""

    codigo_articulo: str
    descripcion: str
    numero_remito: str
    cantidad: float
    precio_unitario: float
    dto1: float = 0.0
    dto2: float = 0.0
    dto3: float = 0.0

    @property
    def subtotal(self) -> float:
        neto = self.precio_unitario
        for dto in (self.dto1, self.dto2, self.dto3):
            neto *= 1 - dto / 100
        return round(neto * self.cantidad, 2)


@dataclass
class InvoiceLine:
    """A line extracted from the provider's PDF/image invoice."""

    codigo_proveedor: str
    descripcion: str
    cantidad: float
    precio_unitario: float
    subtotal: float | None = None

    def __post_init__(self) -> None:
        if self.subtotal is None:
            self.subtotal = round(self.cantidad * self.precio_unitario, 2)


class MatchStatus(str, Enum):
    OK = "ok"
    CANTIDAD_DIFIERE = "cantidad_difiere"
    PRECIO_DIFIERE = "precio_difiere"
    EXCEDENTE = "excedente"  # remitido, no facturado -> no se carga
    FALTANTE = "faltante"  # facturado, no remitido -> concepto "Mercaderías varias"


@dataclass
class MatchResult:
    status: MatchStatus
    remito_line: RemitoArticleLine | None = None
    invoice_line: InvoiceLine | None = None
    notes: str = ""


@dataclass
class ConceptoFaltante:
    """Línea a cargar manualmente en 'Conceptos' -> '02 - Mercaderías varias'."""

    codigo_concepto: str = "02"
    observaciones: str = ""  # código del artículo faltante, texto libre
    cantidad: float = 0.0
    precio: float = 0.0

    @classmethod
    def desde_invoice_line(cls, line: InvoiceLine) -> "ConceptoFaltante":
        return cls(
            observaciones=f"{line.codigo_proveedor} - {line.descripcion}".strip(" -"),
            cantidad=line.cantidad,
            precio=line.precio_unitario,
        )


@dataclass
class RegimenEspecial:
    """Percepción por jurisdicción, siempre carga manual."""

    jurisdiccion: str
    importe: float
    observaciones: str = ""


@dataclass
class InvoiceTotals:
    """Bloque 'Totales' impreso en el PDF del proveedor (o su equivalente en
    pantalla de Centum: 'Subtotal sin Impuestos con Descuento')."""

    subtotal_neto: float  # subtotal sin impuestos, ya con descuento aplicado
    iva: float = 0.0
    total: float = 0.0


@dataclass
class ValidationResult:
    ok: bool
    subtotal_sistema: float
    subtotal_factura: float
    diferencia: float
    detalle: str = ""


class TipoComprobante(str, Enum):
    FACTURA = "factura"
    NOTA_CREDITO = "nota_credito"


@dataclass
class Segmentacion:
    """Reparto de una compra entre 'oficial' y 'alternativa' (u otras partes),
    ya sea proporcional o por cantidad puntual acordada con el proveedor."""

    parte: str  # ej. "oficial", "alternativa"
    cantidad: float


def repartir_segmentacion(cantidad_total: float, partes: dict[str, float]) -> list[Segmentacion]:
    """Reparte `cantidad_total` según los pesos relativos de `partes` (ej.
    {"oficial": 1, "alternativa": 1} para mitad y mitad). El último reparto
    absorbe el redondeo para que la suma dé exacto."""
    if not partes:
        raise ValueError("Debe indicarse al menos una parte para segmentar.")
    total_pesos = sum(partes.values())
    if total_pesos <= 0:
        raise ValueError("La suma de los pesos de las partes debe ser mayor a cero.")

    nombres = list(partes.keys())
    resultado = []
    asignado = 0.0
    for nombre in nombres[:-1]:
        cantidad = round(cantidad_total * partes[nombre] / total_pesos, 3)
        resultado.append(Segmentacion(parte=nombre, cantidad=cantidad))
        asignado += cantidad
    resultado.append(Segmentacion(parte=nombres[-1], cantidad=round(cantidad_total - asignado, 3)))
    return resultado
