"""Cliente de automatización de Centum Suite vía navegador (Playwright).

Hasta no tener acceso a la API, este es el "brazo" que hace en el navegador
los mismos clicks que haríamos a mano: login, Compras > Compras > Crear,
elegir proveedor, suscribir remitos, destildar excedentes, cargar Conceptos
y Regímenes Especiales. NUNCA hace click en Guardar/Cerrar — ese paso queda
siempre para que lo haga una persona, mirando la pantalla real.

Los selectores están armados por texto visible (lo que se ve en pantalla),
no por el HTML interno de Centum, porque lo único que vimos son capturas de
pantalla. Es muy probable que haya que ajustar alguno la primera vez que
esto corra contra el sistema real -- por eso CentumTextos centraliza todo
en un solo lugar fácil de parchar, y cada paso guarda una captura de pantalla
de depuración si falla (ver `directorio_debug`).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from campomar_compras.models import ConceptoFaltante, RegimenEspecial, RemitoArticleLine


class PasoCentumError(RuntimeError):
    """Un paso de la automatización no encontró lo que esperaba en pantalla."""


@dataclass
class CentumCredenciales:
    url_login: str
    usuario: str
    contrasena: str
    sucursal: str


# Textos visibles tal como aparecen en las capturas que compartió el usuario.
# Si Centum cambia un rótulo, se ajusta acá y no en el resto del código.
class CentumTextos:
    MENU_COMPRAS = "Compras"
    SUBMENU_COMPRAS = "Compras"  # el ítem "Compras" dentro del submenú Compras
    BOTON_CREAR = "Crear"
    CAMPO_PROVEEDOR = "Proveedor"
    TIPO_COMPROBANTE = "Tipo Comprobante"
    SECCION_ARTICULOS = "Artículos Suscriptos de Remitos de Compra"
    BOTON_AGREGAR = "Agregar"
    BOTON_AGREGAR_Y_VOLVER = "Agregar y volver"
    SECCION_TOTALES = "Totales"
    LABEL_SUBTOTAL_DESC = "Subtotal sin Impuestos con Descuento"
    SECCION_CONCEPTOS = "Conceptos"
    SECCION_REGIMENES = "Regímenes Especiales"
    CONCEPTO_MERCADERIAS_VARIAS = "Mercaderías varias"


class CentumBrowserClient:
    def __init__(self, page, directorio_debug: Path | None = None):
        self.page = page
        self.directorio_debug = directorio_debug

    def _capturar_debug(self, nombre: str) -> None:
        if not self.directorio_debug:
            return
        try:
            self.directorio_debug.mkdir(parents=True, exist_ok=True)
            marca = datetime.datetime.now().strftime("%H%M%S")
            self.page.screenshot(path=str(self.directorio_debug / f"{marca}_{nombre}.png"))
        except Exception:  # noqa: BLE001 - nunca debe tapar el error original
            pass

    def _paso(self, descripcion: str, accion):
        try:
            return accion()
        except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier fallo de Playwright
            self._capturar_debug(f"error_{descripcion}")
            raise PasoCentumError(
                f"Fallo en el paso '{descripcion}'. Revisá la captura de depuración "
                f"y confirmá si el texto/botón que busco sigue llamándose igual "
                f"en pantalla. Detalle: {exc}"
            ) from exc

    # --- Login y navegación -------------------------------------------

    def login(self, credenciales: CentumCredenciales) -> None:
        def accion():
            self.page.goto(credenciales.url_login)
            # OJO: esta página es ASP.NET clásico y tiene inputs ocultos
            # (__VIEWSTATE, etc.) antes de los campos visibles, así que no
            # se puede usar "el primer input de la página" -- hay que
            # buscarlos por su placeholder tal como se ve en pantalla.
            self.page.get_by_placeholder("Usuario").fill(credenciales.usuario)
            self.page.get_by_placeholder("Contraseña").fill(credenciales.contrasena)
            # El combo "Sucursal" (de dónde se toma stock) no es obligatorio:
            # Centum entra igual aunque no se toque. Si se definió una, se
            # intenta elegir, pero si no se encuentra no frena el login.
            if credenciales.sucursal:
                try:
                    self.page.get_by_text("Seleccione la Sucursal").click()
                    self.page.get_by_text(credenciales.sucursal, exact=False).first.click()
                except Exception:  # noqa: BLE001
                    pass
            self.page.get_by_role("button", name="Ingresar").click()
            self.page.wait_for_load_state("networkidle")

        self._paso("login", accion)

    def _abrir_menu_lateral(self) -> None:
        """Después del login queda la pantalla de 'Bienvenida' con el menú
        lateral cerrado -- hay que abrirlo con el ícono de las tres rayas
        (arriba a la izquierda) antes de poder clickear cualquier módulo.
        Es un <div class="menu-wrapper" onclick="OpenMenu();">, confirmado
        inspeccionando la página real (no tiene rol accesible, por eso no
        se podía encontrar por texto/rol)."""
        candidatos = [
            lambda: self.page.locator(".menu-wrapper").first,
            lambda: self.page.get_by_role("button", name="menu").first,
            lambda: self.page.locator("[aria-label*='menu' i]").first,
            lambda: self.page.locator("header button, nav button").first,
        ]
        for candidato in candidatos:
            try:
                candidato().click(timeout=3000)
                return
            except Exception:  # noqa: BLE001
                continue
        raise PasoCentumError(
            "No encontré el botón de las tres rayas para abrir el menú lateral. "
            "Revisar la captura de depuración."
        )

    def ir_a_compras_crear(self) -> None:
        def accion():
            self._abrir_menu_lateral()
            # El menú "Compras" y el ítem "Compras" dentro del submenú tienen
            # el mismo texto exacto. El primero que aparece en pantalla es el
            # módulo (lo abrimos), el que aparece después del submenú
            # desplegado es el ítem real que hay que clickear.
            self.page.get_by_text(CentumTextos.MENU_COMPRAS, exact=True).first.click()
            self.page.get_by_text(CentumTextos.SUBMENU_COMPRAS, exact=True).last.click()
            self.page.get_by_role("button", name=CentumTextos.BOTON_CREAR).click()
            self.page.wait_for_load_state("networkidle")

        self._paso("ir_a_compras_crear", accion)

    # --- Cabecera de la compra ------------------------------------------

    def elegir_proveedor(self, razon_social_exacta: str) -> None:
        def accion():
            # El combo Select2 de arriba del campo Proveedor NO busca contra
            # el backend (sólo filtra lo que ya está precargado en pantalla,
            # por eso "NOSSOVITCH" no encontraba nada ahí). La búsqueda real
            # es una ventana aparte que se abre con la lupa -- confirmado
            # inspeccionando: <button id="BtnlstProveedor_Back" ...
            # onclick="SetearOrigenBusquedaProveedor(false);BusquedaProveedorMostrar();">.
            # El campo Razón Social de esa ventana (#txtRazonSocialProveedor)
            # ya se había visto en un error anterior de Playwright.
            #
            # OJO: puede haber varios proveedores con el MISMO CUIT (ej.
            # "NOSSO DESCONTINUADOS", "NOSSO NACIONAL" y "NOSSOVITCH Y CIA
            # S.A." comparten CUIT) -- por eso se busca y matchea por razón
            # social EXACTA, no por CUIT.
            self.page.locator("#BtnlstProveedor_Back").click()
            self.page.locator("#txtRazonSocialProveedor").fill(razon_social_exacta)
            self.page.get_by_role("button", name="Buscar").click()
            self.page.wait_for_load_state("networkidle")
            fila = self.page.locator("tr", has_text=razon_social_exacta)
            fila.locator("input[type=checkbox]").first.check()

        self._paso("elegir_proveedor", accion)

    def completar_cabecera(
        self,
        tipo_comprobante: str,
        fecha_documento: str,
        punto_venta: str,
        numero: str,
    ) -> None:
        def accion():
            self.page.get_by_label(CentumTextos.TIPO_COMPROBANTE).select_option(
                label=tipo_comprobante
            )
            self.page.get_by_label("Fecha Documento").fill(fecha_documento)
            self.page.get_by_label("Punto de Venta").fill(punto_venta)
            self.page.get_by_label("Número").fill(numero)

        self._paso("completar_cabecera", accion)

    # --- Remitos / artículos ------------------------------------------

    def abrir_remitos_pendientes(self) -> None:
        def accion():
            seccion = self.page.get_by_text(CentumTextos.SECCION_ARTICULOS)
            seccion.scroll_into_view_if_needed()
            self.page.get_by_role("button", name=CentumTextos.BOTON_AGREGAR).first.click()
            self.page.wait_for_load_state("networkidle")

        self._paso("abrir_remitos_pendientes", accion)

    def listar_remitos_pendientes(self) -> list[dict]:
        """Lee la tabla 'Remitos de Compra Pendientes' (fecha, número, sucursal)."""

        def accion():
            filas = self.page.locator("table tr").all()
            remitos = []
            for fila in filas:
                celdas = [c.strip() for c in fila.all_inner_texts()]
                if len(celdas) >= 3 and "/" in celdas[0]:
                    remitos.append({"fecha": celdas[0], "numero": celdas[1], "sucursal": celdas[2]})
            return remitos

        return self._paso("listar_remitos_pendientes", accion)

    def seleccionar_remitos(self, numeros_remito: list[str]) -> None:
        def accion():
            for numero in numeros_remito:
                fila = self.page.locator("tr", has_text=numero)
                fila.locator("input[type=checkbox]").first.check()
            self.page.get_by_role("button", name=CentumTextos.BOTON_AGREGAR_Y_VOLVER).click()
            self.page.wait_for_load_state("networkidle")

        self._paso("seleccionar_remitos", accion)

    def leer_articulos_cargados(self) -> list[RemitoArticleLine]:
        """Lee la tabla de 'Artículos Suscriptos de Remitos de Compra' ya cargada,
        tal como quedó después de suscribir los remitos elegidos."""

        def accion():
            filas = self.page.locator("table tr").all()
            lineas = []
            for fila in filas:
                celdas = [c.strip() for c in fila.all_inner_texts()]
                if len(celdas) < 6:
                    continue
                try:
                    lineas.append(
                        RemitoArticleLine(
                            codigo_articulo=celdas[3],
                            descripcion=celdas[9] if len(celdas) > 9 else "",
                            numero_remito=celdas[1],
                            cantidad=float(celdas[4].replace(",", ".")),
                            precio_unitario=float(celdas[11].replace(",", ".")),
                        )
                    )
                except (ValueError, IndexError):
                    continue
            return lineas

        return self._paso("leer_articulos_cargados", accion)

    def destildar_articulo(self, numero_remito: str, codigo_articulo: str) -> None:
        def accion():
            fila = self.page.locator("tr", has_text=codigo_articulo).filter(has_text=numero_remito)
            fila.locator("input[type=checkbox]").first.uncheck()

        self._paso(f"destildar_{codigo_articulo}", accion)

    # --- Conceptos y regímenes especiales -------------------------------

    def agregar_concepto(self, concepto: ConceptoFaltante) -> None:
        def accion():
            self.page.get_by_text(CentumTextos.SECCION_CONCEPTOS).scroll_into_view_if_needed()
            self.page.get_by_role("button", name=CentumTextos.BOTON_AGREGAR).last.click()
            self.page.get_by_text(CentumTextos.CONCEPTO_MERCADERIAS_VARIAS).click()
            self.page.get_by_label("Observaciones").fill(concepto.observaciones)
            self.page.get_by_label("Cantidad").fill(str(concepto.cantidad))
            self.page.get_by_label("Precio").fill(str(concepto.precio))

        self._paso("agregar_concepto", accion)

    def agregar_regimen_especial(self, regimen: RegimenEspecial) -> None:
        def accion():
            self.page.get_by_text(CentumTextos.SECCION_REGIMENES).scroll_into_view_if_needed()
            self.page.get_by_role("button", name=CentumTextos.BOTON_AGREGAR).last.click()
            self.page.get_by_label("Jurisdicción").fill(regimen.jurisdiccion)
            self.page.get_by_label("Importe").fill(str(regimen.importe))

        self._paso("agregar_regimen_especial", accion)

    # --- Lectura final ---------------------------------------------------

    def leer_subtotal_sistema(self) -> float:
        def accion():
            texto = self.page.get_by_text(CentumTextos.LABEL_SUBTOTAL_DESC).locator("..").inner_text()
            digitos = "".join(c for c in texto if c.isdigit() or c in ",.")
            return float(digitos.replace(".", "").replace(",", "."))

        return self._paso("leer_subtotal_sistema", accion)
