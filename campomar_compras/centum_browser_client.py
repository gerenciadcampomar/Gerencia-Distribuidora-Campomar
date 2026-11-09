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

from campomar_compras.models import ConceptoFaltante, RemitoArticleLine
from campomar_compras.provider_parsers import _num_eu


class PasoCentumError(RuntimeError):
    """Un paso de la automatización no encontró lo que esperaba en pantalla."""


def _formato_decimal_ar(valor: float) -> str:
    """Centum espera coma decimal, no punto -- confirmado con error real:
    "43434.0" quedaba marcado como inválido (en rosa) en el campo de
    Regímenes Especiales, porque en Argentina el separador decimal es la
    coma, no el punto. Ej.: 43434.0 -> "43434.00" -> "43434,00"."""
    return f"{valor:.2f}".replace(".", ",")


_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _epoch_ms_utc(anio: int, mes: int, dia: int) -> int:
    """El datepicker de Fecha Documento (bootstrap-datepicker) marca cada
    celda de día con data-date=<epoch en milisegundos, medianoche UTC>
    -- confirmado inspeccionando el HTML real, ej. 5/11/2026 ->
    data-date="1793836800000". Se usa para ubicar la celda exacta a
    clickear."""
    dt = datetime.datetime(anio, mes, dia, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


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

    def _contenedor_remitos_pendientes(self):
        """Devuelve el Page o Frame donde está la grilla "Remitos de Compra
        Pendientes". Confirmado con DevTools que las filas tienen
        checkboxes con id "chkRemitoCompraPendiente_<id>"; buscarlos
        directamente en la página principal no encontró nada (0 <tr>/<td>
        relevantes pese a verse 3 filas en pantalla), así que lo más
        probable es que esa grilla esté dentro de un iframe -- se recorren
        todos los frames hasta encontrar el que los tiene."""
        if self.page.locator("input[id^='chkRemitoCompraPendiente_']").count() > 0:
            return self.page
        for frame in self.page.frames:
            try:
                if frame.locator("input[id^='chkRemitoCompraPendiente_']").count() > 0:
                    return frame
            except Exception:  # noqa: BLE001 - frame puede haberse destruido
                continue
        return self.page

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

    def elegir_proveedor(self, cuit: str, razon_social_pista: str | None = None) -> None:
        """Busca al proveedor por CUIT (sin guiones, 11 dígitos) -- más
        confiable que por Razón Social: el nombre impreso en la factura
        puede no coincidir exacto con cómo está cargado en Centum
        (confirmado con DOLZ: "DOLZ ARGENTINA S.A.U." no encontró nada
        buscando por razón social). Si la búsqueda por CUIT trae más de
        un proveedor (puede pasar, como con Nossovitch: "NOSSO
        DESCONTINUADOS", "NOSSO NACIONAL" y "NOSSOVITCH Y CIA S.A."
        comparten CUIT), se refina filtrando por razon_social_pista."""

        def accion():
            # El combo Select2 de arriba del campo Proveedor NO busca contra
            # el backend (sólo filtra lo que ya está precargado en pantalla).
            # La búsqueda real es una ventana aparte que se abre con la lupa
            # -- confirmado inspeccionando: <button id="BtnlstProveedor_Back"
            # ... onclick="SetearOrigenBusquedaProveedor(false);
            # BusquedaProveedorMostrar();">. El campo CUIT de esa ventana es
            # #txtBusquedaProveedorCUIT (confirmado inspeccionando,
            # minlength=11 maxlength=11 -- sin guiones).
            self.page.locator("#BtnlstProveedor_Back").click()
            self.page.locator("#txtBusquedaProveedorCUIT").fill(cuit)
            self.page.get_by_role("button", name="Buscar").click()
            self.page.wait_for_load_state("networkidle")

            # OJO: la columna "Seleccionar" NO es un checkbox real -- es un
            # <button onclick="BusquedaProveedorSeleccionarProveedor(id, razon)">
            # con un ícono que visualmente parece un checkbox tildado
            # (confirmado inspeccionando). Tildar/clickear buscando un
            # input[type=checkbox] no hacía nada porque ese elemento no existe.
            filas = self.page.locator("tr:has(button:has(i.fa-check-square))")
            # OJO: a diferencia de .click(), .count() NO espera a que la
            # grilla termine de renderizar (no hace auto-wait) -- confirmado
            # con un caso real: Centum SÍ encontró 2 proveedores buscando
            # por ese mismo CUIT a mano, pero acá daba 0 porque contaba
            # demasiado rápido, antes de que aparecieran las filas (mismo
            # problema de timing que tuvimos con la grilla de Remitos
            # Pendientes). Hay que esperar explícitamente.
            try:
                filas.first.wait_for(state="attached", timeout=15000)
            except Exception:  # noqa: BLE001 - si de verdad no hay resultados, cantidad da 0 abajo
                pass
            cantidad = filas.count()
            if cantidad == 0:
                raise PasoCentumError(f"No se encontró ningún proveedor con CUIT {cuit} en Centum.")
            if cantidad > 1:
                if not razon_social_pista:
                    raise PasoCentumError(
                        f"La búsqueda por CUIT {cuit} encontró {cantidad} proveedores "
                        f"y no hay razón social para desambiguar cuál usar."
                    )
                filas = filas.filter(has_text=razon_social_pista)
                if filas.count() != 1:
                    raise PasoCentumError(
                        f"La búsqueda por CUIT {cuit} + '{razon_social_pista}' encontró "
                        f"{filas.count()} proveedores (se esperaba exactamente 1)."
                    )
            filas.locator("button:has(i.fa-check-square)").first.click()
            self.page.wait_for_load_state("networkidle")

        self._paso("elegir_proveedor", accion)

    def completar_cabecera(
        self,
        tipo_comprobante: str,
        fecha_documento: str,
        punto_venta: str,
        numero: str,
    ) -> None:
        def accion():
            # Tipo Comprobante también es Select2 (id="lstTipoComprobante",
            # clase select2-hidden-accessible, confirmado con un error real
            # de Playwright) -- a diferencia de Proveedor, acá la lista de
            # opciones es chica y fija, así que el buscador interno de
            # Select2 sí filtra correctamente sin necesitar la lupa.
            self.page.locator("#select2-lstTipoComprobante-container").click()
            self.page.locator("input.select2-search__field").fill(tipo_comprobante)
            self.page.locator("li.select2-results__option", has_text=tipo_comprobante).first.click()
            # Confirmado con error real: id="txtFechaDocumento" (hay varios
            # campos de fecha similares en filtros de búsqueda escondidos).
            self.page.locator("#txtFechaDocumento").fill(fecha_documento)
            # Confirmado con error real: id="txtPuntoVenta" (el otro,
            # "txtPuntoVentaBusqueda", es el filtro de búsqueda de la grilla).
            self.page.locator("#txtPuntoVenta").fill(punto_venta)
            # Confirmado con error real: id="txtNumero" (el otro,
            # "txtNumeroBusqueda", es el filtro de búsqueda de la grilla).
            self.page.locator("#txtNumero").fill(numero)

        self._paso("completar_cabecera", accion)

    def fijar_fecha_documento(self, fecha_documento: str) -> None:
        """Vuelve a fijar Fecha Documento al final de todo el proceso,
        porque algún paso posterior (remitos, conceptos, regímenes) la
        pisa con la fecha de hoy.

        Confirmado con tres intentos fallidos sobre un caso real (DOLZ)
        que escribir el texto con fill() no sirve: es un bootstrap-
        datepicker (confirmado con DevTools real) que guarda su propia
        fecha "seleccionada" en un estado interno de JS, separado del
        texto visible. fill() solo cambia el texto -- el widget nunca se
        entera, y al cerrarse (con Escape, con Tab, con click afuera, da
        igual) repone su estado interno, pisando el texto de nuevo con
        la fecha de hoy.

        La única forma real es clickear el día correcto adentro del
        propio calendario, como lo haría una persona. Cada celda de día
        trae data-date=<epoch en milisegundos, medianoche UTC> (ej.
        5/11/2026 -> data-date="1793836800000"), y el encabezado
        (".datepicker-switch") muestra el mes/año actual en español con
        flechas ".prev"/".next" para navegar -- confirmado con DevTools
        real."""

        def accion():
            dia, mes, anio = (int(x) for x in fecha_documento.split("/"))

            campo = self.page.locator("#txtFechaDocumento")
            campo.click()
            self.page.wait_for_timeout(300)

            switch = self.page.locator(".datepicker-switch").first
            for _ in range(36):
                texto = switch.inner_text().strip().lower()
                partes = texto.split()
                if len(partes) != 2 or not partes[1].isdigit():
                    break
                nombre_mes, anio_mostrado = partes[0], int(partes[1])
                if nombre_mes not in _MESES_ES:
                    break
                mes_mostrado = _MESES_ES.index(nombre_mes) + 1
                delta = (anio - anio_mostrado) * 12 + (mes - mes_mostrado)
                if delta == 0:
                    break
                boton = self.page.locator(".next" if delta > 0 else ".prev").first
                boton.click()
                self.page.wait_for_timeout(200)

            data_date = _epoch_ms_utc(anio, mes, dia)
            self.page.locator(f"td.day[data-date='{data_date}']").first.click()
            self.page.wait_for_timeout(500)

            valor_final = campo.input_value()
            if valor_final != fecha_documento:
                print(
                    f"[aviso] Fecha Documento no quedó en {fecha_documento} "
                    f"(quedó en '{valor_final}') -- revisala y corregila a mano "
                    "en Centum antes de guardar."
                )

        self._paso("fijar_fecha_documento", accion)

    def mostrar_totales(self) -> None:
        """Deja la pantalla scrolleada en la sección Totales (subtotal,
        IVA, Regímenes Especiales, total) al terminar todo el proceso,
        para que la persona que revisa no tenga que buscarla a mano antes
        de decidir si guarda."""

        def accion():
            # Confirmado con error real: hay dos elementos con el texto
            # "Totales" (#phrTotales y #phrTotalesSegmentado) -- se usa el
            # id exacto de la sección principal.
            self.page.locator("#phrTotales").scroll_into_view_if_needed()

        self._paso("mostrar_totales", accion)

    # --- Remitos / artículos ------------------------------------------

    def abrir_remitos_pendientes(self) -> None:
        def accion():
            # Confirmado con error real: el título visible de la sección es
            # el span #LblgrdRemitoCompraArticulosSuscriptos (el otro,
            # #phrRemitoCompraArticulosSuscriptos, está vacío/oculto).
            seccion = self.page.locator("#LblgrdRemitoCompraArticulosSuscriptos")
            seccion.scroll_into_view_if_needed()
            # Confirmado con error real: hay 7 botones "Agregar" en la
            # pantalla (uno por sección). El de esta sección es
            # #btnBuscarRemitosCompraPendientes.
            self.page.locator("#btnBuscarRemitosCompraPendientes").click()
            self.page.wait_for_load_state("networkidle")
            # La grilla tarda un instante más en renderizarse después de
            # que la red queda inactiva (confirmado: justo después de este
            # punto el checkbox todavía no estaba, pero sí apareció unos
            # segundos más tarde al inspeccionar con DevTools). Esperamos
            # explícitamente a que aparezca al menos un checkbox de fila.
            self.page.locator("input[id^='chkRemitoCompraPendiente_']").first.wait_for(
                state="attached", timeout=15000
            )

        self._paso("abrir_remitos_pendientes", accion)

    def listar_remitos_pendientes(self) -> list[dict]:
        """Lee la tabla 'Remitos de Compra Pendientes'. Columnas reales
        confirmadas por captura: [tildar], Copiar Datos Cabecera, Fecha,
        Número, Sucursal, Nota, Moneda, Cotización."""

        def accion():
            # Confirmado con DevTools: cada fila tiene un checkbox con id
            # "chkRemitoCompraPendiente_<id>". Usarlo como ancla es más
            # confiable que buscar "table tr" (que no encontraba nada,
            # probablemente porque esta grilla vive en un iframe --
            # _contenedor_remitos_pendientes ya busca en todos los frames).
            contenedor = self._contenedor_remitos_pendientes()
            checks = contenedor.locator("input[id^='chkRemitoCompraPendiente_']").all()
            remitos = []
            for check in checks:
                fila = check.locator("xpath=ancestor::tr[1]")
                celdas = [c.strip() for c in fila.locator("td").all_inner_texts()]
                if len(celdas) >= 5 and "/" in celdas[2]:
                    remitos.append({"fecha": celdas[2], "numero": celdas[3], "sucursal": celdas[4]})

            if not remitos:
                # DIAGNÓSTICO TEMPORAL: si tampoco aparece el checkbox en
                # ningún frame, puede que "Agregar" haya abierto una
                # pestaña/ventana nueva del navegador (Playwright la ve
                # como otra Page, no como frame de self.page). Imprime
                # pestañas abiertas y sus URLs para confirmarlo con
                # evidencia real, sin adivinar más.
                print(f"[diagnóstico] checkboxes chkRemitoCompraPendiente_*: {len(checks)}")
                if checks:
                    primera = checks[0].locator("xpath=ancestor::tr[1]")
                    print(f"[diagnóstico] celdas fila 0: {primera.locator('td').all_inner_texts()!r}")
                print(f"[diagnóstico] pestañas abiertas en el contexto: {len(self.page.context.pages)}")
                for i, pag in enumerate(self.page.context.pages):
                    print(f"[diagnóstico] pestaña[{i}] url: {pag.url}")
                print(f"[diagnóstico] frames de self.page: {len(self.page.frames)}")
                for i, fr in enumerate(self.page.frames):
                    print(f"[diagnóstico] frame[{i}] url: {fr.url}")

            return remitos

        return self._paso("listar_remitos_pendientes", accion)

    def seleccionar_remitos(self, numeros_remito: list[str]) -> None:
        def accion():
            contenedor = self._contenedor_remitos_pendientes()
            for numero in numeros_remito:
                fila = contenedor.locator("tr", has_text=numero)
                fila.locator("input[id^='chkRemitoCompraPendiente_']").first.check()
            contenedor.get_by_role("button", name=CentumTextos.BOTON_AGREGAR_Y_VOLVER).click()
            self.page.wait_for_load_state("networkidle")
            # Mismo problema de timing que en abrir_remitos_pendientes: tras
            # volver a la pantalla principal, la grilla "Artículos
            # Suscriptos de Remitos de Compra" tarda un instante más en
            # refrescarse con los artículos recién suscriptos (confirmado:
            # leer_articulos_cargados encontró todavía los datos viejos de
            # "Remitos Pendientes" justo después de este click).
            self.page.wait_for_timeout(3000)

        self._paso("seleccionar_remitos", accion)

    def leer_articulos_cargados(self) -> list[RemitoArticleLine]:
        """Lee la tabla de 'Artículos Suscriptos de Remitos de Compra' ya cargada,
        tal como quedó después de suscribir los remitos elegidos."""

        # Confirmado con fila real (BNBJ 12, remito 00009-00150930):
        # celdas[1]=Código propio Proveedor, [2]=Número Remito Compra,
        # [3]=Cantidad Pendiente, [4]=Código Artículo, [10]=Descripción
        # Artículo, [17]=descuento en fracción (0,335000 = 33,5%),
        # [22]=subtotal SIN descuento (precio de lista × cantidad). La
        # cuenta cierra al centavo contra la factura real: 75.113,82 ×
        # (1 - 0,335) = 49.950,69 (= precio_unitario de la factura ×
        # cantidad). precio_unitario acá se guarda como precio de LISTA
        # (sin descuento) y el descuento va en dto1, igual que hace
        # matcher.py con el resto de los proveedores.
        def _leer_una_vez():
            filas = self.page.locator("table tr").all()
            lineas = []
            primera_celdas = None
            for fila in filas:
                # Mismo fix que en listar_remitos_pendientes: hay que
                # apuntar a las celdas (td), no al texto de toda la fila.
                celdas = [c.strip() for c in fila.locator("td").all_inner_texts()]
                if len(celdas) < 23:
                    continue
                if primera_celdas is None:
                    primera_celdas = celdas
                try:
                    cantidad = _num_eu(celdas[3])
                    precio_lista_subtotal = _num_eu(celdas[22])
                    descuento_pct = _num_eu(celdas[17]) * 100
                    lineas.append(
                        RemitoArticleLine(
                            codigo_articulo=celdas[4],
                            descripcion=celdas[10],
                            numero_remito=celdas[2],
                            cantidad=cantidad,
                            precio_unitario=precio_lista_subtotal / cantidad if cantidad else 0.0,
                            dto1=descuento_pct,
                            # Confirmado con el usuario: Centum guarda el
                            # código tal como lo usa el proveedor (idéntico
                            # al de la factura) en esta columna -- siempre
                            # hay que usarlo para comparar, en vez del
                            # Código Artículo interno.
                            codigo_propio_proveedor=celdas[1],
                        )
                    )
                except (ValueError, IndexError, ZeroDivisionError):
                    continue
            return lineas, filas, primera_celdas

        def accion():
            lineas, filas, primera_celdas = _leer_una_vez()

            if not lineas:
                # Reintento: tras suscribir el remito, la grilla puede
                # tardar más en refrescarse si trae muchas filas (388 en
                # la prueba real) -- confirmado que justo después del
                # click todavía se veían los datos viejos de la pantalla
                # anterior.
                self.page.wait_for_timeout(3000)
                lineas, filas, primera_celdas = _leer_una_vez()

            if not lineas:
                # DIAGNÓSTICO TEMPORAL: mismo patrón que en
                # listar_remitos_pendientes -- si "table tr" no encuentra
                # nada (0 filas con >=6 celdas) ni con el reintento,
                # imprime pistas reales (checkboxes de la pantalla y su
                # id) para poder anclar la búsqueda igual que se hizo con
                # chkRemitoCompraPendiente_*.
                print(f"[diagnóstico] <table> en la página: {self.page.locator('table').count()}")
                print(f"[diagnóstico] <tr> en la página: {len(filas)}")
                if primera_celdas is not None:
                    print(f"[diagnóstico] celdas primera fila con datos: {primera_celdas!r}")
                checks = self.page.locator("input[type=checkbox]").all()
                print(f"[diagnóstico] checkboxes (cualquiera) en la página: {len(checks)}")
                for i, c in enumerate(checks[:6]):
                    print(f"[diagnóstico] checkbox[{i}] id: {c.get_attribute('id')!r}")

            return lineas

        return self._paso("leer_articulos_cargados", accion)

    def marcar_articulo_para_eliminar(self, numero_remito: str, codigo_articulo: str) -> None:
        """Tilda un artículo excedente para sacarlo del comprobante actual
        (queda sin suscribir, se puede traer en uno siguiente). Confirmado
        con el usuario: acá "tildar" es al revés de lo que se podría
        suponer -- significa marcar PARA ELIMINAR, no para incluir. Hay
        que tildar todos los excedentes y recién al final llamar a
        eliminar_articulos_marcados() una sola vez (no un click de
        eliminar por artículo)."""

        def accion():
            fila = self.page.locator("tr", has_text=codigo_articulo).filter(has_text=numero_remito)
            fila.locator("input[type=checkbox]").first.check()

        self._paso(f"marcar_para_eliminar_{codigo_articulo}", accion)

    def eliminar_articulos_marcados(self) -> None:
        """Saca del comprobante actual todos los artículos tildados con
        marcar_articulo_para_eliminar. Confirmado con DevTools: botón sin
        texto (ícono de tacho), id real btnEliminarRemitoCompraArticulos,
        onclick="EliminarRemitoCompraArticulosSuscriptos();"."""

        def accion():
            self.page.locator("#btnEliminarRemitoCompraArticulos").click()
            self.page.wait_for_load_state("networkidle")

        self._paso("eliminar_articulos_marcados", accion)

    # --- Conceptos y regímenes especiales -------------------------------

    def agregar_concepto(self, concepto: ConceptoFaltante) -> None:
        def accion():
            # Confirmado con error real (mismo listado de 7 botones
            # "Agregar" que reveló abrir_remitos_pendientes): el de esta
            # sección es #btnAgregarConceptos.
            self.page.locator("#btnAgregarConceptos").click()
            self.page.get_by_text(CentumTextos.CONCEPTO_MERCADERIAS_VARIAS).click()
            # Confirmado con error real: get_by_label("Observaciones") era
            # ambiguo (resolvía a 5 elementos) -- el campo real es
            # #txtObservaciones. También confirmado con error real: el
            # panel tarda en desplegarse después de elegir "Mercaderías
            # varias", el campo existe en el DOM pero todavía no es
            # visible -- mismo patrón que el botón de Regímenes
            # Especiales, se scrollea hasta él antes de escribir.
            campo_observaciones = self.page.locator("#txtObservaciones")
            campo_observaciones.scroll_into_view_if_needed()
            campo_observaciones.fill(concepto.observaciones)
            # Coma decimal, no punto -- confirmado necesario en Regímenes
            # Especiales, se asume el mismo comportamiento acá.
            self.page.get_by_label("Cantidad").fill(_formato_decimal_ar(concepto.cantidad))
            self.page.get_by_label("Precio").fill(_formato_decimal_ar(concepto.precio))

        self._paso("agregar_concepto", accion)

    def abrir_regimenes_no_asignados(self) -> None:
        """Abre la lista completa de Regímenes Especiales disponibles
        (901 CABA, 902 BsAs, ... y muchos más, confirmado con captura
        real). Hay que abrirla UNA sola vez, escribir ahí todos los
        importes que hagan falta con marcar_regimen_especial(), y recién
        al final confirmar con confirmar_regimenes_especiales() -- igual
        que con los excedentes (tildar todos, eliminar en un solo paso)."""

        def accion():
            # Confirmado con error real (mismo listado de 7 botones
            # "Agregar"): el de esta sección es #btnAgregarRegimenesEspeciales.
            # Confirmado con error real: el botón existe en el DOM pero no
            # es visible sin hacer scroll primero (la sección queda más
            # abajo en la pantalla).
            boton = self.page.locator("#btnAgregarRegimenesEspeciales")
            boton.scroll_into_view_if_needed()
            boton.click()
            self.page.wait_for_load_state("networkidle")

        self._paso("abrir_regimenes_no_asignados", accion)

    def marcar_regimen_especial(self, etiqueta_centum: str, importe: float) -> None:
        """etiqueta_centum: el texto EXACTO de la fila en Centum (ej.
        "Percepcion IIBB BsAs (C)"), ya traducido desde la jurisdicción
        que trae el PDF -- cada proveedor la llama distinto, nunca
        coincide literal con Centum (ver jurisdiccion_centum() en
        provider_identificacion.py). Siempre hay que usar la fila que
        termina en "(C)" de Compra, nunca "(V)" de Venta. Confirmado con
        DevTools: el campo de importe de cada fila tiene id
        "txtRegimenEspecialPrecio_<N>" (N es un índice interno, no el
        número de régimen que se ve en pantalla) -- se ubica por la fila
        que contiene el texto de la etiqueta, no por ese id directo. El
        campo de Observaciones de cada fila NO se completa (salvo casos
        puntuales todavía no definidos). Requiere haber llamado antes a
        abrir_regimenes_no_asignados()."""

        def accion():
            fila = self.page.locator("tr", has_text=etiqueta_centum)
            fila.locator("input[id^='txtRegimenEspecialPrecio_']").fill(_formato_decimal_ar(importe))

        self._paso(f"marcar_regimen_especial_{etiqueta_centum}", accion)

    def confirmar_regimenes_especiales(self) -> None:
        """Confirma todos los importes escritos con marcar_regimen_especial()
        y vuelve a la pantalla del comprobante. Confirmado con DevTools:
        botón sin texto único en pantalla (hay "Agregar" y "Agregar y
        Volver") -- se usa el id real, btnAgregarRegimenesEspecialesOKYVolver."""

        def accion():
            self.page.locator("#btnAgregarRegimenesEspecialesOKYVolver").click()
            self.page.wait_for_load_state("networkidle")

        self._paso("confirmar_regimenes_especiales", accion)

    # --- Lectura final ---------------------------------------------------

    def leer_subtotal_sistema(self) -> float:
        def accion():
            texto = self.page.get_by_text(CentumTextos.LABEL_SUBTOTAL_DESC).locator("..").inner_text()
            digitos = "".join(c for c in texto if c.isdigit() or c in ",.")
            return float(digitos.replace(".", "").replace(",", "."))

        return self._paso("leer_subtotal_sistema", accion)
