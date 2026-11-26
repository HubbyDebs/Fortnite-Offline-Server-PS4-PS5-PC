# Auditoría y Plan de Mejoras — Bots de CoreV2 (`bot-traits`)

Documento técnico para guiar las siguientes iteraciones sobre los bots en `CoreV2` (rama `bot-traits`, commits revisados: `5633ffb`, `91df266`, `ce6cd24`).  
Cada punto incluye archivo, líneas aproximadas, causa raíz, solución propuesta y criterio de verificación.

---

## Sprint 1 (Prioridad Alta) — Bugs detectados en `bot-traits`

### 1.1 `TakeHeight()` construye todos los pisos de golpe y congela al bot abajo
- **Archivo:** `FortniteGame/Source/FortniteGame/Private/Athena/AI/CoreAthenaBotController.cpp` (~L7256–L7308, `TakeHeight()`)
- **Problema:**
  - Encola hasta `2 * Floors` (6 piezas: pared + rampa para pisos 0, 1 y 2) de una sola vez en `BuildQueue` y devuelve `false`.
  - Durante los siguientes `1.5–3.0 s`, `TickBuildingQueue()` mantiene al bot quieto sin disparar mientras aparecen rampas 2–3 pisos arriba.
  - Además, al nacer la Rampa 0 en la misma celda `(0,0,0)` donde está parado el bot, con frecuencia queda atrapado **debajo** de su propia rampa y choca contra su `WallForward` hasta agotar `HeightAscendUntil` (`Unfinished`).
- **Solución:**
  - Construir **1 piso a la vez** (`Floor 0`: rampa + pared frontal) y caminar hacia arriba inmediatamente; al alcanzar el borde superior, decidir si continuar al siguiente piso (hasta `TakeHeightMaxSteps`) o empezar a disparar.
  - Desplazar la celda inicial o empujar al bot ligeramente hacia atrás/arriba al colocar la Rampa 0 para no quedar atrapado bajo ella.

### 1.2 `HeightTopPoint` pasa `+60 cm` del borde superior de la rampa (riesgo de caída)
- **Archivo:** `CoreAthenaBotController.cpp` (~L7300–L7302)
- **Problema:** `BuildingCellSize * (Ramps - 0.5f)` ya es el borde delantero de la última rampa; sumar `+ 60.0f` coloca el punto destino en el aire, y `PlayerBuildingClass` aún no coloca un suelo (`Floor`) arriba.
- **Solución:**
  - Detener el destino antes del borde (`- 80.0f` en lugar de `+ 60.0f`), **o** añadir pieza de suelo (`Floor`) en `(Ramps, 0, Ramps)` para rematar la plataforma superior con seguridad.

### 1.3 El chequeo de techo (`headroom`) solo comprueba la vertical del bot
- **Archivo:** `CoreAthenaBotController.cpp` (~L7243–L7249)
- **Problema:** `LineTraceSingle` traza en vertical sobre `(Location.X, Location.Y)`, sin comprobar las celdas diagonales `(1,0,1)` y `(2,0,2)` por donde suben las rampas.
- **Solución:** trazar un rayo diagonal desde `(Location.X, Location.Y, Location.Z + 80)` hacia la cima estimada `(TopX, TopY, TopZ + 80)` antes de iniciar la escalada.

### 1.4 `EscapeUnarmed()` se cancela en cuanto se pierde línea de visión (y puede correr hacia la tormenta)
- **Archivo:** `CoreAthenaBotController.cpp` (~L4444–L4483, `RangeAttack()`)
- **Problema:**
  - El bloque `if (!bArmed && AIBotSkills.bEscapesUnarmed ...)` está **después** de `if (!bHasVisualLineOfSightOnTarget)`. En cuanto el bot sin arma se gira y pasa tras una pared o árbol, pierde LoS, retorna `false` en L4453 y aborta la huida aunque queden 4 segundos de `EscapeUntil`.
  - El vector de huida `Direction2D(EnemyLocation, Location) * 2000.0f` no comprueba si lleva al bot fuera de la zona segura.
- **Solución:**
  - Mover el chequeo de `EscapeUnarmed` **antes** del bloque `!bHasVisualLineOfSightOnTarget` (y mantenerlo activo mientras `Now < EscapeUntil` y `!bArmed`).
  - Si hay zona segura activa (`GetSafeZone`), sesgar la dirección de huida hacia el centro del círculo cuando correr recto aleje al bot de la zona segura.

### 1.5 `Strafing` se satura a partir de `72/100`
- **Archivo:** `FortniteGame/Source/FortniteGame/Private/Athena/AI/CoreAthenaBotManager.cpp` (~L1063, `SkillsForTraits()`)
- **Problema:** `std::min(FMath::Lerp(0.35f, 1.25f, T.Strafing), 1.0f)` alcanza `1.0f` en `T.Strafing ≈ 0.722`; los valores `73–100` no cambian la fuerza lateral.
- **Solución:** mapear limpiamente `FMath::Lerp(0.35f, 1.0f, T.Strafing)` en todo el rango `[0, 1]` (y usar `T.Strafing` también para acelerar el suavizado `Step` de cambio de dirección en `RangeAttack`).

### 1.6 Contadores estáticos de `TakeHeight` / `EscapeUnarmed` no se reinician entre partidas
- **Archivo:** `CoreAthenaBotController.cpp` (~L4394–L4395, L7311, L7325, L7345)
- **Problema:** `TakeHeightStats`, `NextTakeHeightLog` y los `static int32_t Logged` no se limpian en `ResetForNewMatch()`. En servidores con múltiples partidas seguidas, las estadísticas quedan acumuladas/rotas.
- **Solución:** resetear `TakeHeightStats`, `NextTakeHeightLog = 120.0f` y convertir los `Logged` en variables de namespace reseteadas en `ResetForNewMatch()`.

---

## Sprint 2 (Prioridad Alta) — Cuellos de botella de rendimiento (50–99 bots)

### 2.1 Eliminar el `O(N³)` oculto en `UpdateCurrentTarget()` por `FindBot()` lineal
- **Archivos:**
  - `CoreAthenaBotController.cpp` (~L3038–L3120, `UpdateCurrentTarget()`)
  - `CoreAthenaBotManager.cpp` (~L1156–L1171 `FindBot()`, ~L3813 `RefreshEnemies()`)
- **Causa raíz:**
  - Cada `0.25 s`, cada bot recorre `EnemyPawns` (`N`) y llama a `FindBot(Other)`, que hace otro bucle lineal sobre `PlayerBots` (`N`) resolviendo 3 `TWeakObjectPtr` por iteración.
  - Además, `LocationOf(Other)` invoca `GetTransform` por reflexión UE4 repetidamente para el mismo pawn desde múltiples bots, y `AttackerPawn` vuelve a recorrer `EnemyPawns`.
  - Con 99 bots: ~970.000 resoluciones de WeakPtr cada `0.25 s`.
- **Solución:**
  1. Mantener un `std::unordered_map<const UObject*, FCoreAthenaBotController*> BotByObject` actualizado en spawn/posesión/muerte/destrucción para que `FindBot()` sea `O(1)`.
  2. Cachear en `RefreshEnemies()` (una sola vez por frame) un `std::vector<FEnemySnapshot>` con `{ Pawn, PlayerState, Location, Team, BotPtr, bIsDBNO, bIsDying }`.
  3. Usar `DistSquared` antes de `sqrt` y leer `Location` y `BotPtr` del snapshot sin volver a llamar `GetTransform` ni `FindBot`.

### 2.2 Presupuesto por frame para `LineTraceSingle` y `ProjectPointToNavigation`
- **Archivo:** `CoreAthenaBotController.cpp` (~L5751–L5768 `RequestPath()`, ~L5895–L5953 `SteerAroundObstacles()`)
- **Causa raíz:**
  - `SteerAroundObstacles()` puede disparar hasta **42 `LineTraceSingle` síncronos en un solo frame por bot** (`5 ángulos × 2 lados × 4 rayos + 2`).
  - `RequestPath()` puede disparar **15 `ProjectPointToNavigation` seguidos** en un mismo frame.
- **Solución:**
  - Añadir un presupuesto global por tick en `FCoreAthenaBotManager` (ej. máx. 3–4 `RequestPath` y 64 traces de obstáculo por frame); si se agota, diferir al siguiente tick escalonado por `BotIndex`.
  - En `SteerAroundObstacles::Room()`, probar primero el rayo central a altura de rodilla + cabeza (2 traces) y lanzar los 2 laterales solo si los centrales pasan libres.

### 2.3 Rejilla espacial 2D (*Spatial Hash Grid*) para `Loot` y `Containers`
- **Archivo:** `CoreAthenaBotManager.cpp` (~L3928 `RefreshLoot()`, ~L2839 `GatherContainers()`)
- **Causa raíz:** todos los bots recorren linealmente listas globales con cientos/miles de pickups y cofres (`O(N_bots × N_items)`).
- **Solución:** indexar `Loot` y `Containers` en una rejilla 2D de celdas de `5000 uu` (50 m) sobre el mapa de Athena (`~54×54` celdas). Las búsquedas en radio `≤ 50 m` solo inspeccionan las `3×3` celdas vecinas.

### 2.4 AI LOD por distancia al jugador humano más cercano
- **Archivo:** `CoreAthenaBotManager.cpp` (~L1550–L1574, `Tick()`)
- **Causa raíz:** bots a >1.000 m de cualquier jugador humano ejecutan el `Tick()` completo cada frame (30 Hz).
- **Solución:** calcular la distancia mínima al jugador humano vivo más cercano en `RefreshEnemies()`. Para bots a `> 250 m` de cualquier humano y fuera de combate activo, ejecutar su `Tick()` a `10 Hz` (cada 3 frames, pasando `DeltaSeconds` acumulado). Reduce ~50–60% la CPU de IA en early/mid game.

### 2.5 Evitar `LoadSynchronous()` en el Game Thread durante la partida
- **Archivo:** `CoreAthenaBotController.cpp` (~L4995 `PlayEmote()`) y `CoreAthenaBotManager.cpp` (~L3952 `RefreshLoot()`)
- **Problema:** llamadas síncronas `LoadSynchronous()` en emotes y armas nuevas causan micro-tirones de disco en medio de la partida.
- **Solución:** asegurar que los montajes de emotes queden incluidos en `PreloadBotAssets()` y evitar `LoadSynchronous()` cuando el asset no esté ya cargado en memoria (o diferir su uso hasta que cargue por `RequestAsyncLoad`).

---

## Sprint 3 (Prioridad Media) — Conectar traits dormidos y mejorar combate

### 3.1 Conectar los 3 traits ya definidos pero sin uso (`Harvesting`, `Looting`, `Rotation`)
Actualmente `SkillsForNewBot()` modifica estos traits para `looter`, `builder` y `camper`, pero ningún código los consulta (por eso el `builder` intenta construir más pero se queda sin materiales: `SkipMaterials`).

1. **`Harvesting` (`T.Harvesting`):**
   - Escalar el objetivo de materiales deseados antes de priorizar combate/caza (ej. `Lerp(100, 600, T.Harvesting)`) y el radio de búsqueda de árboles/piedras/muebles en `Harvest()`.
   - Hace que el arquetipo `builder` farmee antes de pelear y pueda completar sus `TakeHeight()` y `Bunker()`.
2. **`Looting` (`T.Looting`):**
   - Escalar la exigencia de loadout antes de pasar a `Investigate()`/`Hunt`:
     - `Looting` bajo (`rusher`): caza en cuanto tiene 1 arma con munición.
     - `Looting` alto (`looter`): prioriza conseguir al menos 2 armas de distintos rangos (AR + Escopeta/SMG) y escudo/curas antes de buscar pelea.
3. **`Rotation` (`T.Rotation`):**
   - En `ShouldHeadForSafeZone()`, sumar un margen de anticipación `Lerp(0.0f, 30.0f, T.Rotation)` segundos al tiempo necesario para llegar al siguiente círculo.
   - Los bots con `Rotation` alto rotan antes de que cierre la tormenta en vez de correr pegados al borde y morir por tormenta.

### 3.2 Espaciado de combate adaptado al arma equipada
- **Archivo:** `CoreAthenaBotController.cpp` (~L4552–L4565, `RangeAttack()`)
- **Problema:** actualmente retrocede siempre cuando `Distance < 600.0f` (6 m), incluso llevando escopeta, y trata igual todas las armas entre 6 m y 25 m.
- **Solución:** definir rango óptimo `(MinDist, MaxDist)` según `Weapon.Type`:
  - **Shotgun:** `300–850 uu` (3–8.5 m) → empujar agresivamente + mayor probabilidad de salto (`Jumping`).
  - **SMG / Pistol:** `600–1400 uu` (6–14 m) → strafe rápido.
  - **AssaultRifle:** `1500–3800 uu` (15–38 m) → ADS + strafe moderado.
  - **Sniper:** `3500–8500 uu` (35–85 m) → mantenerse quieto/ADS al apuntar y cambiar a AR/SMG si el enemigo baja de 20 m.

### 3.3 Curación táctica en combate (`Healing` + `Bunker`)
- **Archivo:** `CoreAthenaBotController.cpp` (`Heal()`, `Bunker()`, `LootHealing()`)
- **Problema:** `T.Healing` solo ajusta `LowHealthShield` (cuándo buscar curas en el suelo), pero no cuándo cubrirse y curarse durante una pelea.
- **Solución:** si `Health + Shield` cae por debajo del umbral escalado por `T.Healing` (ej. `Lerp(55, 135, T.Healing)`), tiene consumibles utilizables y tiene materiales, disparar `Bunker()` y acto seguido usar el consumible en lugar de seguir disparando al descubierto.

### 3.4 Pieza de suelo (`Floor`) + Peeking y ediciones básicas (`EditFrequency`, `EditSpeed`)
- Añadir `Floor` a `ECoreOrientedConstructionBuildingType` y `PlayerBuildingClass` para dar soporte a plataformas de altura y 1x1 completos.
- Permitir que un bot encerrado en su propio `Bunker` edite una ventana o puerta en su pared (con retraso gobernado por `EditSpeed` y probabilidad por `EditFrequency`) en lugar de esperar pasivamente o romper su propia pared a pico.

---

## Sprint 4 (Prioridad Media-Baja / Evolución) — IA Híbrida, ML ligero y Simulador de entrenamiento

> **Principio de arquitectura:** mantener el control fino (apuntar, caminar, colocar piezas en UE4) en C++ determinista y aplicar ML/optimización **solo en la capa táctica y de calibración**, evitando redes pesadas en el Game Thread o Deep RL *end-to-end* lento sobre UE4.

### 4.1 Grabación de partidas y datasets desde telemetría (Base de datos real)
- **Dónde:** `server/start.mjs` (endpoint `/fortnite/api/game/v2/matchmakingservice/telemetry`) + `CoreV2` (`StartMatchTelemetry`).
- **Qué hacer:**
  - Guardar cada partida en `server/data/matches/<matchId>.jsonl` (muestras a `2 Hz` + resumen final de kills, causa de muerte, supervivencia y arquetipo por bot/jugador).
  - Separar claramente acciones de jugadores humanos (PS4/PC) vs bots para usarlo como dataset de referencia.

### 4.2 Auto-calibración evolutiva de los 20 traits (CMA-ES / Algoritmos Genéticos)
- **Problema:** los rangos en `SkillsForTraits()` y los 5 arquetipos (`looter`, `rusher`, `builder`, `camper`, `balanced`) están ajustados a mano.
- **Solución:**
  - Script en Python (CMA-ES / Optuna / evolución) que lance tandas automáticas de `-CoreBotMatch`, lea el JSONL/resumen de partida y optimice los pesos de cada arquetipo contra objetivos medibles (baja tasa de muertes por tormenta, alta tasa de `TakeHeight` exitosas, distribución realista de combates y duración de partida).
  - **Coste en `Core.dll`: 0 ms extra**, ya que solo calibra los valores numéricos de `FCoreBotTraits` / `SkillsForTraits()`.

### 4.3 Mapas de calor / *Influence Maps* de la isla Temporada 7
- **Dónde:** `server/start.mjs` (cálculo offline/incremental) → consumido por `CoreAthenaBotManager` en el inicio de partida.
- **Solución:**
  - Generar una matriz 2D `50×50` (celdas de `50 m`) con densidad histórica de muertes, combates y rutas de rotación por fase de tormenta.
  - Consultar la matriz en `O(1)` al elegir aterrizaje y rotaciones:
    - `looter` / `camper`: penalizan celdas calientes y evitan cruces con alto tráfico.
    - `rusher`: priorizan celdas calientes y buscan *third-party*.

### 4.4 Utility AI + Behavioral Cloning (Imitation Learning de jugadores reales)
- **Dónde:** capa de decisión táctica en `FCoreAthenaBotController::Tick()` (sustituyendo la cascada rígida `if/else` de alto nivel).
- **Solución:**
  - Extraer un vector compacto de ~16 señales: vida, escudo, materiales, arma equipada, munición, distancia al enemigo, `ΔZ` (ventaja/desventaja de altura), nº de enemigos en rango, daño reciente, distancia al borde de zona, tiempo restante de tormenta, disponibilidad de curas.
  - Elegir entre ~7 acciones tácticas: `Fight`, `TakeHeight`, `BoxAndHeal`, `RotateStorm`, `Escape`, `Loot`, `Harvest`.
  - Entrenar en Python (sobre las partidas grabadas del usuario en PS4/PC) un modelo diminuto (**árbol GBDT/Random Forest** o **MLP `16 → 32 → 7`**) y exportarlo como código C++ puro sin dependencias ni GPU (`< 2 µs` por bot por evaluación).

### 4.5 Simulador táctico 2D *headless* de alta velocidad (Macro-estrategia)
- **Propósito:** evolucionar el simulador 2D de demo hacia un entorno rápido en Python/C++ (10.000x velocidad real) con los POIs reales de Temporada 7, tiempos de tormenta, tablas de loot, curación y modelos de duelo calibrados con partidas reales de `CoreV2`.
- **Uso:** entrenar con **PPO / Evolución** las decisiones de **macro-estrategia** (elección de salto desde el bus, tiempo óptimo de looteo, rotaciones anticipadas y posicionamiento en círculos finales) y exportar las políticas/pesos resultantes a `CoreV2`.

---

## Criterios de validación (Banco de pruebas)

Antes y después de cada sprint, ejecutar `-CoreBotMatch` con **50 bots** y **99 bots** (`mixed: true`) y comparar métricas en el log (`LogSummary` + `LogTakeHeightStats`) y en el Mapa Táctico en Vivo:

| Métrica | Objetivo |
|---|---|
| `BotsMs` promedio por frame (50 bots) | `< 3.0 ms` |
| `BotsMsMax` p99 / picos > 33 ms | Sin picos atribuibles a percepción o `SteerAroundObstacles` |
| Ratio `TakeHeight` (`Reached / Started`) | `> 70%` |
| `SkipMaterials` en arquetipo `builder` | Reducción significativa tras conectar `Harvesting` |
| Muertes por tormenta (`Storm deaths / total`) | `< 15%` tras conectar `Rotation` |
| Diferenciación real entre arquetipos | `rusher` con más kills tempranas; `looter`/`camper` con mayor supervivencia media |
