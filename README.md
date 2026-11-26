<p align="center"><img src="app/icon.png" width="96" alt=""></p>

<h1 align="center">Fortnite Offline Server</h1>

<p align="center">
Play <b>Fortnite Battle Royale 3.6</b> (Chapter 1, Season 3) offline, against bots, on a jailbroken <b>PS4</b> and on <b>PC</b>,<br>
with your own server running on your computer. LAN play between PS4s and PCs is planned.
</p>

<p align="center">
<a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/license-GPL--3.0-blue"></a>
<img alt="Fortnite 3.6" src="https://img.shields.io/badge/Fortnite-3.6%20(Season%203)-7b5cff">
<img alt="PS4 GoldHEN" src="https://img.shields.io/badge/PS4-GoldHEN-2fb8ff">
</p>

> [!IMPORTANT]
> This is a fan project for personal, offline use. It is **not affiliated with, endorsed by or connected to Epic Games
> or Sony**. Fortnite is a trademark of Epic Games, Inc. This repository contains **no game files**: you need your own
> copy of the game. Do not use it to connect to Epic's online services.

---

## What it does

```
 PS4 (GoldHEN)                                  Your PC
┌─────────────────────────┐                   ┌──────────────────────────────────────┐
│ Fortnite 3.6            │   HTTP (login,    │ Core-Backend  :5595                  │
│ + fnredir.prx plugin ───┼── lobby, party) ─▶│  accounts, profiles, item shop,      │
│   sends Epic's servers  │   XMPP :5222      │  matchmaking, chat (XMPP)            │
│   to your PC            │                   ├──────────────────────────────────────┤
│                         │   UDP :7777       │ Fortnite PC 3.6 + CoreV2             │
│                         ├──────────────────▶│  headless match server with bots     │
└─────────────────────────┘                   └──────────────────────────────────────┘
        ▲  the desktop app finds the console, uploads the plugin and runs everything
```

- **The PS4 plugin (`plugin/`)** hooks the console's own HTTP and DNS functions inside Fortnite and points Epic's
  servers to your PC. No game file is changed.
- **The backend (`server/`)** is [Core-Backend](https://github.com/PongooDev/Core-Backend) with a small patch for the
  PS4 (PSN sign-in, a maxed Battle Pass for chosen accounts) and a guard that only lets your consoles and LAN in.
- **The match server** is [CoreV2](https://github.com/PongooDev/CoreV2) on the PC build of Fortnite 3.6: real bots
  that drop, loot, fight, heal and build. The bots are this project's own work, in [`corev2/`](corev2): CoreV2 at a
  fixed commit plus `corev2/corev2.patch` (navigation, storm, looting, fights, traits, match stats, navmesh cache).
  GitHub builds it on every change and publishes `Core.dll`, `CoreClient.dll` and `CoreRedirect.dll` as a `bots-N`
  [release](https://github.com/HubbyDebs/Fortnite-Offline-Server-PS4-PS5-PC/releases), which the app downloads.
- **The desktop app (`app/`)** starts and stops everything, finds your PS4 on the network, installs and updates the
  plugin over FTP, and shows what is going on.

## Status

| Part | State |
|---|---|
| PS4 plugin: redirect HTTP + DNS to the PC | ✅ working |
| Sign-in, profile, item shop, cloud settings from the PS4 | ✅ working |
| Two account modes: progression / everything unlocked (all cosmetics, Battle Pass tier 100) | ✅ working |
| Bot personalities: twenty traits, skill presets, mixed play styles ([`corev2/`](corev2)) | ✅ built, waiting for the match server to be tested in a match |
| Bots that take height on you (wall + ramp stairs up to 3 floors), strafe and jump by traits, run when out of ammo | ✅ built, waiting for the match server to be tested in a match |
| Peeking, climbing round the sides, edits | 🚧 planned |
| Chat and party (XMPP) | 🧪 implemented, being tested |
| Desktop app (find the PS4, install the plugin, bots, accounts, log) | ✅ working |
| Match server with bots (CoreV2 + Fortnite PC 3.6) | ✅ working: the PS4 joins matches against the bots |
| LAN: PS4 + PS4, PC + PC, PS4 + PC | 🚧 the backend accepts LAN players; needs the match server. PS4 (CL-4016789) and PC (CL-4019403) builds differ, so cross-play still has to be confirmed |

## Requirements

- **PS4** with [GoldHEN](https://github.com/GoldHEN/GoldHEN) and its FTP server on, and **Fortnite 3.6**
  (Season 3, `ECHO03600`, CL-4016789) installed. You provide the game.
- **Windows PC** on the same network, with [Node.js](https://nodejs.org) 22+, [Git](https://git-scm.com) and
  [Python](https://www.python.org) 3.11+ (for the desktop app). Microsoft Edge is used for the app's window.
- For matches: the **PC build of Fortnite 3.6** (CL-4019403) in a folder named like `3.6-CL-4019403` (or `pc-3.6`). You provide it.

## Setup

```sh
git clone https://github.com/HubbyDebs/Fortnite-Offline-Server-PS4-PS5-PC.git
cd Fortnite-Offline-Server-PS4-PS5-PC/server
node setup.mjs        # downloads Core-Backend at the tested commit, applies core-backend.patch, installs it
node test.mjs         # optional: checks the backend end to end without the game
```

Then open the desktop app:

```sh
cd ../app
pip install pyinstaller pillow   # only needed to build the .exe
build_exe.bat                    # builds "Fortnite Offline Server.exe" in the project folder
```

or run it directly with `pythonw app/fortnite_offline_server.pyw`.

## How to play (PS4)

1. Turn on the PS4, load GoldHEN and enable its FTP server.
2. Open **Fortnite Offline Server** on the PC. It finds the console by itself.
3. Press **Prepare PS4** once: it uploads `fnredir.prx`, writes `/data/GoldHEN/fnredir.ini` with your PC's address
   and enables the plugin **for Fortnite only** (`[ECHO03600]` and `[CUSA07022]` in `plugins.ini`; the old file is kept as
   `plugins.ini.before-fortnite`). From then on, the app keeps the plugin and your PC's IP up to date by itself.
4. Press the **power button** to start the server.
5. Start Fortnite on the PS4 and sign in with **`name@core.com`** and any password. The part before `@` is your
   in-game name and a new account is made the first time.

### Account modes

- **Progression**: any account starts from zero and levels up the Battle Pass by playing.
- **Everything unlocked**: accounts listed in the app (or `unlock_all` in `server/settings.json`) get every cosmetic
  with all its styles and the season's Battle Pass bought at tier 100. Restart the game once after the first sign-in.

### Settings (`server/settings.json`)

| Key | Meaning |
|---|---|
| `ps4_ip` | `"auto"` (find consoles with the PlayStation discovery protocol) or a fixed IPv4 |
| `port` | backend port (default `5595`; XMPP is also served on `5222`) |
| `psn_account` | account used when a console signs in with PSN |
| `bots.count` / `bots.fight_chance` | number of bots (0-99), % of bot-vs-bot fights |
| `bots.traits` | the bots' twenty traits, 0-100 each (aim, reactions, aggression, building, height, healing...); without it every trait follows `bots.level` (1-10) |
| `bots.mixed` | `true`: every bot gets its own skill tier and play style (looter, rusher, builder, camper, balanced) |
| `bots.materials` / `bots.harvest_multiplier` | wood, stone and metal each bot leaves the bus with (0-999), and how many times what its pickaxe harvests (1-10): bots that can afford to build |
| `unlock_all` | account names with everything unlocked |
| `lan` | `true` lets any device on the local network in (PCs with Core Launcher, other consoles) |
| `pc_build` | folder of the PC build of Fortnite 3.6 (CL-4019403), the one holding `FortniteGame`; empty uses `pc-3.6` |
| `match_server` | `false` does not start the match server with the backend |
| `warmup_seconds` | seconds on the warmup island before the bus (10-1800); absent uses the game's 120 |

## Repository layout

| Path | What |
|---|---|
| `server/start.mjs` | runs Core-Backend with the settings, the network guard, XMPP on 5222, PS4 discovery and unlock-all |
| `server/find-ps4.mjs` | PlayStation discovery protocol (UDP 987) |
| `server/setup.mjs`, `server/core-backend.patch` | fetches Core-Backend at a fixed commit and applies this project's changes |
| `corev2/setup.mjs`, `corev2/corev2.patch` | the bots: fetches CoreV2 at a fixed commit and applies this project's changes (`--build` also builds it with Visual Studio 2022); `corev2/make-patch.mjs <CoreV2 working copy>` regenerates the patch |
| `server/test.mjs` | end-to-end test of the backend without the game |
| `plugin/source/main.c`, `plugin/build.sh` | the GoldHEN plugin and its build script; `plugin/fnredir.prx` is a prebuilt copy |
| `app/` | desktop app: Python standard library + an HTML interface in an Edge app window |

### Building the plugin

`plugin/build.sh` expects, in a `tools/` folder next to `plugin/`:
[LLVM 18](https://github.com/llvm/llvm-project/releases/tag/llvmorg-18.1.8) (`tools/llvm`),
the [OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain) (`tools/oo`),
[GoldHEN_Plugins_SDK](https://github.com/GoldHEN/GoldHEN_Plugins_SDK) (`tools/sdk`) and
[GoldHEN_Plugins_Repository](https://github.com/GoldHEN/GoldHEN_Plugins_Repository) (`tools/plugins-repo`).
Run it from Git Bash: `sh plugin/build.sh`.

## Credits and thanks

This project stands on the work of others. Thank you!

- **[Core](https://github.com/PongooDev/CoreV2)** by **PongooDev** and the
  [Core contributors](https://github.com/PongooDev/CoreV2/graphs/contributors): **CoreV2**, the match server with
  bots, and **[Core-Backend](https://github.com/PongooDev/Core-Backend)**, the backend this project runs and patches.
  Without Core none of this would exist. Both are GPL-3.0.
- **[GoldHEN](https://github.com/GoldHEN/GoldHEN)** by SiSTR0 and the GoldHEN team: the homebrew enabler, its plugin
  loader, the [plugin SDK](https://github.com/GoldHEN/GoldHEN_Plugins_SDK) (MIT) and the
  [plugins repository](https://github.com/GoldHEN/GoldHEN_Plugins_Repository) (GPL-3.0). The fnredir plugin follows the
  structure of its **AFR** plugin by **jocover** and **SiSTR0**.
- **[OpenOrbis PS4 Toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain)** (GPL-3.0), used to build the
  plugin, and **[LLVM](https://llvm.org)**.
- **ECHO**, whose PS4 setup showed that Fortnite 3.60's services can be pointed elsewhere through the game's config.

If you use this project, please credit **Core** as well.

## License

[GNU General Public License v3.0](LICENSE), the same as Core. You may use, change and share this project; if you
share a modified version, it must stay GPL-3.0 and come with its source code.

Fortnite and all its content belong to Epic Games, Inc. and are not covered by this license.
