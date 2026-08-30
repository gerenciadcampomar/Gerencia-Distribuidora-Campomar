"""Cliente contra la API de Centum Suite.

Placeholder: todavía no tenemos la documentación de endpoints/autenticación
de la API oficial ("Apificables hasta la muerte"). Esta interfaz define las
operaciones que necesita el flujo de Compras, para que el resto del código
(matcher, extractor) se pueda probar con un fake/mock mientras se consigue
acceso real, y después sólo haga falta implementar estos métodos.
"""

from __future__ import annotations

from dataclasses import dataclass

from campomar_compras.models import (
    ConceptoFaltante,
    RegimenEspecial,
    RemitoArticleLine,
)


@dataclass
class CentumCredentials:
    base_url: str
    api_key: str
    sucursal: str


class CentumClient:
    def __init__(self, credentials: CentumCredentials):
        self.credentials = credentials

    def buscar_remitos_pendientes(self, proveedor_id: str) -> list[dict]:
        """GET remitos de compra pendientes de un proveedor (pantalla
        'Remitos de Compra Pendientes'). Pendiente de mapear al endpoint real."""
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")

    def suscribir_remitos(self, compra_id: str, numeros_remito: list[str]) -> list[RemitoArticleLine]:
        """Equivale a tildar remitos y presionar 'Agregar y volver'."""
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")

    def crear_compra(self, proveedor_id: str, tipo_comprobante: str, **datos_comprobante) -> str:
        """Crea la cabecera de la Compra (Factura de Compras). Devuelve compra_id."""
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")

    def agregar_concepto(self, compra_id: str, concepto: ConceptoFaltante) -> None:
        """Carga un renglón en 'Conceptos' (ej. '02 - Mercaderías varias')."""
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")

    def agregar_regimen_especial(self, compra_id: str, regimen: RegimenEspecial) -> None:
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")

    def guardar_compra(self, compra_id: str) -> None:
        raise NotImplementedError("Falta documentación/acceso a la API de Centum")
