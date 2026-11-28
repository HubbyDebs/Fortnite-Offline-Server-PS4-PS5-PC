# Procedimiento para mejorar los bots

Cómo se mejoran ("entrenan") los bots de este servidor. No hay aprendizaje automático: es un ciclo de
**medir → diagnosticar → cambiar → simular → comparar con humanos → quedarse o deshacer**. Cada cambio se
mide en partidas simuladas antes de llegar al servidor con el que se juega.

Objetivo: que los bots se comporten **lo más parecido a un jugador humano de la Temporada 3**, sin trampas
(nada de materiales o munición regalados, nada de puntería perfecta).

---

## 1. Piezas

| Pieza | Dónde | Qué es |
|---|---|---|
| Código de los bots | `C:\Users\jj\CoreV2\FortniteGame\Source\FortniteGame\Private\Athena\AI\` | `CoreAthenaBotController.cpp` (cada bot: moverse, saquear, pelear, tormenta) y `CoreAthenaBotManager.cpp` (niveles, rasgos, ajustes) |
| Helpers puros con test | `CoreAthenaBotAwareness.h` + `sim/tests/bot_logic_test.cpp` | Lógica sin motor (puntería según la situación, retirada, oído) |
| Simulador | `sim/bot_sim.py` | Partidas solo de bots en servidores de prueba, en paralelo |
| Estadísticas rápidas | `sim/runstats.py` | Aciertos, % parado, muertes, cofres, distancias de las bajas |
| Peleas frente a humanos | `ml/analyze_fights.py` | Reparto y distancia de las bajas por arma, frente al killfeed humano |
| Datos humanos | `ml/models/s3/human_metrics_s3.json` (Temporada 3, 19 partidas), `ml/models/human_metrics_ch1.json` (Temp. 6/7) | La referencia |
| Plan por fases | `PLAN-BOTS.md` | Fases 0 y A–G, estado y números |

---

## 2. Métricas y objetivos

| Métrica | Humanos (T3) | Bots ahora (nivel 7) | Fuente |
|---|---|---|---|
| Disparos que aciertan | ~21% | ~24% | `hit rates after N shots` del log |
| Aciertos por nivel | – | nivel 3 ≈ 16%, nivel 7 ≈ 22–24%, nivel 10 ≈ 28% | idem |
| Tiempo parado en el suelo | 20% | 23,7–26,8% | `still%` del simulador |
| Muertes por caída | ~2% | 0–1,3% | `fallDeaths%` |
| Muertes por tormenta | bajas | 0–1,4% | `stormDeaths%` |
| Bajas con escopeta / AR | 31% / 21% (T3) | 44% / 49% | `analyze_fights.py` |
| Distancia de la baja: escopeta / AR / pistola | 6,1 / 11,1 / 6,3 m | 6,4 / 20–25 / 4–6 m | idem |
| Cofres por bot | – | 2,3–2,9 | `chestsPerBot` |
| Paredes/suelos rotos para pasar por bot | casi ninguno | ~8,5 | `breakThroughsPerBot` |
| Atascos por bot | – | ~1,5 | `trappedPerBot` |

---

## 3. El ciclo

### 3.1 Medir (línea base)
Antes de tocar nada, la versión actual en las mismas condiciones en las que se probará el cambio:

```
python -I sim/bot_sim.py run --bots 25 --level 7 --matches 3 --no-hotdrop --timeout 25 ^
  --alongside --parallel 3 --port 5731 --core-dir "C:\Users\jj\CoreV2\x64\ReleaseSimNN" --label NOMBRE
```

- 3 partidas por tanda tardan unos 18–20 min (en paralelo). Cada servidor usa ~2,7 GB de RAM: como mucho 2 tandas a la vez (6 servidores).
- `--zero-build` prueba el modo Zero Build; `--mixed --traits-from-settings --level 10` prueba la configuración real del jugador.
- **Nunca el puerto 5595**: es el backend con el que se juega.
- **No simular mientras el usuario juega**, salvo que lo autorice.

### 3.2 Diagnosticar
No se cambia nada sin saber la causa. Herramientas en los logs (`_local/sim/<run>/match-*.server.log`):

| Línea del log | Para qué |
|---|---|
| `match stats {...}` (una por bot al final) | `stillByIntent`, `stillSpells` (ratos parado por motivo y duración), `breakThroughsByWhy`, `ledgeTurns`, `trapped`, `death`, `kills`… |
| `stillSpells: "loot stop: …"` | Por qué frena al saquear: camino nuevo/viejo, objetivo nuevo/mismo, motivo de dirección, distancia al objeto |
| `X lands/dies after a N m fall from … : …` | Cada caída de 10 m o más: qué hacía, si iba por su camino, siguiente punto, velocidad |
| `X at (x, y, z) <intent>, speed …` (cada 20 s) | Dónde está y qué hace cada bot |
| `hit rates after N shots` | Aciertos por arma y distancia |
| `is trapped`, `breaks through`, `is off the navmesh`, `opens fire`, `eliminates … from N m` | Eventos concretos |

Método: contar el problema (p. ej. 70 paradas cortas por bot), agrupar por causa con un script pequeño, leer
5–10 casos concretos del log y solo entonces formular la causa.

### 3.3 Cambiar
- Un cambio a la vez, pequeño, con un comentario que diga **por qué** (con el número que lo motivó).
- Cambios de comportamiento **con interruptor o escalados por rasgos/nivel** cuando dependan del jugador.
- Lógica pura → `CoreAthenaBotAwareness.h` con su test en `sim/tests/bot_logic_test.cpp`.
- Mirar los finales de línea del archivo antes de editar (unos son LF y otros CRLF).

### 3.4 Compilar (siempre en una carpeta nueva)
```
"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe" Core.sln -t:Server ^
  /p:Configuration=Release /p:Platform=x64 "/p:OutDir=C:\Users\jj\CoreV2\x64\ReleaseSimNN\" -m -v:minimal
copy x64\ReleaseSim29\CoreClient.dll x64\ReleaseSimNN\  &  copy x64\ReleaseSim29\CoreRedirect.dll x64\ReleaseSimNN\
```
Sin `CoreClient.dll` y `CoreRedirect.dll` en la carpeta, el simulador usa en silencio la última versión pública.

### 3.5 Simular y comparar
- **3 partidas no bastan para decidir por poco**: entre tandas, el % parado varía ±1,5 puntos y las caídas son eventos raros.
  Para cambios pequeños, 6 partidas por versión.
- Para dudas: **cara a cara a la vez** (versión vieja y nueva lanzadas juntas, misma carga del PC).
- Comparar: `python -I sim/runstats.py ETIQUETA_VIEJA ETIQUETA_NUEVA`.
- Mirar también los **casos extremos**, no solo la media (p. ej. un bot con 1885 giros en un borde).

### 3.6 Decidir
- Mejor en lo que se buscaba y **sin empeorar** caídas, tormenta, atascos, aciertos ni % parado → se queda.
- Peor → se deshace y se quita el código (no dejar código muerto). Ejemplo: "saltar desde los tejados" (ReleaseSim50/51) empeoró el % parado y se quitó.

### 3.7 Poner en el servidor de juego
Solo con la app cerrada y sin que el usuario esté jugando:
```
copy x64\Release\Core.dll + Core.pdb  ->  x64\Release-backup-<nombre>\      (copia de seguridad)
copy x64\ReleaseSimNN\Core.dll + Core.pdb  ->  x64\Release\
```
La app carga los bots de `x64\Release` al arrancar (`core_dir` en `server/settings.json`).
Si cambia la app (`app/fortnite_offline_server.pyw` o `app/ui`), recompilar el `.exe` en `app\build\staging` y sustituir
el de la raíz con la app cerrada (el `.exe` viejo sube su propio parche del FOV al PS4).

### 3.8 Apuntar
Resultado, números y versión en `PLAN-BOTS.md` (y en la memoria del asistente). Publicar en GitHub como `bots-N`
solo cuando el usuario lo pida.

---

## 4. Lecciones aprendidas (no repetir)

| Problema | Lección |
|---|---|
| El % parado no bajaba con "esperas largas" | Era ruido de **paradas de menos de 1 s** (70 por bot). Medir la duración de cada rato parado antes de arreglar |
| Las paradas cortas al saquear | 95% eran bots en un tejado con el camino empezando justo debajo; saltar del tejado empeoró. Falta arreglar el punto de inicio del camino |
| Caídas "sin motivo" | La inercia al esprintar sacaba al bot del borde; se quita la velocidad hacia el borde. Saltar barandillas en torres también mataba bots |
| Una comprobación de pendiente demasiado estricta | Atrapó bots 5 min en una cima. Medir la pendiente en 3 m, no en 1 m |
| Bots que andan apuntando | Apuntar obliga a andar: fuera de su distancia, esprintan sin apuntar (escopeta/subfusil) o se plantan (AR) |
| Revisiones de otras IAs | Comprobar cada punto en el código y los logs antes de aplicarlo; algunos son ciertos, otros no |

---

## 5. Lo que queda (por orden)

1. **Paradas cortas en tejados** (~5% del tiempo): arreglar dónde empieza el camino cuando el bot está en un tejado.
2. **Romper paredes para pasar** (~8,5 por bot): usar puertas/escaleras antes de romper.
3. **Movimiento humano**: alternar correr/esprintar, agacharse, saltar al moverse (humanos: esprintan 23%, agachados 7,6%).
4. **Francotirador** (0 bajas).
5. **Construcción (fase F)**: paredes en peleas, cajas para curarse, rampas; después, ediciones.
6. **Ajuste automático de rasgos (fase G)**: repetir este ciclo de forma automática por la noche contra las métricas humanas.
