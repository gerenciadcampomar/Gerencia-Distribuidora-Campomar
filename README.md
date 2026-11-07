# Gerencia-Distribuidora-Campomar

Automatización de conciliación de Compras contra Centum Suite: dado un
proveedor y el PDF/imagen de su factura, compara los artículos ya
suscriptos desde los Remitos de Compra pendientes contra lo realmente
facturado, y deja marcado qué coincide, qué sobra (excedente) y qué falta
(a cargar como Concepto "Mercaderías varias").

## Estado actual

Prototipo de la lógica de negocio, probado con datos de ejemplo:

- `campomar_compras/models.py` — modelo de datos (líneas de remito, líneas
  de factura, resultado de matching, concepto faltante, régimen especial).
- `campomar_compras/code_mapping.py` — mapeo código proveedor ↔ código
  Centum, persistido por proveedor en `campomar_compras/providers/*.json`,
  con sugerencias por similitud cuando todavía no hay un mapeo confirmado.
- `campomar_compras/matcher.py` — compara remito vs. factura y clasifica
  cada línea (OK / cantidad difiere / precio difiere / excedente / faltante).
- `campomar_compras/pdf_extractor.py` — extrae líneas de la factura del PDF
  (texto nativo, con fallback a OCR si es escaneado o es una imagen).
- `campomar_compras/centum_client.py` — **stub**: define las operaciones que
  hacen falta contra la API de Centum, pendiente de credenciales/documentación
  real para implementarse.
- `campomar_compras/validator.py` — validación final: compara el subtotal
  sin impuestos que quedaría cargado contra el neto real del PDF; sólo si
  coincide se indica que se puede guardar la compra.
- `campomar_compras/cli.py` — corre el flujo completo en **modo manual**.
- `campomar_compras/provider_parsers.py` — parsers calibrados con facturas
  reales de **Nosso** (código en columna aparte, formato numérico EE.UU.,
  bloque de totales por Alícuota/Gravado) y **Ralux** (código pegado a la
  descripción, formato europeo, columna de descuento, percepción de IIBB
  por jurisdicción). Cada proveedor nuevo suma su propio parser acá.
- Mapeo de código proveedor → Centum: puede ser una **regla determinística**
  (ej. Ralux: `"RAL" + último segmento tras el guion`, confirmado con
  ejemplos reales) o una **tabla aprendida** (ej. Nossovitch, donde no hay
  una fórmula única y cada código se va confirmando a mano).
- `campomar_compras.models.repartir_segmentacion` — reparte una cantidad
  entre "oficial"/"alternativa" (u otras partes), proporcional o por
  cantidad puntual, para cuando el proveedor factura mitad oficial/mitad
  alternativa (o un reparto puntual acordado).
- `campomar_compras/provider_parsers.py` (ByC) — decodifica el reporte de
  Pedido de un proveedor 100% "alternativo": el campo Artículo trae pegados
  código con cero a la izquierda + variante (2 dígitos) + código sin cero.
  La variante **no** es packaging por cantidad — es un atributo físico del
  artículo (ej. espesor de pared del buje: a mayor espesor, menor diámetro
  interno), así que dos líneas con el mismo código base y variante distinta
  son artículos distintos. La clave real a mapear contra Centum es
  `(codigo_base, variante)` junto (`PedidoProveedorLine.clave_articulo`),
  probado con las 106 líneas reales del reporte.
- `tests/` — casos con datos reales de Nossovitch, Ralux y ByC.
- `campomar_compras/centum_browser_client.py` + `campomar_compras/watcher.py`
  — automatización por navegador (Playwright) mientras no hay API: vigila
  una carpeta, y por cada PDF nuevo hace login en Centum, crea la compra,
  suscribe remitos, compara, destilda excedentes y carga Conceptos. **Nunca
  hace click en Guardar/Cerrar** — eso lo hace siempre una persona. Ver
  "Automatización por navegador" más abajo.
- `campomar_compras/provider_identificacion.py` — identifica el proveedor
  de una factura por su CUIT (`providers/cuits.json`) y extrae la fecha del
  comprobante, para elegir los remitos pendientes más cercanos a esa fecha.

## Pendiente / a definir con más ejemplos

- **Notas de Crédito: fuera de alcance a propósito.** La de Exintrader
  analizada es una Nota de Crédito A (cantidades negativas), pero
  normalmente no son por artículos sino por descuentos — esas siempre se
  cargan a mano en Centum, no forman parte de este flujo automatizado.
  (`TipoComprobante.NOTA_CREDITO` queda en el modelo sólo para poder
  distinguir el tipo de documento si hiciera falta más adelante.)
- **Descripciones que se cortan en dos líneas** (pasa en algunas facturas de
  Ralux): hoy esa línea se pierde silenciosamente en vez de reconstruirse.
- **Mapeo de código para ByC**: no hay una fórmula conocida (como sí la hay
  para Ralux) entre `(codigo_base, variante)` y el código real de Centum —
  hace falta ir confirmando mapeos a mano como con Nossovitch.
- **Segmentación oficial/alternativa entre dos compras**: el modelo
  (`repartir_segmentacion`) está listo, pero falta definir el flujo
  concreto de cuándo y cómo se decide el reparto entre la compra "oficial"
  y la "alternativa" para un mismo pedido.
- **Reintento con "Actualizar Precios de Lista de Compra"**: en la pantalla
  de Remitos de Compra Pendientes hay un check "Actualizar Precios de Lista
  de Compra". Los precios del remito de compra en Centum quedan congelados
  desde la orden de compra; si el proveedor aumentó precios entre la orden
  y la entrega, el remito en sistema puede no coincidir con la factura real
  aunque los artículos y cantidades sí coincidan. Flujo a implementar: 1)
  primer intento sin tocar precios; 2) si códigos y cantidades coinciden
  pero el subtotal no cierra porque el sistema está más barato que la
  factura, repetir tildando ese check (trae los precios de lista
  actualizados) y volver a comparar; 3) si ahora cierra, avisar al usuario
  que se tuvo que actualizar precios para que coincida (no debe pasar
  desapercibido). Pendiente hasta calibrar `seleccionar_remitos` /
  `leer_articulos_cargados` / el matching contra el sistema real.

## Uso en modo manual (hoy)

Hasta tener acceso a la API de Centum, el flujo es: vos ya suscribiste los
remitos en Centum a mano (tal como en las capturas), copiás esas líneas a un
JSON simple, y el comando compara contra el PDF real:

```
python -m campomar_compras.cli \
    --proveedor nossovitch \
    --remitos remitos_ejemplo.json \
    --factura factura_nossovitch.pdf
```

Formato de `--remitos`:

```json
[
  {"codigo_articulo": "RERNMI37A859", "descripcion": "Regulador de voltaje",
   "numero_remito": "00001-04062026", "cantidad": 1,
   "precio_unitario": 27762.2, "dto1": 30, "dto2": 5}
]
```

El comando imprime, línea por línea, qué tildar en Centum, qué NO tildar
(excedente), qué cargar como Concepto "Mercaderías varias" (faltante), y al
final un **PASS/FAIL**: sólo si da PASS corresponde entrar a Centum y
guardar la compra. Termina con código de salida 0 (PASS) o 1 (FAIL) para
poder engancharlo más adelante a un proceso automático.

Cuando tengamos acceso a la API, este mismo `matcher`/`validator` se
reutiliza: sólo cambia de dónde salen las líneas de remito (en vez de leer
el JSON manual, las trae `CentumClient` directo del sistema) y quién aprieta
"guardar" (inicialmente vos, después el propio script).

## Automatización por navegador (mientras no hay API)

Este es el camino que corre **en tu computadora Windows**, no en esta sesión
en la nube (no tiene red hacia tu Centum). Pasos para instalarlo:

```powershell
# 1) Crear y activar un entorno virtual (en la carpeta del proyecto)
python -m venv .venv
.venv\Scripts\activate

# 2) Instalar dependencias, incluido el navegador que usa Playwright
pip install -r requirements.txt
playwright install chromium

# 3) Definir las credenciales como variables de entorno (no van en el código)
$env:CENTUM_URL = "https://plataforma5.centum.com.ar:22480/CentumSuite/BL1/Interfaces/Login.aspx"
$env:CENTUM_USUARIO = "tu_usuario"
$env:CENTUM_CONTRASENA = "tu_contraseña"
# Opcional: Centum entra igual sin elegir nada en ese combo.
$env:CENTUM_SUCURSAL = "Distribuidora Campomar"
```

Estructura de carpetas que arma solo (por defecto en `C:\Campomar`, configurable con `--carpeta-base`):

```
C:\Campomar\
  facturas_pendientes\    <- soltás el PDF nuevo acá
  facturas_procesadas\    <- el script lo mueve acá solo, ya analizado
  reportes\                <- qué tildar, qué sacar, PASS/FAIL de cada factura
  debug\                   <- capturas de pantalla si algún paso falla
```

**Primera prueba, sin tocar ningún dato real** (solo confirma que el login
y la navegación funcionan):

```powershell
python -m campomar_compras.watcher --modo solo-login
```

Recién después de confirmar que eso anda, el modo completo (vigila la
carpeta sin parar, hasta que lo cortes con Ctrl+C):

```powershell
python -m campomar_compras.watcher --modo completo
```

Por cada PDF: identifica el proveedor, entra a Centum, crea la compra,
suscribe los remitos más cercanos a la fecha de la factura, compara línea
por línea, destilda lo que sobra, carga los Conceptos de lo que falta, dejá
el reporte en `reportes\` y la compra **abierta en una pestaña nueva, sin
guardar** — revisás el reporte y entrás vos a guardarla a mano.

**Importante:** los selectores (qué botón/campo busca en cada pantalla)
están armados en base a capturas de pantalla, no al HTML real de Centum —
es esperable que haya que ajustar alguno (`campomar_compras/centum_browser_client.py`,
clase `CentumTextos`) la primera vez que se corra contra el sistema real.
Cada paso que falla guarda una captura en `C:\Campomar\debug\` para
diagnosticarlo rápido.

También faltan cargar a mano, por proveedor, dos tablas pequeñas antes de
usar un proveedor nuevo:
- `campomar_compras/providers/nombres_centum.json`: cómo buscarlo en el
  campo Proveedor de Centum.
- `campomar_compras/providers/cuits.json`: su CUIT, para identificarlo
  automáticamente desde el PDF.

## Lo que falta para que corra contra el sistema real

1. **Acceso a la API de Centum**: URL base, forma de autenticación (API key,
   usuario técnico, OAuth) y los endpoints concretos de Compras (buscar
   remitos pendientes de un proveedor, crear compra, suscribir remitos,
   cargar concepto, cargar régimen especial, guardar). Sin esto,
   `centum_client.py` sigue siendo un stub.
2. **PDFs/imágenes reales de cada proveedor** para ajustar
   `pdf_extractor.py` (cada proveedor tiene su propio layout de columnas).
3. **Mapeos de código confirmados** por proveedor, que se van cargando en
   `campomar_compras/providers/<proveedor>.json` a medida que se procesan
   facturas (algunos ya cargados a mano como ejemplo: Nossovitch).

## Cómo correr los tests

```
pip install -r requirements.txt
python -m pytest tests/ -q
```
