# Plan de mejora de los bots (Fortnite 3.6, Temporada 3) — estado a 26/11/2026

Orden acordado: **lo básico primero** (moverse, tormenta, saquear, pelear) y **la construcción al final**.
Cada cambio se mide con el simulador (`sim/bot_sim.py`) antes y después, y se compara con jugadores reales de la
Temporada 6/7 (`ml/models/human_metrics_ch1.json`, 296 repeticiones).

Leyenda: ✅ hecho · 🧪 en prueba · ⏳ pendiente · ❌ descartado

## Resultados en 3.6 (25 bots nivel 7, sin aterrizaje caliente)

`A36-before` = la versión del 25/11 en 3.6 (3 partidas). "Ahora" = 19 partidas con la versión actual o casi
(`A36-gemini-reviewed` 1-4, `A36-storm-loop`, `A36-awareness`, `A36-bots2`); rango entre tandas.

| Métrica | `A36-before` | Ahora | Humanos (Temp. 6/7) |
|---|---|---|---|
| Giros falsos por "precipicio" por bot | 70 | **2–6** | — |
| Tiempo siguiendo una ruta | 68% | **70–71%** | — |
| Tiempo fuera del mapa de navegación | 0,7% | **0,1–0,4%** | — |
| Muertes por tormenta | 2,8% | **0–2%** | 3,6% |
| Muertes por caída | 0% | **0–1,4%** | 2,2% |
| Paredes rotas por bot | 14 | **10–12** | — |
| Tiempo con el pico | 20% | **15–20%** | — |
| Kills por bot | 0,93 | 0,94–0,96 | 0,25 (jugador medio) |
| Tiempo parados | 27% | 27–29% | 20% |
| Rendimiento del servidor | — | 30 Hz, 0 frames > 50 ms | — |

## Fase 0 — Banco de pruebas ✅
- ✅ Compilar `Core.dll` en el PC (VS 2022 Build Tools); carpeta aparte `x64\ReleaseSim` para no chocar con la partida.
- ✅ La app usa el `Core.dll` local (`core_dir` en `server/settings.json`).
- ✅ Simulador `sim/bot_sim.py` (`run`, `compare`, `humans`, `--alongside`), métricas por bot al final de partida.
- ✅ Métricas humanas de repeticiones reales de la Temporada 6/7.

## Fase A — Moverse bien 🟡 (casi)
- ✅ 3.6: detector de precipicios que funciona cuesta arriba (70 giros falsos por bot → 2–6).
- ✅ 3.6: desvío lateral antes de seguir la ruta, rodear paredes de casa (≥ 250 de vida).
- ✅ A1 Edificios en el mapa de navegación (CoreV2 `0ccf003`).
- ❌ A1b Agente de navegación de 44 cm (rompió la navegación, revertido).
- ✅ A2 No caer por precipicios (`5b68c5f`) — mejoró, pero aún 14% de muertes por caída.
- ✅ A3 Apartarse de lado al trabarse en esquinas (`5b68c5f`).
- ✅ A4 Detectar "atrapado" y salir por otro lado (`5b68c5f`).
- ✅ A5 Causa de cada muerte: caída / tormenta / otra (`5b68c5f`).
- ⏳ A6 Rampa para salir de un terreno que bloquea (idea de Titanium).
- ✅ A7 Puertas: abrirlas en abanico (±25°) y abrir la puerta cerrada en vez de picarla.
- ✅ Caídas < 3% en 3.6. Quedan dos casos: aterrizar en lo alto de un acantilado y que la primera ruta lo saque
  por el borde, y romper un tejado alto al aterrizar en él.
- ⏳ Tiempo parado (objetivo < 25%, hoy 27–29%) y paredes rotas (< 5 por bot, hoy 10–12).

## Fase B — Tormenta ✅
- ✅ B1 Margen de rotación según `Rotation`, calibrado con humanos (30–63 s).
- ✅ B2 Dentro de la tormenta, correr en vez de pelear (salvo enemigo a < 15 m).
- ✅ B5 Rescate si sigue atrapado con la tormenta encima y nadie lo ve.
- ⏳ B3 "¿Llego con la vida que me queda?" (opcional).
- ✅ B4 Huir sin arma hacia la zona.
- ✅ B6 Bucle en la tormenta (ir y venir sin acercarse al círculo) cuenta como atrapado y sale hacia el círculo.
- ✅ B7 La causa de la muerte sale del propio juego (una muerte por tormenta ya no se cuenta como caída).

## Fase C — Saquear y armarse 🔧 (iterando con simulaciones)
Antes: 28% del tiempo con el pico (146–208 golpes por bot), 0,03 POIs alcanzados.
- ✅ `771a85e` Recolección en ráfagas (`Harvesting`), radio y rareza por `Looting`, POI cuenta solo el viaje.
- ✅ `5eb19be` Tormenta: ir al círculo solo cuando urge (antes 42% del tiempo). `LootReach`: armado y 90–240 s en su zona → solo lo que está a 15 m y al siguiente POI. Muertes por caída mal contadas (un bot abatido mientras saltaba = "caída"): 16% → 2,8% (humanos 2,2%).
- 🔧 Paredes fuertes (>300 de vida): rodear la primera vez en lugar de romperlas (9,5 roturas por bot, gran parte del 40% quieto).
- Resultados (`trapped-x3` → `lootC3-reach-x3`): POIs alcanzados 0,03 → 0,17, POIs de salida 0,3 → 1,0, kills 0,35 → 0,45, materiales recogidos 188 (humanos 215), cofres 1,9 → 2,3. Pendiente: quieto 40% (humanos 20%), salir de la zona 312 s (humanos 175 s).
- Nota: los bots solo cazan humanos (`Investigate` ignora bots), así que en simulaciones solo-bots las cacerías son 0.

## Fase D — Pelear bien ⏳
- ✅ D1 Distancia según arma (escopeta 2,5–7 m, AR 5–25 m, francotirador 20–80 m) y cambio de arma cada 1,5 s.
- ⏳ D2 Puntería por nivel calibrada (nivel 3 ~18%, 7 ~28%, 10 ~38%).
- ✅ D5 Retirarse con poca vida a un sitio fuera de la vista del tirador y curarse allí (sin construir).
- ✅ D8 Oír disparos (de bots y de jugadores): alerta (reaccionan antes) y a veces ir a mirar. Los disparos de
  jugadores aún no están probados en una partida real.
- ⏳ D3 Que peleen más. D4 Recargar / huir sin munición. D6 Asomarse.
- ⏳ D9 No empezar un botiquín de 10 s si la tormenta o un enemigo llegan antes.

## Fase E — Datos y rendimiento ⏳
- ⏳ E1 Grabar cada partida. ⏳ E2 Medir y optimizar con 50/99 bots.
- ⏳ E4 Caché del mapa de navegación (en 7.40 nunca se guarda; solo rendimiento).

## Fase F — Construcción ⏳
Hoy: el 80% de las piezas son rampas para salir de atascos; ganar altura nunca se activa.
- ⏳ F1 Salir a cielo abierto antes de construir. F2 Altura piso a piso. F3 Suelos y conos, comprobar espacio arriba.
- ⏳ F4 Varias formas de subir. F5 Construir en peleas (pared a cada disparo, rampear, cajas). F6 Ediciones.

**Objetivo acordado (26/11/2026):** construcción sweat pero no excesiva, sin limitarse a cómo se jugaba en la Temporada 3 (90s, ramp push, cajas, ganar altura, alguna edición), escalada por nivel del bot.

| Nivel | Qué hace | Piezas/s | Ediciones |
|---|---|---|---|
| Bajo (Trash/Easy) | Pared o rampa cuando le disparan, alguna caja | 2-3 | No |
| Medio | Ramp rush con paredes, caja para curarse, gana altura si el enemigo está arriba | 3-4 | No |
| Alto (Hard) | 90s para ganar altura, ramp push con paredes y suelos, dispara desde arriba | 5-6 | Alguna simple (ventana o rampa para asomarse) |
| Sweat | Igual que Hard pero más rápido y decidiendo mejor; encadena varios 90s | 6-7 | Sí, más a menudo |

Límites para que no parezca un robot:
- Tiempo de reacción antes de construir (nunca en el mismo frame del disparo), con algo de variación.
- Fallos ocasionales: pieza mal rotada, un 90 que no sale y hay que rehacer.
- No encadena cajas sin fin: si lleva un rato sin sacar nada, sale a pelear o se reposiciona.
- Gasta materiales de verdad y deja de construir con pocos.
- Tope de altura: no subir sin motivo.

Orden: primero que el bot se mueva bien por sus propias piezas sin depender del navmesh (input directo + comprobar en cada paso dónde está), después los 90s. Las piezas nuevas no deben forzar regenerar el navmesh deprisa (eso causaba los tirones del servidor). Cada paso se compara con las métricas de antes para que construir no empeore lo básico (caídas, atascos). Se empieza después de la fase D.

## Fase G — Ajuste fino ⏳
- ⏳ G1 Utility AI. G2 Autoajuste nocturno de rasgos con objetivos humanos (niveles 3 y 9 = jugador medio / top 10%).
- ❌ Usar los modelos de `ml/models` como cerebro (aprendieron reglas, no a los humanos) y el simulador 2D.

## Pendientes generales
- ✅ Los bots viven en este repositorio (`corev2/`: CoreV2 + `corev2.patch`); GitHub publica las releases `bots-N`.
- ⏳ Mapa en vivo de la Temporada 3 (hoy muestra la isla de la Temporada 7).
- ⏳ Mapa en vivo: calibrar la imagen con posiciones reales.
- ⏳ Las partidas solo de bots se cortan a los ~10 minutos.
- ⏳ Controles en la app para materiales / recolección (hoy solo en `settings.json`).
