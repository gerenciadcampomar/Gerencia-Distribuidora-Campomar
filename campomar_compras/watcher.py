"""Vigila `C:\\Campomar\\facturas_pendientes` (configurable) y, por cada PDF
nuevo, prepara automáticamente la compra en Centum hasta el punto previo a
Guardar. NUNCA hace click en Guardar/Cerrar -- eso lo hace siempre una
persona, mirando la pantalla real.

Uso (Windows, PowerShell):

    $env:CENTUM_URL = "https://plataforma5.centum.com.ar:22480/CentumSuite/BL1?dominio=DistribuidoraCampomarSA"
    $env:CENTUM_USUARIO = "tu_usuario"
    $env:CENTUM_CONTRASENA = "tu_contraseña"
    # Opcional: el combo "Sucursal" del login no es obligatorio en Centum.
    $env:CENTUM_SUCURSAL = "Distribuidora Campomar"
    python -m campomar_compras.watcher --carpeta-base C:\\Campomar

Primero probar con --modo solo-login (no toca ningún dato, solo confirma
que el login y la navegación a Compras funcionan) antes de usar --modo
completo.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from campomar_compras.centum_browser_client import CentumBrowserClient, CentumCredenciales, PasoCentumError
from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import match_remitos_contra_factura, proponer_conceptos_faltantes
from campomar_compras.models import MatchResult
from campomar_compras.provider_identificacion import (
    cuit_proveedor,
    extraer_fecha_factura,
    identificar_proveedor,
    jurisdiccion_centum,
    nombre_centum,
    tipo_descuento_centum,
)
from campomar_compras.provider_parsers import (
    PROVIDER_COMPROBANTE_PARSERS,
    PROVIDER_FECHA_PARSERS,
    PROVIDER_PARSERS,
    PROVIDER_REGIMENES_PARSERS,
    regla_codigo_ralux,
)
from campomar_compras.validator import validar_subtotal

REGLAS_CODIGO = {"ralux": regla_codigo_ralux}

POLL_SEGUNDOS = 10


def notificar_usuario(titulo: str, mensaje: str) -> None:
    """Aviso nativo de Windows. Hace falta porque el modo completo corre
    en segundo plano -- un print en la consola no alcanza si no hay
    nadie mirando la terminal en ese momento.

    OJO: se probó primero con win10toast y después con win11toast
    (notificaciones tipo "toast" del Centro de actividades), pero
    Windows las descartaba en silencio -- confirmado mirando
    Configuración > Notificaciones: un script de Python sin empaquetar
    no queda registrado ahí como app, así que Windows nunca las
    muestra. Un MessageBox clásico (ctypes, ya viene con Python, sin
    librerías extra) sí funciona siempre porque no depende de ningún
    registro ni permiso. Es modal (bloquea hasta que se cierra) --
    buscado a propósito, para no perderse el aviso."""
    try:
        import ctypes

        MB_OK = 0x0
        MB_ICONINFORMATION = 0x40
        MB_SYSTEMMODAL = 0x1000  # para que aparezca siempre arriba de todo
        ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL)
    except Exception as exc:  # noqa: BLE001 - un fallo acá nunca debe tumbar el proceso
        print(f"[aviso] no pude mostrar el cartel de Windows: {exc}")


@dataclass
class Carpetas:
    base: Path

    @property
    def pendientes(self) -> Path:
        return self.base / "facturas_pendientes"

    @property
    def procesadas(self) -> Path:
        return self.base / "facturas_procesadas"

    @property
    def reportes(self) -> Path:
        return self.base / "reportes"

    def crear(self) -> None:
        for carpeta in (self.pendientes, self.procesadas, self.reportes):
            carpeta.mkdir(parents=True, exist_ok=True)


def _credenciales_desde_entorno() -> CentumCredenciales:
    faltantes = [
        var
        for var in ("CENTUM_URL", "CENTUM_USUARIO", "CENTUM_CONTRASENA")
        if not os.environ.get(var)
    ]
    if faltantes:
        raise SystemExit(
            "Faltan variables de entorno: " + ", ".join(faltantes) + ". Ver el docstring de este archivo."
        )
    return CentumCredenciales(
        url_login=os.environ["CENTUM_URL"],
        usuario=os.environ["CENTUM_USUARIO"],
        contrasena=os.environ["CENTUM_CONTRASENA"],
        sucursal=os.environ.get("CENTUM_SUCURSAL", ""),
    )


RANGO_DIAS_REMITOS = 21  # "tres semanas", confirmado con el usuario.


def _elegir_remitos_mas_cercanos(remitos_pendientes: list[dict], fecha_factura) -> list[str]:
    """Toma los remitos pendientes del proveedor cuya fecha esté dentro de
    RANGO_DIAS_REMITOS días de la fecha de la factura (antes o después,
    porque el remito puede llegar un poco después de emitida la factura),
    ordenados del más cercano al más lejano. Si no hay fecha de factura,
    los toma todos (hay que revisarlo a mano en ese caso)."""
    import datetime as _dt

    def _parse(fecha_str: str) -> _dt.date:
        d, m, a = (int(x) for x in fecha_str.split("/"))
        return _dt.date(a, m, d)

    if fecha_factura is None:
        return [r["numero"] for r in remitos_pendientes]

    candidatos = [
        r for r in remitos_pendientes if abs((_parse(r["fecha"]) - fecha_factura).days) <= RANGO_DIAS_REMITOS
    ]
    candidatos.sort(key=lambda r: abs((_parse(r["fecha"]) - fecha_factura).days))
    return [r["numero"] for r in candidatos]


def procesar_factura(
    pdf_path: Path,
    client: CentumBrowserClient,
    carpetas: Carpetas,
    tipo_comprobante: str = "Factura de Compras",
    punto_venta: str | None = None,
    numero: str | None = None,
    es_ultima_del_lote: bool = False,
) -> str:
    """Devuelve el texto del reporte generado para esta factura.

    OJO: punto_venta y numero todavía hay que pasarlos a mano (leídos del
    PDF por una persona) -- falta un extractor automático del número de
    comprobante (ej. "0015-00141664") para el modo completo. La fecha sí
    se extrae sola del PDF.

    es_ultima_del_lote: confirmado con el usuario que un código remitido
    de más (excedente, total o parcial) no es un problema de ESTA factura
    -- se ajusta/elimina igual y "es problema del siguiente comprobante".
    Pero si esta es la ÚLTIMA factura del lote que se está procesando, no
    hay un "siguiente comprobante" en esta corrida que se haga cargo, así
    que el reporte lo marca con una alerta aparte para que se revise (capaz
    hay otra factura o un presupuesto del proveedor, todavía no cargado,
    que lo cubre)."""
    slug = identificar_proveedor(pdf_path)
    if slug is None or slug not in PROVIDER_PARSERS:
        return (
            f"NO PROCESADO: no pude identificar el proveedor de '{pdf_path.name}' "
            f"(CUIT no encontrado en campomar_compras/providers/cuits.json, o no "
            f"hay parser para ese proveedor todavía). Revisar a mano."
        )

    cuit_buscar = cuit_proveedor(slug)
    if cuit_buscar is None:
        return f"NO PROCESADO: proveedor '{slug}' identificado, pero no tiene CUIT en cuits.json."
    # El nombre en nombres_centum.json es opcional: solo hace falta para
    # desambiguar cuando la búsqueda por CUIT trae más de un proveedor
    # (ver elegir_proveedor) -- si el CUIT es único en Centum (el caso
    # normal), no se necesita. Si hace falta y no está, elegir_proveedor
    # tira un PasoCentumError claro pidiéndolo, en vez de frenar acá.
    nombre_buscar = nombre_centum(slug)
    # Algunos proveedores (ej. Some) tienen el descuento general cargado
    # por un código del combo "Tipo de Descuento de Proveedor" en vez de
    # por artículo (ver descuentos_centum.json) -- si no aplica, queda en
    # None y completar_cabecera no toca ese campo.
    tipo_descuento_buscar = tipo_descuento_centum(slug)

    parse_lineas, parse_totales = PROVIDER_PARSERS[slug]
    lineas_factura = parse_lineas(pdf_path)
    totales_factura = parse_totales(pdf_path)
    # Algunos proveedores necesitan un extractor de fecha a medida (ver
    # PROVIDER_FECHA_PARSERS) -- el genérico asume la etiqueta "Fecha"
    # pegada al número con barras, y no todos los PDF lo imprimen así.
    extractor_fecha = PROVIDER_FECHA_PARSERS.get(slug, extraer_fecha_factura)
    fecha_factura = extractor_fecha(pdf_path)
    fecha_documento = fecha_factura.strftime("%d/%m/%Y") if fecha_factura else None
    mapping = CodeMappingStore(slug, regla=REGLAS_CODIGO.get(slug))

    client.ir_a_compras_crear()
    client.elegir_proveedor(cuit_buscar, nombre_buscar)
    client.completar_cabecera(
        tipo_comprobante=tipo_comprobante,
        fecha_documento=fecha_documento,
        punto_venta=punto_venta,
        numero=numero,
        tipo_descuento_proveedor=tipo_descuento_buscar,
    )
    client.abrir_remitos_pendientes()
    remitos_pendientes = client.listar_remitos_pendientes()
    numeros_elegidos = _elegir_remitos_mas_cercanos(remitos_pendientes, fecha_factura)
    client.seleccionar_remitos(numeros_elegidos)

    remitos_cargados = client.leer_articulos_cargados()
    resultados = match_remitos_contra_factura(remitos_cargados, lineas_factura, mapping)

    excedentes = [r for r in resultados if r.status.value == "excedente"]
    for r in excedentes:
        # Un código puede venir repetido en más de un renglón del remito
        # (ver matcher.py) -- si está excedente, se elimina cada renglón.
        for remito_line in r.remito_lines:
            client.marcar_articulo_para_eliminar(
                remito_line.numero_remito, remito_line.codigo_articulo, remito_line.codigo_propio_proveedor
            )
    if excedentes:
        client.eliminar_articulos_marcados()

    # Excedente parcial (mismo código, el remito trae más cantidad que la
    # factura): se baja la cantidad a lo facturado, dejando el resto
    # pendiente de suscribir para otra factura del proveedor. Si el
    # código vino repetido en más de un renglón del remito, no hay forma
    # de decidir sola en cuál de los varios aplicar el ajuste -- ahí
    # queda para revisión manual (ver reporte). El ajuste en sí se hace
    # más abajo, al final de todo el proceso -- confirmado con error
    # real que un paso posterior (Conceptos/Regímenes) lo pisaba.
    excedentes_parciales = [r for r in resultados if r.status.value == "excedente_parcial"]
    excedentes_parciales_sin_ajustar = [r for r in excedentes_parciales if len(r.remito_lines) != 1]

    conceptos = proponer_conceptos_faltantes(resultados)
    if conceptos:
        client.abrir_conceptos()
        for i, concepto in enumerate(conceptos):
            es_ultimo = i == len(conceptos) - 1
            client.cargar_concepto_mercaderias_varias(concepto, confirmar=es_ultimo)

    regimenes_parser = PROVIDER_REGIMENES_PARSERS.get(slug)
    regimenes = regimenes_parser(pdf_path) if regimenes_parser else []
    regimenes_sin_traducir = []
    regimenes_a_cargar = []
    for regimen in regimenes:
        etiqueta = jurisdiccion_centum(regimen.jurisdiccion)
        if etiqueta is None:
            # No adivinamos la fila de Centum -- cada proveedor llama a la
            # jurisdicción distinto, hay que confirmar el texto exacto a
            # mano la primera vez (ver jurisdicciones_centum.json) antes
            # de cargarla sola.
            regimenes_sin_traducir.append(regimen)
            continue
        regimenes_a_cargar.append((etiqueta, regimen))

    if regimenes_a_cargar:
        # Se abre la lista UNA sola vez, se escriben todos los importes,
        # y recién al final se confirma -- igual que con los excedentes
        # (tildar todos, eliminar en un solo paso).
        client.abrir_regimenes_no_asignados()
        for etiqueta, regimen in regimenes_a_cargar:
            client.marcar_regimen_especial(etiqueta, regimen.importe)
        client.confirmar_regimenes_especiales()

    ajustes_no_verificados: list[MatchResult] = []
    for r in excedentes_parciales:
        if len(r.remito_lines) == 1:
            remito_line = r.remito_lines[0]
            verificado = client.ajustar_cantidad_excedente_parcial(
                remito_line.numero_remito,
                remito_line.codigo_articulo,
                r.invoice_line.cantidad,
                remito_line.codigo_propio_proveedor,
            )
            if not verificado:
                ajustes_no_verificados.append(r)

    if fecha_documento:
        # El usuario notó que "Fecha Documento" puede quedar pisada con
        # la fecha de hoy por algún paso posterior (remitos, conceptos,
        # regímenes) -- la sincronización automática con "Fecha
        # Imputación" que hace Centum cuando una persona edita el campo
        # a mano no se dispara con el fill() del script. Se vuelve a
        # fijar acá, al final de todo, para asegurar que quede bien.
        client.fijar_fecha_documento(fecha_documento)

    # Deja la pantalla en la sección Totales (subtotal, IVA, Regímenes
    # Especiales, total) para que quien revisa la vea de entrada, sin
    # tener que scrollear.
    client.mostrar_totales()

    # Se compara contra el total REAL que Centum calculó (leído de la
    # pantalla), no contra nuestra propia cuenta en Python -- Centum
    # puede tener su propio redondeo al calcular descuentos que no
    # siempre coincide exacto con una cuenta simple hecha acá. Si por
    # algún motivo no se puede leer, se cae al cálculo propio como
    # respaldo en vez de frenar todo el proceso.
    try:
        subtotal_sistema_real = client.leer_subtotal_con_descuento()
    except PasoCentumError:
        subtotal_sistema_real = None

    validacion = validar_subtotal(resultados, totales_factura, subtotal_sistema_real=subtotal_sistema_real)

    numero_comprobante = f"{punto_venta}-{numero}" if punto_venta and numero else pdf_path.name
    if validacion.ok:
        notificar_usuario(
            f"Compra lista para guardar ({slug})",
            f"Comprobante {numero_comprobante}: códigos, cantidades, precios y "
            f"descuentos OK. {validacion.detalle} Revisá y guardá en Centum.",
        )
    else:
        notificar_usuario(
            f"Compra con diferencias ({slug})",
            f"Comprobante {numero_comprobante}: {validacion.detalle} NO guardes sin revisar.",
        )

    lineas_reporte = [
        f"Factura: {pdf_path.name}",
        f"Proveedor: {slug}",
        f"Remitos elegidos: {', '.join(numeros_elegidos) or '(ninguno)'}",
        "",
    ]
    for r in resultados:
        codigo = r.remito_lines[0].codigo_articulo if r.remito_lines else r.invoice_line.codigo_proveedor
        lineas_reporte.append(f"  [{r.status.value}] {codigo}  {r.notes}")
    excedentes_parciales_ajustados = [r for r in excedentes_parciales if r not in excedentes_parciales_sin_ajustar]
    if excedentes_parciales_ajustados:
        lineas_reporte.append("")
        lineas_reporte.append("Cantidad ajustada (el proveedor entregó de más que lo facturado):")
        for r in excedentes_parciales_ajustados:
            codigo = r.remito_lines[0].codigo_articulo
            aviso = "  >>> NO SE PUDO VERIFICAR, revisalo a mano <<<" if r in ajustes_no_verificados else ""
            lineas_reporte.append(
                f"  {codigo}: sobraban {r.cantidad_diferencia}, quedó en {r.invoice_line.cantidad}{aviso}"
            )
    if excedentes_parciales_sin_ajustar:
        lineas_reporte.append("")
        lineas_reporte.append(
            ">>> ATENCIÓN: hay artículos remitidos de más en más de un renglón repetido "
            "del mismo código -- no se pudo decidir solo cuál ajustar, hacelo a mano: <<<"
        )
        for r in excedentes_parciales_sin_ajustar:
            codigo = r.remito_lines[0].codigo_articulo
            lineas_reporte.append(
                f"  {codigo}: sobran {r.cantidad_diferencia} entre {len(r.remito_lines)} renglones "
                f"(dejar {r.invoice_line.cantidad} en total)"
            )
    if regimenes:
        cargados = [r for r in regimenes if r not in regimenes_sin_traducir]
        if cargados:
            lineas_reporte.append("")
            lineas_reporte.append("Regímenes Especiales cargados:")
            for regimen in cargados:
                lineas_reporte.append(f"  {regimen.jurisdiccion}: ${regimen.importe:,.2f}")
    if regimenes_sin_traducir:
        lineas_reporte.append("")
        lineas_reporte.append(
            ">>> ATENCIÓN: hay Regímenes Especiales en la factura que el agente NO "
            "sabe a qué fila de Centum corresponden -- cargalos a mano: <<<"
        )
        for regimen in regimenes_sin_traducir:
            lineas_reporte.append(f"  {regimen.jurisdiccion}: ${regimen.importe:,.2f}")

    # Un excedente (total o parcial) no es problema de ESTA factura --
    # confirmado con el usuario: se ajusta/elimina igual y queda pendiente
    # para la próxima. Pero si esta es la ÚLTIMA factura del lote, no hay
    # "próxima" en esta corrida que se haga cargo, así que se marca aparte
    # para que se revise (puede haber otra factura o un presupuesto del
    # proveedor, todavía no cargado, que sí lo cubra).
    if es_ultima_del_lote and (excedentes or excedentes_parciales):
        lineas_reporte.append("")
        lineas_reporte.append(
            ">>> ÚLTIMA FACTURA DEL LOTE: esto quedó pendiente de suscribir y "
            "NINGUNA otra factura de esta corrida lo va a cubrir -- si no esperás "
            "otra factura o un presupuesto del proveedor que lo incluya, revisalo: <<<"
        )
        for r in excedentes:
            for remito_line in r.remito_lines:
                lineas_reporte.append(
                    f"  {remito_line.codigo_articulo}: {remito_line.cantidad} eliminado de esta compra"
                )
        for r in excedentes_parciales:
            codigo = r.remito_lines[0].codigo_articulo
            lineas_reporte.append(f"  {codigo}: {r.cantidad_diferencia} quedó pendiente")
        notificar_usuario(
            f"Quedó pendiente de suscribir ({slug})",
            f"Comprobante {numero_comprobante}: es la última factura de esta tanda, "
            "y quedaron artículos remitidos de más que ninguna otra factura de esta "
            "corrida va a cubrir. Revisá el reporte antes de dar por cerrado el "
            "proveedor.",
        )

    # "Marcar las diferencias" -- pedido del usuario para saber de dónde
    # sale la diferencia entre el subtotal del sistema y el de la
    # factura, en vez de solo un número agregado al final. Estimación por
    # código de cuánto de más (o de menos) queda en el sistema respecto
    # de lo facturado, para los casos que el matcher ya sabe que no
    # cierran solos.
    impactos: list[tuple[str, float, str]] = []
    for r in resultados:
        if r.status.value == "precio_difiere":
            codigo = r.remito_lines[0].codigo_articulo
            subtotal_remito = sum(linea.subtotal for linea in r.remito_lines)
            impacto = round(subtotal_remito - r.invoice_line.subtotal, 2)
            impactos.append((codigo, impacto, "precio distinto"))
    for r in excedentes_parciales_sin_ajustar:
        codigo = r.remito_lines[0].codigo_articulo
        cantidad_remito = sum(linea.cantidad for linea in r.remito_lines)
        precio_neto = sum(linea.subtotal for linea in r.remito_lines) / cantidad_remito if cantidad_remito else 0.0
        impacto = round(r.cantidad_diferencia * precio_neto, 2)
        impactos.append((codigo, impacto, "sin ajustar, sobra cantidad entre varios renglones"))
    for r in ajustes_no_verificados:
        codigo = r.remito_lines[0].codigo_articulo
        cantidad_remito = sum(linea.cantidad for linea in r.remito_lines)
        precio_neto = sum(linea.subtotal for linea in r.remito_lines) / cantidad_remito if cantidad_remito else 0.0
        impacto = round(r.cantidad_diferencia * precio_neto, 2)
        impactos.append((codigo, impacto, "ajuste de cantidad no se pudo verificar"))
    if impactos:
        impactos.sort(key=lambda t: abs(t[1]), reverse=True)
        lineas_reporte.append("")
        lineas_reporte.append("Diferencias que explican el subtotal (de mayor a menor):")
        for codigo, impacto, motivo in impactos:
            signo = "sistema de más" if impacto > 0 else "sistema de menos"
            lineas_reporte.append(f"  {codigo}: ${abs(impacto):,.2f} ({signo}, {motivo})")

    lineas_reporte += [
        "",
        f"Subtotal sistema: ${validacion.subtotal_sistema:,.2f}",
        f"Subtotal factura: ${validacion.subtotal_factura:,.2f}",
        validacion.detalle,
        "",
        ">>> PASS: anda a Centum, revisá la compra y guardala vos mismo. <<<"
        if validacion.ok
        else ">>> FAIL: NO guardes, revisá las diferencias. La pestaña quedó abierta. <<<",
    ]
    return "\n".join(lineas_reporte)


def correr_watcher(
    carpeta_base: Path,
    modo: str,
    headless: bool,
    proveedor_test: str | None = None,
    tipo_comprobante: str | None = None,
    fecha_documento: str | None = None,
    punto_venta: str | None = None,
    numero: str | None = None,
    factura_pdf: str | None = None,
) -> None:
    carpetas = Carpetas(carpeta_base)
    carpetas.crear()
    credenciales = _credenciales_desde_entorno()

    with sync_playwright() as p:
        # --start-maximized + viewport=None: si no, Chromium arranca con un
        # viewport chico (1280x720 por defecto) adentro de una ventana más
        # grande y queda una franja gris a la derecha/abajo sin usar.
        browser = p.chromium.launch(headless=headless, args=None if headless else ["--start-maximized"])
        context = browser.new_context(no_viewport=not headless)
        page = context.new_page()
        client = CentumBrowserClient(page, directorio_debug=carpetas.base / "debug")
        client.login(credenciales)

        if modo == "solo-login":
            print("Login OK. Dejo el navegador abierto 30s para que confirmes visualmente.")
            time.sleep(30)
            return

        if modo == "solo-navegacion":
            client.ir_a_compras_crear()
            print("Navegación a Compras > Compras > Crear OK. Dejo el navegador abierto 30s.")
            time.sleep(30)
            return

        if modo == "solo-proveedor":
            client.ir_a_compras_crear()
            nombre = nombre_centum(proveedor_test) if proveedor_test else None
            cuit = cuit_proveedor(proveedor_test) if proveedor_test else None
            if not nombre or not cuit:
                raise SystemExit(
                    f"Falta nombre y/o CUIT para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json y cuits.json, "
                    f"o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(cuit, nombre)
            print(f"Elegir proveedor '{nombre}' OK. Dejo el navegador abierto 30s.")
            time.sleep(30)
            return

        if modo == "solo-remitos":
            client.ir_a_compras_crear()
            nombre = nombre_centum(proveedor_test) if proveedor_test else None
            cuit = cuit_proveedor(proveedor_test) if proveedor_test else None
            if not nombre or not cuit:
                raise SystemExit(
                    f"Falta nombre y/o CUIT para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json y cuits.json, "
                    f"o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(cuit, nombre)
            client.completar_cabecera(
                tipo_comprobante=tipo_comprobante or "Factura de Compras",
                fecha_documento=fecha_documento,
                punto_venta=punto_venta,
                numero=numero,
            )
            client.abrir_remitos_pendientes()
            remitos = client.listar_remitos_pendientes()
            print(f"Cabecera OK. Remitos pendientes encontrados: {len(remitos)}")
            for r in remitos:
                print(f"  {r}")
            print("Dejo el navegador abierto 30s.")
            time.sleep(30)
            return

        if modo == "solo-articulos":
            client.ir_a_compras_crear()
            nombre = nombre_centum(proveedor_test) if proveedor_test else None
            cuit = cuit_proveedor(proveedor_test) if proveedor_test else None
            if not nombre or not cuit:
                raise SystemExit(
                    f"Falta nombre y/o CUIT para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json y cuits.json, "
                    f"o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(cuit, nombre)
            client.completar_cabecera(
                tipo_comprobante=tipo_comprobante or "Factura de Compras",
                fecha_documento=fecha_documento,
                punto_venta=punto_venta,
                numero=numero,
            )
            client.abrir_remitos_pendientes()
            remitos = client.listar_remitos_pendientes()
            fecha_factura = None
            if fecha_documento:
                import datetime as _dt

                d, m, a = (int(x) for x in fecha_documento.split("/"))
                fecha_factura = _dt.date(a, m, d)
            numeros_elegidos = _elegir_remitos_mas_cercanos(remitos, fecha_factura)
            print(f"Remitos elegidos (ventana de {RANGO_DIAS_REMITOS} días): {numeros_elegidos}")
            client.seleccionar_remitos(numeros_elegidos)
            articulos = client.leer_articulos_cargados()
            print(f"Artículos cargados: {len(articulos)}")
            for a in articulos:
                print(f"  {a}")
            print("Dejo el navegador abierto 30s.")
            time.sleep(30)
            return

        if modo == "solo-eliminar":
            client.ir_a_compras_crear()
            nombre = nombre_centum(proveedor_test) if proveedor_test else None
            cuit = cuit_proveedor(proveedor_test) if proveedor_test else None
            if not nombre or not cuit:
                raise SystemExit(
                    f"Falta nombre y/o CUIT para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json y cuits.json, "
                    f"o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(cuit, nombre)
            client.completar_cabecera(
                tipo_comprobante=tipo_comprobante or "Factura de Compras",
                fecha_documento=fecha_documento,
                punto_venta=punto_venta,
                numero=numero,
            )
            client.abrir_remitos_pendientes()
            remitos = client.listar_remitos_pendientes()
            fecha_factura = None
            if fecha_documento:
                import datetime as _dt

                d, m, a = (int(x) for x in fecha_documento.split("/"))
                fecha_factura = _dt.date(a, m, d)
            numeros_elegidos = _elegir_remitos_mas_cercanos(remitos, fecha_factura)
            client.seleccionar_remitos(numeros_elegidos)
            articulos = client.leer_articulos_cargados()
            if not articulos:
                raise SystemExit("No se cargó ningún artículo, no hay nada para eliminar de prueba.")
            primero = articulos[0]
            print(f"Voy a marcar para eliminar (solo de prueba): {primero}")
            client.marcar_articulo_para_eliminar(
                primero.numero_remito, primero.codigo_articulo, primero.codigo_propio_proveedor
            )
            client.eliminar_articulos_marcados()
            print("eliminar_articulos_marcados OK (sin error). Dejo el navegador abierto 30s para que confirmes visualmente.")
            time.sleep(30)
            return

        if modo == "solo-validar":
            if factura_pdf:
                pdfs_a_procesar = [(Path(factura_pdf), punto_venta, numero)]
            else:
                # No hace falta escribir ni renombrar nada: toma todos los
                # PDFs que haya en facturas_pendientes y los procesa de a
                # uno, en orden de la FECHA DE LA FACTURA (no del nombre
                # del archivo) -- pedido del usuario, para suscribirlos en
                # el orden real en que se facturaron. punto_venta/numero
                # se sacan solos del PDF para los proveedores que lo
                # tengan calibrado (ver PROVIDER_COMPROBANTE_PARSERS); si
                # no, quedan en blanco para completarlos a mano en Centum.
                pendientes = sorted(carpetas.pendientes.glob("*.pdf"))
                if not pendientes:
                    raise SystemExit(
                        f"No hay ningún PDF en {carpetas.pendientes} y no pasaste --factura-pdf. "
                        "Poné la factura ahí (con el nombre que tenga) o pasá la ruta con --factura-pdf."
                    )

                def _fecha_para_orden(p: Path) -> datetime.date:
                    try:
                        slug_p = identificar_proveedor(p)
                        extractor = PROVIDER_FECHA_PARSERS.get(slug_p, extraer_fecha_factura)
                        return extractor(p) or datetime.date.max
                    except Exception:  # noqa: BLE001 - si no se puede leer, va al final
                        return datetime.date.max

                pendientes.sort(key=_fecha_para_orden)

                def _comprobante_para(p: Path) -> tuple[str | None, str | None]:
                    try:
                        slug_p = identificar_proveedor(p)
                        extractor = PROVIDER_COMPROBANTE_PARSERS.get(slug_p)
                        if extractor is None:
                            return None, None
                        resultado = extractor(p)
                        return resultado if resultado else (None, None)
                    except Exception:  # noqa: BLE001 - si no se puede leer, queda para completar a mano
                        return None, None

                pdfs_a_procesar = [(p, *_comprobante_para(p)) for p in pendientes]
                if len(pdfs_a_procesar) > 1:
                    print(f"Encontré {len(pdfs_a_procesar)} PDFs pendientes, los proceso en orden de fecha de factura:")
                    for p, _, _ in pdfs_a_procesar:
                        print(f"  - {p.name}")

            pagina_actual = page
            client_actual = client
            for i, (pdf_path, pv, num) in enumerate(pdfs_a_procesar):
                if i > 0:
                    # Pestaña nueva en el mismo contexto -- comparte
                    # cookies/sesión, así que no hace falta volver a
                    # completar usuario/contraseña. Pero la pestaña en sí
                    # arranca en blanco (about:blank): confirmado con
                    # error real que, sin este goto, ir_a_compras_crear()
                    # fallaba buscando el menú porque no había ninguna
                    # página de Centum cargada todavía.
                    print(f"Pasando a la próxima factura ({pdf_path.name})...")
                    pagina_actual = context.new_page()
                    pagina_actual.goto(credenciales.url_login)
                    pagina_actual.wait_for_load_state("networkidle")
                    client_actual = CentumBrowserClient(pagina_actual, directorio_debug=carpetas.base / "debug")
                    # Por las dudas la sesión no se haya mantenido entre
                    # pestañas, si sigue mostrando el formulario de login
                    # se vuelve a loguear antes de seguir.
                    try:
                        pagina_actual.get_by_placeholder("Usuario").wait_for(timeout=3000)
                        client_actual.login(credenciales)
                    except Exception:  # noqa: BLE001 - ya está logueado, el campo no aparece
                        pass

                print(f"Procesando {pdf_path.name}...")
                reporte = procesar_factura(
                    pdf_path,
                    client_actual,
                    carpetas,
                    tipo_comprobante=tipo_comprobante or "Factura de Compras",
                    punto_venta=pv,
                    numero=num,
                    es_ultima_del_lote=(i == len(pdfs_a_procesar) - 1),
                )
                print(reporte)
                print(
                    "Dejo el navegador abierto hasta que vos lo cierres (sin límite de "
                    "tiempo). NUNCA guarda solo -- revisalo, guardalo a mano si está OK, "
                    "y cerrá la ventana del navegador cuando termines."
                )
                # Sin timeout: espera indefinidamente a que la persona
                # cierre la pestaña/el navegador antes de seguir con la
                # próxima factura (si hay más de una). Un timeout fijo no
                # tiene sentido acá -- nadie puede revisar y guardar en
                # Centum en 60 segundos siempre.
                try:
                    pagina_actual.wait_for_event("close", timeout=0)
                except Exception:  # noqa: BLE001 - si ya se cerró de otra forma, no importa
                    pass
            return

        print(f"Vigilando {carpetas.pendientes} cada {POLL_SEGUNDOS}s (Ctrl+C para cortar)...")
        while True:
            for pdf_path in sorted(carpetas.pendientes.glob("*.pdf")):
                print(f"Procesando {pdf_path.name}...")
                nueva_pestana = context.new_page()
                client_factura = CentumBrowserClient(nueva_pestana, directorio_debug=carpetas.base / "debug")
                try:
                    reporte = procesar_factura(pdf_path, client_factura, carpetas)
                except PasoCentumError as exc:
                    reporte = f"ERROR procesando {pdf_path.name}: {exc}"

                (carpetas.reportes / f"{pdf_path.stem}.txt").write_text(reporte, encoding="utf-8")
                shutil.move(str(pdf_path), str(carpetas.procesadas / pdf_path.name))
                print(reporte)
                print("-" * 60)

            time.sleep(POLL_SEGUNDOS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--carpeta-base", type=Path, default=Path(r"C:\Campomar"))
    parser.add_argument(
        "--modo",
        choices=[
            "solo-login",
            "solo-navegacion",
            "solo-proveedor",
            "solo-remitos",
            "solo-articulos",
            "solo-eliminar",
            "solo-validar",
            "completo",
        ],
        default="solo-login",
        help="Empezar siempre con 'solo-login' para probar sin tocar datos.",
    )
    parser.add_argument(
        "--proveedor-test",
        help="Slug de proveedor (ej. 'nosso', 'ralux') para --modo solo-proveedor / solo-remitos.",
    )
    parser.add_argument("--tipo-comprobante", default="Factura de Compras")
    parser.add_argument("--fecha-documento", help="DD/MM/AAAA, para --modo solo-remitos.")
    parser.add_argument("--punto-venta", help="Para --modo solo-remitos / solo-validar.")
    parser.add_argument("--numero", help="Para --modo solo-remitos / solo-validar.")
    parser.add_argument(
        "--factura-pdf",
        help=(
            "Ruta al PDF real de la factura, para --modo solo-validar. Si se omite, "
            "usa el único PDF que haya en facturas_pendientes (o avisa si hay más de uno)."
        ),
    )
    parser.add_argument("--headless", action="store_true", help="Correr sin mostrar el navegador.")
    args = parser.parse_args()
    correr_watcher(
        args.carpeta_base,
        args.modo,
        args.headless,
        args.proveedor_test,
        args.tipo_comprobante,
        args.fecha_documento,
        args.punto_venta,
        args.numero,
        args.factura_pdf,
    )


if __name__ == "__main__":
    main()
