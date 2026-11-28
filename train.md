# Pipeline de Machine Learning y Simulador 2D — Bots de Fortnite 7.40

Sistema unificado para **grabar partidas**, **importar repeticiones `.replay`**, **simular y visualizar en el Mapa en Vivo**, y **entrenar un cerebro táctico ligero (*Behavioral Cloning*) exportable a C++ puro para `CoreV2`**.

---

## Arquitectura unificada

Todas las fuentes hablan **exactamente el mismo formato `.jsonl`** y comparten el mismo vector de **16 señales tácticas → 7 acciones** definido en [ml/schema.py](ml/schema.py):

```
  [Archivos .replay] ──▶ ml/replay-extractor (.NET 8) ──┐
                                                        ├──▶ .jsonl unificado ──▶ ml/train_bc.py (PyTorch)
  [Partidas PS4 / PC] ──▶ server/start.mjs ─────────────┤                              │
                                                        │                              ▼
  [Mini simulador 2D] ──▶ ml/sim2d.py --generate ───────┘             ┌─ ml/models/bot_brain.json (sim2d)
                                                                      └─ ml/models/CoreAthenaBotBrainWeights.h (CoreV2)
```

### Vector de estado (16 entradas normalizadas)
| Índice | Nombre | Significado |
|---|---|---|
| 0 | `hp_norm` | Salud (`0..1`) |
| 1 | `shield_norm` | Escudo (`0..1`) |
| 2 | `mats_norm` | Materiales (`0..1`, sobre 999) |
| 3 | `has_weapon` | `1` si tiene arma con munición |
| 4 | `has_heals` | `1` si lleva curas/escudos |
| 5 | `enemy_dist_norm` | Distancia al enemigo más cercano (`0..1` hasta 150 m) |
| 6 | `enemy_dz_norm` | Diferencia de altura `(enemy_z - my_z) / 1500` (`-1..1`) |
| 7 | `enemies_near_norm` | Enemigos vivos en 40 m (`0..1`, sobre 5) |
| 8 | `in_storm` | `1` si está dentro de la tormenta |
| 9 | `dist_next_zone` | Distancia firmada al borde de la siguiente zona (`-1..1`) |
| 10 | `storm_time_norm` | Segundos restantes de fase (`0..1`, sobre 120 s) |
| 11 | `storm_phase_norm` | Fase de tormenta (`0..1`, sobre 8) |
| 12 | `recently_hurt` | `1` si recibió daño en los últimos segundos |
| 13 | `weapon_close` | `1` si su arma principal es corta distancia (escopeta/SMG) |
| 14 | `alive_ratio` | Fracción de jugadores vivos (`playersLeft / totalPlayers`) |
| 15 | `loadout_score` | Calidad/completitud del inventario (`0..1`) |

### Acciones tácticas (7 salidas)
`0: Fight` · `1: TakeHeight` · `2: BoxAndHeal` · `3: RotateStorm` · `4: Escape` · `5: Loot` · `6: Harvest`

---

## Guía paso a paso

### 1. Grabación automática de tus partidas reales (PS4 / PC)
El servidor [server/start.mjs](server/start.mjs) guarda automáticamente cualquier partida que reciba telemetría de `CoreV2` en:
```
server/data/matches/match-<fecha>.jsonl
```
*(No requiere configuración; las reproducciones con `record: false` no se vuelven a grabar.)*

### 2. Descargar y extraer repeticiones reales de Capítulo 1 (Temporada 6–10)
El extractor en C#/.NET 8 [ml/replay-extractor/Program.cs](ml/replay-extractor/Program.cs) carga automáticamente `oo2core_5_win64.dll` desde tu build de Fortnite 7.40 (`server/settings.json -> pc_build`) para descomprimir **Oodle LZHLW** (el formato original de Capítulo 1) y filtrar vehículos/aviones/tirolinas (`> 850 uu/s`).

**Paso A — Descargar replays auténticos de Capítulo 1 (Temporada 7 y 6) de `Kuinox/FortniteReplayCollection` (756 disponibles):**
```powershell
python ml/download_kuinox.py --count 200 --out ml/data/replays_ch1
```
*(Puedes subir `--count 756` si quieres bajar la colección completa.)*

**Paso B — Extraer en paralelo a `.jsonl` unificado:**
```powershell
python ml/extract_replays.py --in ml/data/replays_ch1 --out ml/data/replays_ch1_jsonl --workers 6
```

*(Para replays de Capítulo 2+ también sigue disponible el importador Node.js `node ml/replays/import_replays.mjs --dir ml/data/replays`.)*

### 3. Generar partidas rápidas con el Mini Simulador 2D (calibración Temporada 7)
Genera 25 partidas *headless* con las velocidades y tormentas reales de Temporada 7:
```powershell
python ml/sim2d.py --generate 25 --policy expert --out ml/data/sim_matches
```

### 4. Entrenamiento en 2 fases (Pre-entrenar con humanos → Calibrar en Temporada 7)
- **Fase 1 — Aprender instintos tácticos humanos** desde repeticiones reales (`.jsonl` filtrados):
  ```powershell
  python ml/train_bc.py --data ml/data/replays_jsonl/*.jsonl --epochs 25 --out-dir ml/models/pretrained
  ```
- **Fase 2 — Calibrar a velocidades/tiempos de Temporada 7** (afinando los pesos con el simulador 2D y tus partidas reales grabadas en `server/data/matches/`):
  ```powershell
  python ml/train_bc.py --init ml/models/pretrained/bot_brain.json --lr 0.001 --epochs 15 --data server/data/matches/*.jsonl ml/data/sim_matches/*.jsonl
  ```
*(O todo junto en una sola pasada con `python ml/train_bc.py --epochs 25`, que ya aplica 3× más peso a datos humanos/repeticiones.)*

Esto genera:
- `ml/models/bot_brain.json` — usado al instante por el mini simulador 2D.
- `ml/models/CoreAthenaBotBrainWeights.h` — cabecera **C++20 pura sin dependencias** (`< 2 µs` por bot) lista para incluir en `CoreV2`.

### 5. Evaluar el modelo ML frente a bots base (*Head-to-Head*)
Enfrenta 50% bots con el cerebro ML contra 50% bots heurísticos base en 40 partidas:
```powershell
python ml/sim2d.py --eval 40 --bots 50 --model ml/models/bot_brain.json
```

### 6. Ver el modelo ML en directo en el Mapa Táctico del `.exe`
1. Abre `Fortnite Offline Server.exe`, enciende el servidor y abre **Mapa en vivo**.
2. Ejecuta en PowerShell:
```powershell
python ml/sim2d.py --live --bots 50 --policy mixed --model ml/models/bot_brain.json --speed 2.5
```
También puedes reproducir cualquier `.jsonl` grabado (de PS4, `.replay` o simulador):
```powershell
python ml/replay_to_map.py server/data/matches/<archivo>.jsonl --speed 2.0
```

### 7. Generar el Mapa de Calor / *Influence Map* (`50×50`)
Agrega todas las partidas para crear el mapa de densidad de aterrizajes, tráfico y muertes:
```powershell
python ml/build_heatmap.py
```
Salida: `server/data/heatmap-s7.json`.

---

## Cómo integrar `CoreAthenaBotBrainWeights.h` en `CoreV2`
1. Copia `ml/models/CoreAthenaBotBrainWeights.h` a `FortniteGame/Source/FortniteGame/Public/Athena/AI/`.
2. En `FCoreAthenaBotController::Tick()` (cada `0.25–0.5 s`), rellena el array `float Features[16]` en el orden exacto de la tabla superior y llama a:
   ```cpp
   const CoreBotBrain::ETacticalAction Action = CoreBotBrain::Evaluate(Features);
   ```
3. Despacha a los comportamientos ya existentes en `CoreAthenaBotController` (`RangeAttack`, `TakeHeight`, `Bunker` + `Heal`, `Storm`, `EscapeUnarmed`, `Loot`, `Harvest`).
