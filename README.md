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
- `tests/` — casos con los ejemplos reales de Nossovitch y Cia S.A.

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
