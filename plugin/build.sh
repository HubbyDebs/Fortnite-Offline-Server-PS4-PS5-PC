#!/bin/sh
# Builds fnredir.prx (GoldHEN plugin). Usage: sh build.sh
# Needs, under ../tools (see plugin/README.md): LLVM 18 (llvm/bin/clang.exe, ld.lld.exe, llvm-ar.exe),
# the OpenOrbis PS4 Toolchain (oo/OpenOrbis/PS4Toolchain), GoldHEN_Plugins_SDK (sdk) and
# GoldHEN_Plugins_Repository (plugins-repo, for common/plugin_common.c).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
T="$HERE/../tools"
OO="$T/oo/OpenOrbis/PS4Toolchain"
SDK="$T/sdk"
COMMON="$T/plugins-repo/common"
CC="$T/llvm/bin/clang.exe"
LD="$T/llvm/bin/ld.lld.exe"
AR="$T/llvm/bin/llvm-ar.exe"
B="$HERE/build"
mkdir -p "$B/sdk"
[ -f "$COMMON/git_ver.h" ] || printf '#pragma once\n#define GIT_COMMIT "fnredir"\n#define GIT_VER "local"\n#define GIT_NUM 1\n#define BUILD_DATE "%s"\n' "$(date '+%b %d %Y')" > "$COMMON/git_ver.h"

# 1. GoldHEN SDK (libGoldHEN_Hook.a), only the first time
if [ ! -f "$SDK/libGoldHEN_Hook.a" ]; then
  SDKF="--target=x86_64-pc-freebsd12-elf -DORBIS=1 -D__ORBIS__=1 -DPS4=1 -DOO=1 -D__PS4__=1 -DOOPS4=1 -D__OOPS4__=1 -D__BSD_VISIBLE=1 -D_BSD_SOURCE=1 -march=btver2 -mtune=btver2 -fPIC -std=c11 -isysroot $OO -isystem $OO/include -I$SDK/include -D__USE_KLOG__ -DDEBUG=0"
  for f in "$SDK"/source/*.c; do
    "$CC" $SDKF -c "$f" -o "$B/sdk/$(basename "$f" .c).o"
  done
  "$CC" -target x86_64-pc-linux-gnu -ffreestanding -nostdlib -fno-builtin -fPIC -isysroot "$OO" -isystem "$OO/include" -I"$SDK/include" -c "$SDK/crt/crtprx.c" -o "$B/sdk/crtprx.o"
  "$AR" --format=bsd rcs "$SDK/libGoldHEN_Hook.a" "$B"/sdk/*.o
fi

# 2. Plugin
CF="-D__FINAL__=1 -D__USE_PRINTF__ --target=x86_64-pc-freebsd12-elf -fPIC -funwind-tables -Wall -isysroot $OO -isystem $OO/include -I$SDK/include -I$COMMON"
"$CC" $CF -c "$HERE/source/main.c" -o "$B/main.o"
"$CC" $CF -c "$COMMON/plugin_common.c" -o "$B/plugin_common.o"
"$LD" -m elf_x86_64 -pie --script "$OO/link.x" -e _init --eh-frame-hdr -L"$OO/lib" -L"$SDK" \
  "$B/sdk/crtprx.o" "$B/main.o" "$B/plugin_common.o" -lSceLibcInternal -lGoldHEN_Hook -lkernel -lSceSysmodule -lSceHttp -lSceNet -o "$B/fnredir.elf"
OO_PS4_TOOLCHAIN="$(cygpath -w "$OO" 2>/dev/null || echo "$OO")" "$OO/bin/windows/create-fself.exe" -in="$B/fnredir.elf" -out="$B/fnredir.oelf" -lib="$HERE/fnredir.prx" --paid 0x3800000000000011
ls -la "$HERE/fnredir.prx"
