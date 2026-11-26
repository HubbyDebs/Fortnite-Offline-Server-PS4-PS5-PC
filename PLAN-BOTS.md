# Plan de mejora de los bots (Fortnite 3.6, Temporada 3) — estado a 24/11/2026

Orden acordado: **lo básico primero** (moverse, tormenta, saquear, pelear) y **la construcción al final**.
Cada cambio se mide con el simulador (`sim/bot_sim.py`) antes y después, y se compara con jugadores reales de la
Temporada 6/7 (`ml/models/human_metrics_ch1.json`, 296 repeticiones).

Leyenda: ✅ hecho · 🧪 en prueba · ⏳ pendiente · ❌ descartado

## Resultados hasta ahora (25 bots nivel 7, 3 partidas por versión)

| Métrica | Bots originales | Ahora | Humanos (Temp. 7) |
|---|---|---|---|
| Tiempo siguiendo una ruta | 31% | **62%** | — |
| Tiempo fuera del mapa de navegación | 36% | **1%** | — |
| Muertes por tormenta | ~30–40% | **3,6%** | 3,6% |
| Muertes sin culpable (tormenta + caídas) | ~40% | **7%** | ~6% |
| Disparos por bot | ~5 | **8,4** | — |
| Kills por bot | 0,24 | 0,35 | 0,25 (jugador medio) |
| Caídas (% de muertes) | ~12% | 14% | 2% |
| Tiempo parados | 43% | 39% | 20% |
| Paredes rotas por bot | 11 | 13 | — |
| Piezas construidas por bot | 1,8 | 1,1–1,7 | 4 (medio) / 36 (top 10%) |
| Puntería (aciertos) | — | 40% | 25% |

## Fase 0 — Banco de pruebas ✅
- ✅ Compilar `Core.dll` en el PC (VS 2022 Build Tools); carpeta aparte `x64\ReleaseSim` para no chocar con la partida.
- ✅ La app usa el `Core.dll` local (`core_dir` en `server/settings.json`).
- ✅ Simulador `sim/bot_sim.py` (`run`, `compare`, `humans`, `--alongside`), métricas por bot al final de partida.
- ✅ Métricas humanas de repeticiones reales de la Temporada 6/7.

## Fase A — Moverse bien 🟡 (casi)
- ✅ A1 Edificios en el mapa de navegación (CoreV2 `0ccf003`).
- ❌ A1b Agente de navegación de 44 cm (rompió la navegación, revertido).
- ✅ A2 No caer por precipicios (`5b68c5f`) — mejoró, pero aún 14% de muertes por caída.
- ✅ A3 Apartarse de lado al trabarse en esquinas (`5b68c5f`).
- ✅ A4 Detectar "atrapado" y salir por otro lado (`5b68c5f`).
- ✅ A5 Causa de cada muerte: caída / tormenta / otra (`5b68c5f`).
- ⏳ A6 Rampa para salir de un terreno que bloquea (idea de Titanium).
- ⏳ A7 Puertas y marcos de puerta.
- ⏳ Bajar caídas (objetivo < 3%), tiempo parado (< 25%) y paredes rotas (< 5 por bot).

## Fase B — Tormenta ✅
- ✅ B1 Margen de rotación según `Rotation`, calibrado con humanos (30–63 s).
- ✅ B2 Dentro de la tormenta, correr en vez de pelear (salvo enemigo a < 15 m).
- ✅ B5 Rescate si sigue atrapado con la tormenta encima y nadie lo ve.
- ⏳ B3 "¿Llego con la vida que me queda?" (opcional).
- ⏳ B4 Huir sin arma hacia la zona (MEJORAS 1.4, opcional).

## Fase C — Saquear y armarse 🔧 (iterando con simulaciones)
Antes: 28% del tiempo con el pico (146–208 golpes por bot), 0,03 POIs alcanzados.
- ✅ `771a85e` Recolección en ráfagas (`Harvesting`), radio y rareza por `Looting`, POI cuenta solo el viaje.
- ✅ `5eb19be` Tormenta: ir al círculo solo cuando urge (antes 42% del tiempo). `LootReach`: armado y 90–240 s en su zona → solo lo que está a 15 m y al siguiente POI. Muertes por caída mal contadas (un bot abatido mientras saltaba = "caída"): 16% → 2,8% (humanos 2,2%).
- 🔧 Paredes fuertes (>300 de vida): rodear la primera vez en lugar de romperlas (9,5 roturas por bot, gran parte del 40% quieto).
- Resultados (`trapped-x3` → `lootC3-reach-x3`): POIs alcanzados 0,03 → 0,17, POIs de salida 0,3 → 1,0, kills 0,35 → 0,45, materiales recogidos 188 (humanos 215), cofres 1,9 → 2,3. Pendiente: quieto 40% (humanos 20%), salir de la zona 312 s (humanos 175 s).
- Nota: los bots solo cazan humanos (`Investigate` ignora bots), así que en simulaciones solo-bots las cacerías son 0.

## Fase D — Pelear bien ⏳
- ⏳ D1 Distancia según arma y cambio de arma (hoy disparan más la escopeta a media distancia que de cerca).
- ⏳ D2 Puntería por nivel calibrada (nivel 3 ~18%, 7 ~28%, 10 ~38%).
- ⏳ D3 Que peleen más. D4 Recargar / huir sin munición. D5 Curarse a cubierto. D6 Asomarse.
- ⏳ D7 Arreglos pequeños (strafe saturado, contadores que no se reinician).

## Fase E — Datos y rendimiento ⏳
- ⏳ E1 Grabar cada partida. ⏳ E2 Medir y optimizar con 50/99 bots.
- ⏳ E4 Caché del mapa de navegación (en 7.40 nunca se guarda; solo rendimiento).

## Fase F — Construcción ⏳
Hoy: el 80% de las piezas son rampas para salir de atascos; ganar altura nunca se activa.
- ⏳ F1 Salir a cielo abierto antes de construir. F2 Altura piso a piso. F3 Suelos y conos, comprobar espacio arriba.
- ⏳ F4 Varias formas de subir. F5 Construir en peleas (pared a cada disparo, rampear, cajas). F6 Ediciones.

## Fase G — Ajuste fino ⏳
- ⏳ G1 Utility AI. G2 Autoajuste nocturno de rasgos con objetivos humanos (niveles 3 y 9 = jugador medio / top 10%).
- ❌ Usar los modelos de `ml/models` como cerebro (aprendieron reglas, no a los humanos) y el simulador 2D.

## Pendientes generales
- ⏳ **Push** de 8 commits de CoreV2 (`git -C C:\Users\jj\CoreV2 push origin HEAD:main`).
- ⏳ Mapa en vivo: calibrar la imagen con posiciones reales.
- ⏳ Las partidas solo de bots se cortan a los ~10 minutos.
- ⏳ Controles en la app para materiales / recolección (hoy solo en `settings.json`).
