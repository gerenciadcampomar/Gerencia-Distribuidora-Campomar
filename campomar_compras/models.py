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
    codigo_propio_proveedor: str = ""
    """Código tal como lo usa el proveedor (columna "Código propio Proveedor"
    en Centum), idéntico al que trae la factura -- cuando está disponible,
    el matcher lo usa en vez de la tabla de mapeo aprendida (ver matcher.py)."""

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
    PRECIO_DIFIERE = "precio_difiere"
    EXCEDENTE = "excedente"  # código remitido entero, no está en la factura -> tachito
    FALTANTE = "faltante"  # facturado, no remitido en absoluto -> concepto "Mercaderías varias"
    FALTANTE_PARCIAL = "faltante_parcial"
    """Mismo código en ambos, pero el remito trae MENOS cantidad que la
    factura (ej. se facturan 10, el proveedor entrega 8) -- la diferencia
    se carga como concepto "Mercaderías varias", igual que un FALTANTE
    completo pero solo por la cantidad que falta."""
    EXCEDENTE_PARCIAL = "excedente_parcial"
    """Mismo código en ambos, pero el remito trae MÁS cantidad que la
    factura (ej. se facturan 10, el proveedor entrega 12) -- hay que bajar
    la cantidad del artículo a la cantidad facturada (campo "Cantidad
    Ajustada" en Centum), dejando el resto pendiente de suscribir para
    otra factura del proveedor."""


@dataclass
class MatchResult:
    status: MatchStatus
    remito_lines: list[RemitoArticleLine] = field(default_factory=list)
    """Una o más líneas de Centum que corresponden a este resultado -- más
    de una cuando el remito trae el mismo código repetido en renglones
    separados (ver matcher.py: se agrupan por código antes de comparar)."""
    invoice_line: InvoiceLine | None = None
    cantidad_diferencia: float | None = None
    """Solo para FALTANTE_PARCIAL/EXCEDENTE_PARCIAL: cuánto falta o sobra,
    siempre como número positivo."""
    notes: str = ""


@dataclass
class ConceptoFaltante:
    """Línea a cargar manualmente en 'Conceptos' -> '02 - Mercaderías varias'."""

    codigo_concepto: str = "02"
    observaciones: str = ""  # "<código del artículo> FALTANTE" -- formato confirmado por el usuario
    cantidad: float = 0.0
    precio: float = 0.0

    @classmethod
    def desde_invoice_line(cls, line: InvoiceLine, cantidad: float | None = None) -> "ConceptoFaltante":
        """`cantidad` por defecto es la de la línea entera (faltante
        completo); se puede pasar explícita para un faltante parcial
        (cuando el remito trajo parte de la cantidad facturada)."""
        return cls(
            observaciones=f"{line.codigo_proveedor} FALTANTE",
            cantidad=cantidad if cantidad is not None else line.cantidad,
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


@dataclass
class PedidoProveedorLine:
    """Línea de un reporte de Pedido (no es la factura en sí) donde el
    código de Centum viene codificado dentro del campo Artículo junto con
    una variante (ej. proveedor "ByC": código con cero a la izquierda +
    variante de 2 dígitos + código sin cero a la izquierda).

    La variante no es packaging/cantidad por bulto: es un atributo físico
    del artículo (ej. espesor de pared del buje) que lo hace un producto
    distinto. La clave real del artículo es (codigo_base, variante) juntos,
    no codigo_base solo."""

    comprobante: str
    codigo_base: str
    variante: str
    cantidad: float
    precio_bruto: float
    precio_neto: float
    neto_pedido: float

    @property
    def clave_articulo(self) -> str:
        """Clave compuesta para mapear/aprender contra el código de Centum."""
        return f"{self.codigo_base}-{self.variante}"


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
