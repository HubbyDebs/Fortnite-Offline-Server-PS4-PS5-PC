using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Text.Json;
using FortniteReplayReader;
using FortniteReplayReader.Models;
using Unreal.Core;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;
using Unreal.Encryption;

// Extracts exact Chapter 1 human combat/building/weapon-distance metrics directly from .replay files:
//   - EventsOnly pass across ALL replays (0.8s): weapon kill distribution, mats used/gathered, accuracy, damage, placement
//   - Normal/Full KillFeed pass (with native oo2core_5_win64.dll): exact Distance (m) per DeathCause (D1!),
//     exact Storm death %, and TotalPlayerStructures per match (Fase F!).
//
// Usage:
//   ReplayMetrics <replay-dir> [out-json] [--killfeed-limit 120]

if (args.Length < 1)
{
    Console.WriteLine("Usage: ReplayMetrics <replay-dir> [out-json] [--killfeed-limit N]");
    return 1;
}

string dir = args[0];
string outJson = args.Length >= 2 && !args[1].StartsWith("--") ? args[1] : Path.Combine("ml", "models", "ch1_events_metrics.json");
int kfLimit = 120;
for (int i = 0; i < args.Length - 1; i++)
{
    if (args[i].Equals("--killfeed-limit", StringComparison.OrdinalIgnoreCase) && int.TryParse(args[i + 1], out int n))
        kfLimit = n;
}

NativeOodleM.Init();
var files = Directory.GetFiles(dir, "*.replay", SearchOption.AllDirectories).OrderBy(f => f).ToArray();

var gunNames = new Dictionary<int, string>
{
    [0] = "OutsideSafeZone (Storm)",
    [1] = "FallDamage",
    [2] = "Pistol",
    [3] = "Shotgun",
    [4] = "Rifle (AR)",
    [5] = "SMG",
    [6] = "Sniper",
    [7] = "SniperNoScope",
    [8] = "Melee (Pickaxe)",
    [9] = "InfinityBlade",
    [10] = "Grenade",
    [11] = "C4",
    [12] = "GrenadeLauncher",
    [13] = "RocketLauncher",
    [14] = "Minigun",
    [15] = "Bow",
    [16] = "Trap",
    [17] = "DBNOTimeout",
    [22] = "ShoppingCart",
    [23] = "ATK",
    [24] = "QuadCrasher",
    [25] = "Biplane",
    [26] = "BiplaneGun",
    [27] = "LMG",
    [28] = "GasGrenade",
    [32] = "Turret",
};

// Pass 1: Fast EventsOnly across ALL replays
var countsByGun = new Dictionary<int, int>();
int totalReplaysRead = 0, totalElims = 0, knockedEvents = 0, selfElims = 0;
var matUsedList = new List<double>();
var matGatheredList = new List<double>();
var damagePlayersList = new List<double>();
var damageStructsList = new List<double>();
var accuracyList = new List<double>();
var elimsPerPlayerList = new List<double>();
var placeList = new List<double>();
var top10Acc = new List<double>();
var midAcc = new List<double>();
var top10Mats = new List<double>();
var midMats = new List<double>();

foreach (string file in files)
{
    try
    {
        var reader = new ReplayReader(null!, ParseMode.EventsOnly);
        FortniteReplay replay = reader.ReadReplay(file);
        totalReplaysRead++;

        if (replay.Eliminations != null)
        {
            foreach (var e in replay.Eliminations)
            {
                if (e.Knocked) { knockedEvents++; continue; }
                totalElims++;
                if (e.IsSelfElimination) selfElims++;
                countsByGun[e.GunType] = countsByGun.GetValueOrDefault(e.GunType) + 1;
            }
        }
        int pos = (int)(replay.TeamStats?.Position ?? 0);
        if (pos > 0) placeList.Add(pos);
        if (replay.Stats != null)
        {
            if (replay.Stats.MaterialsUsed > 0) matUsedList.Add(replay.Stats.MaterialsUsed);
            if (replay.Stats.MaterialsGathered > 0) matGatheredList.Add(replay.Stats.MaterialsGathered);
            if (replay.Stats.DamageToPlayers > 0) damagePlayersList.Add(replay.Stats.DamageToPlayers);
            if (replay.Stats.DamageToStructures > 0) damageStructsList.Add(replay.Stats.DamageToStructures);
            if (replay.Stats.Accuracy > 0) accuracyList.Add(replay.Stats.Accuracy * 100.0);
            elimsPerPlayerList.Add(replay.Stats.Eliminations);

            if (pos > 0 && pos <= 10)
            {
                if (replay.Stats.Accuracy > 0) top10Acc.Add(replay.Stats.Accuracy * 100.0);
                if (replay.Stats.MaterialsUsed > 0) top10Mats.Add(replay.Stats.MaterialsUsed);
            }
            else if (pos >= 25 && pos <= 70)
            {
                if (replay.Stats.Accuracy > 0) midAcc.Add(replay.Stats.Accuracy * 100.0);
                if (replay.Stats.MaterialsUsed > 0) midMats.Add(replay.Stats.MaterialsUsed);
            }
        }
    }
    catch { }
}

// Pass 2: KillFeed pass (ParseMode.Normal) for exact weapon distances (D1) & structures (Fase F)
var kfDistsByCause = new ConcurrentDictionary<int, ConcurrentBag<double>>();
var kfCountsByCause = new ConcurrentDictionary<int, int>();
var totalStructsList = new ConcurrentBag<double>();
int kfReplaysOk = 0;
int kfTotalEntries = 0;

// Only filter by replays_ch1_jsonl when scanning replays_ch1 (so replays_s3 or custom dirs parse all files)
bool isCh1Dir = dir.Contains("replays_ch1", StringComparison.OrdinalIgnoreCase);
var goodNames = (isCh1Dir && Directory.Exists("ml/data/replays_ch1_jsonl"))
    ? Directory.GetFiles("ml/data/replays_ch1_jsonl", "*.jsonl")
        .Where(p => new FileInfo(p).Length > 1024)
        .Select(p => Path.GetFileNameWithoutExtension(p))
        .ToHashSet(StringComparer.OrdinalIgnoreCase)
    : new HashSet<string>(StringComparer.OrdinalIgnoreCase);
var kfTargets = files
    .Where(f => goodNames.Count == 0 || goodNames.Contains(Path.GetFileNameWithoutExtension(f)))
    .OrderByDescending(f => f.Contains("_s7_"))
    .Take(kfLimit)
    .ToArray();
Parallel.ForEach(kfTargets, new ParallelOptions { MaxDegreeOfParallelism = 6 }, file =>
{
    try
    {
        var reader = new Ch1ReaderM(ParseMode.Normal);
        FortniteReplay replay = reader.ReadReplay(file);
        Interlocked.Increment(ref kfReplaysOk);
        if (replay.GameData?.TotalPlayerStructures is int ts && ts > 0)
        {
            totalStructsList.Add(ts);
        }
        if (replay.KillFeed != null)
        {
            foreach (var kf in replay.KillFeed)
            {
                if (kf.IsDowned || kf.IsRevived) continue;
                Interlocked.Increment(ref kfTotalEntries);
                int dc = kf.DeathCause ?? -1;
                kfCountsByCause.AddOrUpdate(dc, 1, (_, c) => c + 1);
                if (kf.Distance.HasValue && kf.Distance.Value > 50.0f && kf.Distance.Value < 40000.0f)
                {
                    kfDistsByCause.GetOrAdd(dc, _ => new ConcurrentBag<double>()).Add(kf.Distance.Value / 100.0);
                }
            }
        }
    }
    catch { }
});

static object Summarize(IEnumerable<double> src)
{
    var vals = src.ToList();
    if (vals.Count == 0) return new { n = 0, mean = 0.0, p25 = 0.0, median = 0.0, p75 = 0.0, p90 = 0.0 };
    vals.Sort();
    double Pct(double q) => vals[Math.Clamp((int)Math.Round(q * (vals.Count - 1)), 0, vals.Count - 1)];
    return new
    {
        n = vals.Count,
        mean = Math.Round(vals.Average(), 2),
        p25 = Math.Round(Pct(0.25), 2),
        median = Math.Round(Pct(0.50), 2),
        p75 = Math.Round(Pct(0.75), 2),
        p90 = Math.Round(Pct(0.90), 2),
    };
}

var kfWeaponDists = new Dictionary<string, object>();
foreach (var kv in kfCountsByCause.OrderByDescending(k => k.Value))
{
    string label = gunNames.TryGetValue(kv.Key, out var n) ? $"{kv.Key}:{n}" : $"{kv.Key}:Other";
    var dists = kfDistsByCause.TryGetValue(kv.Key, out var bag) ? bag.ToList() : new List<double>();
    kfWeaponDists[label] = new
    {
        kills = kv.Value,
        pct = Math.Round(100.0 * kv.Value / Math.Max(1, kfTotalEntries), 2),
        distance_m = Summarize(dists)
    };
}

var output = new
{
    events_pass = new
    {
        replays_scanned = totalReplaysRead,
        total_eliminations = totalElims,
        knocked_events = knockedEvents,
        self_eliminations = selfElims,
        weapon_share = countsByGun.OrderByDescending(k => k.Value).Take(12).ToDictionary(
            k => gunNames.TryGetValue(k.Key, out var n) ? $"{k.Key}:{n}" : $"{k.Key}:Other",
            k => new { kills = k.Value, pct = Math.Round(100.0 * k.Value / Math.Max(1, totalElims), 2) }
        )
    },
    D1_killfeed_weapon_distances = new
    {
        replays_parsed = kfReplaysOk,
        total_killfeed_elims = kfTotalEntries,
        by_death_cause = kfWeaponDists
    },
    Fase_F_structures_and_mats = new
    {
        total_player_structures_per_match = Summarize(totalStructsList),
        recorder_materials_used = Summarize(matUsedList),
        recorder_pieces_built = Summarize(matUsedList.Select(m => m / 10.0)),
        recorder_materials_gathered = Summarize(matGatheredList),
        top10_pieces_built = Summarize(top10Mats.Select(m => m / 10.0)),
        median_player_pieces_built = Summarize(midMats.Select(m => m / 10.0)),
    },
    recorder_combat_stats = new
    {
        accuracy_pct = Summarize(accuracyList),
        top10_accuracy_pct = Summarize(top10Acc),
        median_player_accuracy_pct = Summarize(midAcc),
        damage_to_players = Summarize(damagePlayersList),
        damage_to_structures = Summarize(damageStructsList),
        eliminations = Summarize(elimsPerPlayerList),
        placement = Summarize(placeList),
    }
};

Directory.CreateDirectory(Path.GetDirectoryName(outJson) ?? ".");
string json = JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true });
File.WriteAllText(outJson, json);
Console.WriteLine(json);
return 0;

sealed class Ch1ReaderM : ReplayReader
{
    private readonly DataBunch _s3Bunch = new();
    private int _s3PktId = 0, _s3InRel = 0;

    public Ch1ReaderM(ParseMode mode) : base(null!, mode) { }

    protected override void OnExternalDataRead(uint channelIndex, IExternalData? externalData)
    {
        try { base.OnExternalDataRead(channelIndex, externalData); }
        catch { }
    }

    public override void ReceivedPacket(FBitArchive bitReader)
    {
        if (bitReader.EngineNetworkVersion >= EngineNetworkVersionHistory.HISTORY_CHANNEL_NAMES)
        {
            base.ReceivedPacket(bitReader);
            return;
        }
        _s3PktId++;
        int guard = 0;
        while (!bitReader.AtEnd() && !bitReader.IsError && ++guard < 256)
        {
            if (bitReader.EngineNetworkVersion < EngineNetworkVersionHistory.HISTORY_ACKS_INCLUDED_IN_HEADER)
                bitReader.ReadBit();
            var bunch = _s3Bunch;
            var bControl = bitReader.ReadBit();
            bunch.PacketId = _s3PktId;
            bunch.bOpen = bControl && bitReader.ReadBit();
            bunch.bClose = bControl && bitReader.ReadBit();
            if (bitReader.EngineNetworkVersion < EngineNetworkVersionHistory.HISTORY_CHANNEL_CLOSE_REASON)
            {
                bunch.bDormant = bunch.bClose && bitReader.ReadBit();
                bunch.CloseReason = bunch.bDormant ? ChannelCloseReason.Dormancy : ChannelCloseReason.Destroyed;
            }
            else
            {
                bunch.CloseReason = bunch.bClose ? (ChannelCloseReason)bitReader.ReadSerializedInt((int)ChannelCloseReason.MAX) : ChannelCloseReason.Destroyed;
                bunch.bDormant = bunch.CloseReason == ChannelCloseReason.Dormancy;
            }
            bunch.bIsReplicationPaused = bitReader.ReadBit();
            bunch.bReliable = bitReader.ReadBit();
            bunch.ChIndex = bitReader.EngineNetworkVersion < EngineNetworkVersionHistory.HISTORY_MAX_ACTOR_CHANNELS_CUSTOMIZATION
                ? bitReader.ReadSerializedInt(10240) : bitReader.ReadIntPacked();
            bunch.bHasPackageMapExports = bitReader.ReadBit();
            bunch.bHasMustBeMappedGUIDs = bitReader.ReadBit();
            bunch.bPartial = bitReader.ReadBit();
            bunch.ChSequence = bunch.bReliable ? (++_s3InRel) : (bunch.bPartial ? _s3PktId : 0);
            bunch.bPartialInitial = bunch.bPartial && bitReader.ReadBit();
            bunch.bPartialFinal = bunch.bPartial && bitReader.ReadBit();
            if (bunch.bReliable || bunch.bOpen)
            {
                var type = bitReader.ReadSerializedInt((int)ChannelType.MAX);
#pragma warning disable CS0618
                bunch.ChType = (ChannelType)type;
#pragma warning restore CS0618
            }
            else
            {
#pragma warning disable CS0618
                bunch.ChType = ChannelType.None;
#pragma warning restore CS0618
            }
            int bunchDataBits = (int)bitReader.ReadSerializedInt(MaxPacketSizeInBits);
            if (bunchDataBits < 0 || bunchDataBits > bitReader.GetBitsLeft()) break;
            if (bunch.bPartial)
            {
                bunch.Archive = new BitReader(bitReader.ReadBits(bunchDataBits), bunchDataBits)
                {
                    EngineNetworkVersion = bitReader.EngineNetworkVersion,
                    NetworkVersion = bitReader.NetworkVersion,
                    ReplayHeaderFlags = bitReader.ReplayHeaderFlags
                };
            }
            else
            {
                bitReader.SetTempEnd(bunchDataBits, FBitArchiveEndIndex.BUNCH);
                bunch.Archive = bitReader;
            }
            if (bitReader.IsError) break;
            if (bunch.bHasPackageMapExports) ReceiveNetGUIDBunch(bunch.Archive);
            if (bunch.ChIndex >= Channels.Length) { if (!bunch.bPartial) bitReader.RestoreTempEnd(FBitArchiveEndIndex.BUNCH); break; }
            if (Channels[bunch.ChIndex] == null) Channels[bunch.ChIndex] = new UChannel { ChannelIndex = bunch.ChIndex };
            try { ReceivedRawBunch(bunch); } catch { }
            finally { if (!bunch.bPartial) bitReader.RestoreTempEnd(FBitArchiveEndIndex.BUNCH); }
        }
    }

    protected override FArchive Decompress(FArchive archive)
    {
        if (!Replay.Info.IsCompressed) return archive;
        int decSize = archive.ReadInt32();
        int compSize = archive.ReadInt32();
        byte[] comp = archive.ReadBytes(compSize).ToArray();
        ReadOnlyMemory<byte> output;
        try { output = Oodle.DecompressReplayData(comp, decSize); }
        catch { output = NativeOodleM.Decompress(comp, decSize); }
        return new Unreal.Core.BinaryReader(output)
        {
            EngineNetworkVersion = archive.EngineNetworkVersion,
            NetworkVersion = archive.NetworkVersion,
            ReplayHeaderFlags = archive.ReplayHeaderFlags,
            ReplayVersion = archive.ReplayVersion,
        };
    }
}

static class NativeOodleM
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate long OodleFn(
        byte[] compBuf, long compBufSize, byte[] rawBuf, long rawLen,
        int fuzzSafe, int checkCRC, int verbosity,
        IntPtr decBufBase, long decBufSize, IntPtr fpCallback, IntPtr callbackUserData,
        IntPtr decoderMemory, long decoderMemorySize, int threadPhase);

    private static OodleFn? _fn;
    public static void Init()
    {
        var paths = new List<string>();
        foreach (string s in new[] { "server/settings.json", "../server/settings.json" })
        {
            try
            {
                if (!File.Exists(s)) continue;
                using var doc = JsonDocument.Parse(File.ReadAllText(s));
                if (doc.RootElement.TryGetProperty("pc_build", out var b) && b.GetString() is string root)
                    paths.Add(Path.Combine(root, "FortniteGame", "Binaries", "ThirdParty", "Oodle", "Win64", "oo2core_5_win64.dll"));
            }
            catch { }
        }
        foreach (string p in paths)
        {
            if (File.Exists(p) && NativeLibrary.TryLoad(p, out IntPtr lib) && NativeLibrary.TryGetExport(lib, "OodleLZ_Decompress", out IntPtr fn))
            {
                _fn = Marshal.GetDelegateForFunctionPointer<OodleFn>(fn);
                return;
            }
        }
    }
    public static byte[] Decompress(byte[] comp, int decSize)
    {
        if (_fn == null) throw new InvalidOperationException("Oodle DLL not loaded");
        byte[] outBuf = new byte[decSize];
        long res = _fn(comp, comp.Length, outBuf, decSize, 1, 0, 0, IntPtr.Zero, 0, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, 0, 0);
        if (res != decSize) throw new Exception($"Oodle failed: {res} != {decSize}");
        return outBuf;
    }
}
