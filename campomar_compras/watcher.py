"""Vigila `C:\\Campomar\\facturas_pendientes` (configurable) y, por cada PDF
nuevo, prepara automáticamente la compra en Centum hasta el punto previo a
Guardar. NUNCA hace click en Guardar/Cerrar -- eso lo hace siempre una
persona, mirando la pantalla real.

Uso (Windows, PowerShell):

    $env:CENTUM_URL = "https://plataforma5.centum.com.ar:22480/CentumSuite/BL1/Interfaces/Login.aspx"
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
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from campomar_compras.centum_browser_client import CentumBrowserClient, CentumCredenciales, PasoCentumError
from campomar_compras.code_mapping import CodeMappingStore
from campomar_compras.matcher import match_remitos_contra_factura, proponer_conceptos_faltantes
from campomar_compras.provider_identificacion import (
    extraer_fecha_factura,
    identificar_proveedor,
    nombre_centum,
)
from campomar_compras.provider_parsers import PROVIDER_PARSERS, regla_codigo_ralux
from campomar_compras.validator import validar_subtotal

REGLAS_CODIGO = {"ralux": regla_codigo_ralux}

POLL_SEGUNDOS = 10


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


def procesar_factura(pdf_path: Path, client: CentumBrowserClient, carpetas: Carpetas) -> str:
    """Devuelve el texto del reporte generado para esta factura."""
    slug = identificar_proveedor(pdf_path)
    if slug is None or slug not in PROVIDER_PARSERS:
        return (
            f"NO PROCESADO: no pude identificar el proveedor de '{pdf_path.name}' "
            f"(CUIT no encontrado en campomar_compras/providers/cuits.json, o no "
            f"hay parser para ese proveedor todavía). Revisar a mano."
        )

    nombre_buscar = nombre_centum(slug)
    if nombre_buscar is None:
        return (
            f"NO PROCESADO: proveedor '{slug}' identificado, pero falta su nombre "
            f"de búsqueda en campomar_compras/providers/nombres_centum.json."
        )

    parse_lineas, parse_totales = PROVIDER_PARSERS[slug]
    lineas_factura = parse_lineas(pdf_path)
    totales_factura = parse_totales(pdf_path)
    fecha_factura = extraer_fecha_factura(pdf_path)
    mapping = CodeMappingStore(slug, regla=REGLAS_CODIGO.get(slug))

    client.ir_a_compras_crear()
    client.elegir_proveedor(nombre_buscar)
    client.abrir_remitos_pendientes()
    remitos_pendientes = client.listar_remitos_pendientes()
    numeros_elegidos = _elegir_remitos_mas_cercanos(remitos_pendientes, fecha_factura)
    client.seleccionar_remitos(numeros_elegidos)

    remitos_cargados = client.leer_articulos_cargados()
    resultados = match_remitos_contra_factura(remitos_cargados, lineas_factura, mapping)

    for r in resultados:
        if r.status.value == "excedente":
            client.destildar_articulo(r.remito_line.numero_remito, r.remito_line.codigo_articulo)

    for concepto in proponer_conceptos_faltantes(resultados):
        client.agregar_concepto(concepto)

    validacion = validar_subtotal(resultados, totales_factura)

    lineas_reporte = [
        f"Factura: {pdf_path.name}",
        f"Proveedor: {slug}",
        f"Remitos elegidos: {', '.join(numeros_elegidos) or '(ninguno)'}",
        "",
    ]
    for r in resultados:
        codigo = r.remito_line.codigo_articulo if r.remito_line else r.invoice_line.codigo_proveedor
        lineas_reporte.append(f"  [{r.status.value}] {codigo}  {r.notes}")
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
) -> None:
    carpetas = Carpetas(carpeta_base)
    carpetas.crear()
    credenciales = _credenciales_desde_entorno()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
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
            if not nombre:
                raise SystemExit(
                    f"No hay nombre de búsqueda para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json, o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(nombre)
            print(f"Elegir proveedor '{nombre}' OK. Dejo el navegador abierto 30s.")
            time.sleep(30)
            return

        if modo == "solo-remitos":
            client.ir_a_compras_crear()
            nombre = nombre_centum(proveedor_test) if proveedor_test else None
            if not nombre:
                raise SystemExit(
                    f"No hay nombre de búsqueda para '{proveedor_test}' en "
                    f"campomar_compras/providers/nombres_centum.json, o no pasaste --proveedor-test."
                )
            client.elegir_proveedor(nombre)
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
        choices=["solo-login", "solo-navegacion", "solo-proveedor", "solo-remitos", "completo"],
        default="solo-login",
        help="Empezar siempre con 'solo-login' para probar sin tocar datos.",
    )
    parser.add_argument(
        "--proveedor-test",
        help="Slug de proveedor (ej. 'nosso', 'ralux') para --modo solo-proveedor / solo-remitos.",
    )
    parser.add_argument("--tipo-comprobante", default="Factura de Compras")
    parser.add_argument("--fecha-documento", help="DD/MM/AAAA, para --modo solo-remitos.")
    parser.add_argument("--punto-venta", help="Para --modo solo-remitos.")
    parser.add_argument("--numero", help="Para --modo solo-remitos.")
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
    )


if __name__ == "__main__":
    main()
