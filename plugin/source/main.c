// fnredir: GoldHEN plugin that sends Fortnite's connections to the backend on your PC.
// Part of Fortnite-Offline-Server (GPL-3.0). Hook setup follows GoldHEN's AFR plugin (jocover, SiSTR0).
//
// It does not touch any game file: it hooks Sony's HTTP functions (libSceHttp) and DNS resolver (libSceNet). URLs and
// host names on the list go to "target" instead.
//
// Settings: /data/GoldHEN/fnredir.ini
//   target=http://192.168.1.10:5595
//   host=fortnite-master-server.ezfn.dev
//   host=.epicgames.com          (a leading dot = that domain and all its subdomains)
// Log:      /data/GoldHEN/fnredir.log (each different host the game asks for, and what was done)

#include "Common.h"
#include "plugin_common.h"
#include <fcntl.h>
#include <stdbool.h>
#include <stdlib.h>
#include <orbis/Http.h>
#include <orbis/Net.h>

attr_public const char *g_pluginName = "fnredir";
attr_public const char *g_pluginDesc = "Sends Fortnite's connections to your PC";
attr_public const char *g_pluginAuth = "Fortnite-Offline-Server";
attr_public u32 g_pluginVersion = 0x00000300; // 3.00

#define INI_PATH GOLDHEN_PATH "/fnredir.ini"
#define LOG_PATH GOLDHEN_PATH "/fnredir.log"
#define MAX_HOSTS 16
#define MAX_SEEN 64
#define URL_MAX 4096

HOOK_INIT(sceHttpCreateConnectionWithURL);
HOOK_INIT(sceHttpCreateRequestWithURL);
HOOK_INIT(sceHttpCreateConnection);
HOOK_INIT(sceNetResolverStartNtoa);
HOOK_INIT(sceNetResolverStartNtoaMultipleRecords);

// SceNetResolverInfo from Sony's SDK (not in OpenOrbis): 10 addresses of 20 bytes and the record count.
typedef struct {
    union { u32 v4; u8 v6[16]; } un;
    s32 af;
} ResolverAddr;
typedef struct {
    ResolverAddr addrs[10];
    s32 records;
    s32 dns4records;
    s32 reserved1;
    s32 reserved[13];
} ResolverInfo;
#define AF_INET_SCE 2

static char target_scheme[8] = "http";
static char target_host[128];
static u16 target_port = 80;
static char hosts[MAX_HOSTS][128];
static int n_hosts;
static char seen[MAX_SEEN][128];
static int n_seen;

static void log_line(const char *a, const char *b, const char *c)
{
    s32 fd = sceKernelOpen(LOG_PATH, O_WRONLY | O_CREAT | O_APPEND, 0666);
    if (fd < 0)
        return;
    const char *parts[3] = { a, b, c };
    for (int i = 0; i < 3; i++) {
        if (!parts[i])
            continue;
        if (i)
            sceKernelWrite(fd, " ", 1);
        sceKernelWrite(fd, parts[i], strlen(parts[i]));
    }
    sceKernelWrite(fd, "\n", 1);
    sceKernelClose(fd);
}

static char lower(char c)
{
    return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
}

static int same_n(const char *a, const char *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (lower(a[i]) != lower(b[i]))
            return 0;
    return 1;
}

// Is host (n characters long) on the list?
static int redirected(const char *host, size_t n)
{
    for (int i = 0; i < n_hosts; i++) {
        const char *h = hosts[i];
        size_t l = strlen(h);
        if (h[0] == '.') {
            if ((n == l - 1 && same_n(host, h + 1, n)) || (n > l && same_n(host + n - l, h, l)))
                return 1;
        } else if (n == l && same_n(host, h, n)) {
            return 1;
        }
    }
    return 0;
}

static void note_seen(const char *host, size_t n, int was_redirected)
{
    if (n == 0 || n >= sizeof(seen[0]))
        return;
    for (int i = 0; i < n_seen; i++)
        if (strlen(seen[i]) == n && same_n(seen[i], host, n))
            return;
    if (n_seen >= MAX_SEEN)
        return;
    memcpy(seen[n_seen], host, n);
    seen[n_seen][n] = 0;
    log_line(was_redirected ? "REDIRECTED" : "untouched", seen[n_seen], NULL);
    n_seen++;
}

// If url points at a host on the list, writes the new one to out and returns 1.
static int rewrite(const char *url, char *out, size_t size)
{
    if (!url || !target_host[0])
        return 0;
    const char *p = strstr(url, "://");
    if (!p)
        return 0;
    const char *host = p + 3;
    size_t n = strcspn(host, ":/?#");
    const char *rest = host + n;
    if (*rest == ':')
        rest += strcspn(rest, "/?#");
    int yes = redirected(host, n);
    note_seen(host, n, yes);
    if (!yes)
        return 0;
    int r = snprintf(out, size, "%s://%s:%u%s", target_scheme, target_host, target_port, rest);
    return r > 0 && (size_t)r < size;
}

s32 sceHttpCreateConnectionWithURL_hook(s32 tmpl, const char *url, bool keep)
{
    char changed[URL_MAX];
    if (rewrite(url, changed, sizeof(changed)))
        url = changed;
    return HOOK_CONTINUE(sceHttpCreateConnectionWithURL, s32 (*)(s32, const char *, bool), tmpl, url, keep);
}

s32 sceHttpCreateRequestWithURL_hook(s32 conn, s32 method, const char *url, u64 length)
{
    char changed[URL_MAX];
    if (rewrite(url, changed, sizeof(changed)))
        url = changed;
    return HOOK_CONTINUE(sceHttpCreateRequestWithURL, s32 (*)(s32, s32, const char *, u64), conn, method, url, length);
}

s32 sceHttpCreateConnection_hook(s32 tmpl, const char *server, const char *scheme, u16 port, s32 keep)
{
    if (server && target_host[0]) {
        size_t n = strlen(server);
        int yes = redirected(server, n);
        note_seen(server, n, yes);
        if (yes) {
            server = target_host;
            scheme = target_scheme;
            port = target_port;
        }
    }
    return HOOK_CONTINUE(sceHttpCreateConnection, s32 (*)(s32, const char *, const char *, u16, s32),
                         tmpl, server, scheme, port, keep);
}

// The target's IPv4 (target_host must be an IP), in network order. 0 = not valid.
static u32 target_ip(void)
{
    u32 ip = 0;
    if (target_host[0] && sceNetInetPton(AF_INET_SCE, target_host, &ip) == 1)
        return ip;
    return 0;
}

// Names on the list -> the PC's IP (XMPP and any connection that does not go through libSceHttp).
s32 sceNetResolverStartNtoa_hook(OrbisNetId rid, const char *name, OrbisNetInAddr *addr, s32 t, s32 r, s32 f)
{
    if (name && addr) {
        size_t n = strlen(name);
        int yes = redirected(name, n);
        note_seen(name, n, yes);
        u32 ip = yes ? target_ip() : 0;
        if (ip) {
            memcpy(addr, &ip, sizeof(ip));
            return 0;
        }
    }
    return HOOK_CONTINUE(sceNetResolverStartNtoa, s32 (*)(OrbisNetId, const char *, OrbisNetInAddr *, s32, s32, s32),
                         rid, name, addr, t, r, f);
}

s32 sceNetResolverStartNtoaMultipleRecords_hook(OrbisNetId rid, const char *name, ResolverInfo *info, s32 t, s32 r, s32 f)
{
    if (name && info) {
        size_t n = strlen(name);
        int yes = redirected(name, n);
        note_seen(name, n, yes);
        u32 ip = yes ? target_ip() : 0;
        if (ip) {
            memset(info, 0, sizeof(*info));
            info->addrs[0].un.v4 = ip;
            info->addrs[0].af = AF_INET_SCE;
            info->records = 1;
            info->dns4records = 1;
            return 0;
        }
    }
    return HOOK_CONTINUE(sceNetResolverStartNtoaMultipleRecords, s32 (*)(OrbisNetId, const char *, ResolverInfo *, s32, s32, s32),
                         rid, name, info, t, r, f);
}

static void copy(char *dst, size_t size, const char *src, size_t n)
{
    if (n >= size)
        n = size - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

static void read_target(const char *v)
{
    const char *p = strstr(v, "://");
    if (p) {
        copy(target_scheme, sizeof(target_scheme), v, (size_t)(p - v));
        v = p + 3;
    }
    size_t n = strcspn(v, ":/");
    copy(target_host, sizeof(target_host), v, n);
    target_port = strcmp(target_scheme, "https") == 0 ? 443 : 80;
    if (v[n] == ':')
        target_port = (u16)strtoul(v + n + 1, NULL, 10);
}

static void read_ini(void)
{
    static char buf[4096];
    s32 fd = sceKernelOpen(INI_PATH, O_RDONLY, 0);
    if (fd < 0) {
        log_line("ERROR: missing", INI_PATH, NULL);
        return;
    }
    s64 n = sceKernelRead(fd, buf, sizeof(buf) - 1);
    sceKernelClose(fd);
    if (n <= 0)
        return;
    buf[n] = 0;
    for (char *l = buf; l && *l;) {
        char *end = strpbrk(l, "\r\n");
        if (end)
            *end++ = 0;
        while (*l == ' ' || *l == '\t')
            l++;
        if (strncmp(l, "target=", 7) == 0)
            read_target(l + 7);
        else if (strncmp(l, "host=", 5) == 0 && l[5] && n_hosts < MAX_HOSTS)
            copy(hosts[n_hosts++], sizeof(hosts[0]), l + 5, strcspn(l + 5, " \t"));
        l = end;
    }
}

s32 attr_public plugin_load(s32 argc, const char *argv[])
{
    final_printf("[GoldHEN] <%s\\Ver.0x%08x> %s\n", g_pluginName, g_pluginVersion, __func__);
    read_ini();
    char port[8];
    snprintf(port, sizeof(port), "%u", target_port);
    log_line("LOADED v3, target", target_host[0] ? target_host : "(none)", port);
    HOOK32(sceHttpCreateConnectionWithURL);
    HOOK32(sceHttpCreateRequestWithURL);
    HOOK32(sceHttpCreateConnection);
    HOOK32(sceNetResolverStartNtoa);
    HOOK32(sceNetResolverStartNtoaMultipleRecords);
    return 0;
}

s32 attr_public plugin_unload(s32 argc, const char *argv[])
{
    UNHOOK(sceHttpCreateConnectionWithURL);
    UNHOOK(sceHttpCreateRequestWithURL);
    UNHOOK(sceHttpCreateConnection);
    UNHOOK(sceNetResolverStartNtoa);
    UNHOOK(sceNetResolverStartNtoaMultipleRecords);
    return 0;
}

s32 attr_module_hidden module_start(s64 argc, const void *args)
{
    return 0;
}

s32 attr_module_hidden module_stop(s64 argc, const void *args)
{
    return 0;
}
