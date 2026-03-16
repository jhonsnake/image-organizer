# NAS Photo Cleaner — Guía de Setup

Pipeline híbrido para limpiar basura de tu biblioteca de fotos en Synology,
preservando la compatibilidad con Synology Photos.

## Requisitos

- **Python 3.10+**
- **Ollama** con modelo `qwen3-vl:8b` (para la clasificación con IA)
- **RTX 5080** (o cualquier GPU con ≥8GB VRAM para Ollama)
- Acceso al NAS via SMB (drive mapeado) o mount

## Instalación

### 1. Instalar dependencias Python

```bash
pip install -r requirements.txt
```

### 2. Instalar y configurar Ollama

```bash
# Descargar Ollama desde https://ollama.com
# Una vez instalado:
ollama pull qwen3-vl:8b
```

Verifica que funciona:
```bash
ollama run qwen3-vl:8b "hello"
```

### 3. Verificar acceso al NAS

Asegúrate de que tu drive mapeado funciona. Ejemplo en Windows:
```
Z:\photo\PhotoLibrary
```

O en Linux con mount SMB:
```
/mnt/nas/photo/PhotoLibrary
```

## Uso

### Paso 1: Dry Run (solo escanea, NO mueve nada)

```bash
python photo_cleaner.py \
    --source "Z:\photo\PhotoLibrary" \
    --output "Z:\photo\_cleanup"
```

Esto genera un reporte CSV en `Z:\photo\_cleanup\classification_report.csv`
que puedes revisar en Excel antes de ejecutar.

### Paso 2: Revisar el reporte

Abre `classification_report.csv` y revisa:
- Las columnas `category`, `action`, `confidence`
- Filtra por `action = delete` y revisa que todo sea basura real
- Los de `classified_by = vision` con `confidence < 0.7` merecen más atención

### Paso 3: Ejecutar (mover archivos)

```bash
python photo_cleaner.py \
    --source "Z:\photo\PhotoLibrary" \
    --output "Z:\photo\_cleanup" \
    --execute
```

Los archivos se mueven a:
```
Z:\photo\_cleanup\
  ├── trash\          ← Screenshots, memes, duplicados, fotos accidentales
  ├── documents\      ← Recibos, facturas, documentos fotografiados
  ├── review\         ← Fotos que necesitan revisión manual
  └── classification_report.csv
```

### Paso 4: Revisión manual

1. Revisa la carpeta `review\` — son fotos que el pipeline no pudo clasificar con certeza
2. Revisa `trash\` rápidamente — busca falsos positivos
3. Revisa `documents\` — confirma que son documentos reales

### Paso 5: Limpieza final

Una vez satisfecho, puedes eliminar `trash\` manualmente.
**NUNCA se elimina nada automáticamente.**

## Opciones avanzadas

```bash
# Sin IA (solo metadata + hashes + calidad) — mucho más rápido
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" --no-vision

# Detección de duplicados más estricta
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" --hash-threshold 6

# Más tolerante con fotos borrosas
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" --blur-threshold 30

# Usar modelo diferente
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" --model "qwen3-vl:4b"

# Ollama remoto (si corre en otra máquina)
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" \
    --ollama-url "http://192.168.1.100:11434"

# Procesar más imágenes con IA (default: 500)
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" --max-vision 2000

# Debug verbose
python photo_cleaner.py --source "Z:\photo" --output "Z:\_cleanup" -v
```

## Compatibilidad con Synology Photos

**Importante:** Este script NO modifica la base de datos de Synology Photos.
Los archivos movidos a `_cleanup` desaparecerán de Photos después del
próximo re-indexado automático (o puedes forzarlo desde el panel de Synology).

Para evitar que Synology indexe la carpeta `_cleanup`:
1. Ve a **Synology Photos → Settings → Indexación**
2. Excluye la carpeta `_cleanup` de la indexación

O nombra la carpeta de output fuera del espacio compartido de Photos.

## Estructura del pipeline

```
Imagen → ┌─ Stage 1: Metadata ──────────────────────────┐
         │  • Nombre de archivo (WhatsApp, Screenshot)   │
         │  • Dimensiones de pantalla + sin EXIF cámara  │ → ~30% clasificado
         │  • Imágenes muy pequeñas (stickers)           │
         └───────────────────────────────────────────────┘
              ↓ (no clasificado)
         ┌─ Stage 2: Hash deduplicación ─────────────────┐
         │  • pHash con tolerancia configurable          │ → ~10-15% más
         │  • Agrupa ráfagas, mantiene la mejor          │
         └───────────────────────────────────────────────┘
              ↓ (no clasificado)
         ┌─ Stage 3: Análisis de calidad ────────────────┐
         │  • Varianza Laplaciana (blur)                 │ → ~5-10% más
         │  • Brillo extremo (oscura/sobreexpuesta)      │
         └───────────────────────────────────────────────┘
              ↓ (no clasificado)
         ┌─ Stage 4: Qwen3-VL-8B ───────────────────────┐
         │  • Clasificación visual con IA local          │ → resto
         │  • Modo /no_think para velocidad              │
         │  • Solo fotos ambiguas                        │
         └───────────────────────────────────────────────┘
              ↓
         ┌─ Acciones ───────────────────────────────────────┐
         │  • DELETE → mover a /trash                       │
         │  • ARCHIVE → mover a /documents                  │
         │  • REVIEW → mover a /review (revisión manual)    │
         │  • KEEP → no mover                               │
         └──────────────────────────────────────────────────┘
```

## Troubleshooting

**Ollama no responde:**
```bash
# Verificar que está corriendo
curl http://localhost:11434/api/tags

# Reiniciar
ollama serve
```

**Modelo no encontrado:**
```bash
ollama pull qwen3-vl:8b
ollama list  # Verificar que aparece
```

**Errores de permisos en NAS:**
```bash
# Asegúrate de que el usuario tiene permisos de lectura+escritura
# en la carpeta compartida del NAS
```

**Demasiado lento:**
- Usa `--no-vision` para un primer pase rápido (solo metadata + hashes)
- Reduce `--max-vision 100` para limitar el procesamiento con IA
- Asegúrate de que Ollama usa la GPU: `ollama ps` debe mostrar tu RTX 5080
