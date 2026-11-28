using System.Runtime.InteropServices;
using System.Text.Json;
using FortniteReplayReader;
using FortniteReplayReader.Models;
using FortniteReplayReader.Models.NetFieldExports;
using Unreal.Core;
using Unreal.Core.Contracts;
using Unreal.Core.Models;
using Unreal.Core.Models.Enums;
using Unreal.Encryption;

// Extracts Fortnite .replay files (Chapter 1 Season 3-10 & Chapter 2+) into the unified JSONL format
// consumed by ml/train_bc.py, ml/build_heatmap.py, and ml/replay_to_map.py.
// Supports Season 3 (v3.5/v3.6, NetworkVersion=11, EngineNetworkVersion=5) ExternalData movement samples
// and automatically loads oo2core_5_win64.dll (from server/settings.json pc_build) for Oodle LZHLW chunks.
//
// Usage:
//   ReplayExtractor <replay-file-or-dir> [out-dir] [--overwrite] [--oodle <path-to-oo2core.dll>]

if (args.Length < 1)
{
    Console.WriteLine("Usage: ReplayExtractor <replay-file-or-dir> [out-dir] [--overwrite]");
    return 1;
}

string inputPath = args[0];
string outDir = args.Length >= 2 && !args[1].StartsWith("--") ? args[1] : Path.Combine("ml", "data", "replays_jsonl");
bool overwrite = args.Contains("--overwrite", StringComparer.OrdinalIgnoreCase);
string? explicitOodle = null;
for (int i = 0; i < args.Length - 1; i++)
{
    if (args[i].Equals("--oodle", StringComparison.OrdinalIgnoreCase)) explicitOodle = args[i + 1];
}
NativeOodle.Init(explicitOodle);
Directory.CreateDirectory(outDir);

var files = Directory.Exists(inputPath)
    ? Directory.GetFiles(inputPath, "*.replay", SearchOption.AllDirectories)
    : (File.Exists(inputPath) ? new[] { inputPath } : Array.Empty<string>());

if (files.Length == 0)
{
    Console.WriteLine($"No .replay files found at: {inputPath}");
    return 1;
}

int converted = 0;
int skipped = 0;
int failed = 0;
long totalFramesAll = 0;

foreach (string file in files)
{
    string outFile = Path.Combine(outDir, Path.GetFileNameWithoutExtension(file) + ".jsonl");
    if (!overwrite && File.Exists(outFile) && new FileInfo(outFile).Length > 1024)
    {
        skipped++;
        continue;
    }

    try
    {
        var reader = new Chapter1ReplayReader(ParseMode.Full);
        FortniteReplay replay = reader.ReadReplay(file);

        string branch = reader.Branch ?? replay.Header?.Branch ?? "unknown";
        string playlist = replay.GameData?.CurrentPlaylist ?? "";
        int major = reader.Major;
        int minor = reader.Minor;

        // Skip Creative / Playground / Lobby-only replays that have no real BR match
        if (playlist.Contains("Playground", StringComparison.OrdinalIgnoreCase) ||
            playlist.Contains("Creative", StringComparison.OrdinalIgnoreCase))
        {
            Console.WriteLine($"  -- Skipping non-BR ({playlist}): {Path.GetFileName(file)}");
            skipped++;
            continue;
        }

        // Prefer LastUpdateTime (per-packet transform timestamp, ~10Hz) when consistent with
        // ReplicatedWorldTimeSeconds (~0.2Hz in Ch1) to get crisp 1-second trajectories!
        static double PickTime(double? d, float? f, float? last = null)
        {
            double world = (d.HasValue && d.Value > 0.01) ? d.Value
                         : ((f.HasValue && f.Value > 0.01f) ? f.Value : -1.0);
            if (last.HasValue && last.Value > 0.01f)
            {
                if (world < 0 || Math.Abs(last.Value - world) < 60.0)
                    return last.Value;
            }
            return world;
        }

        double aircraftStart = replay.GameData?.AircraftStartTime ?? 0.0;

        var killTimesById = new Dictionary<int, List<double>>();
        var deathTimeById = new Dictionary<int, double>();
        if (replay.KillFeed != null)
        {
            foreach (var kf in replay.KillFeed)
            {
                double t = PickTime(kf.ReplicatedWorldTimeSecondsDouble, kf.ReplicatedWorldTimeSeconds);
                if (t <= 0) continue;
                if (!kf.IsDowned && !kf.IsRevived)
                {
                    if (kf.PlayerId.HasValue)
                    {
                        deathTimeById[kf.PlayerId.Value] = t;
                    }
                    if (kf.FinisherOrDowner.HasValue && kf.FinisherOrDowner != kf.PlayerId)
                    {
                        if (!killTimesById.TryGetValue(kf.FinisherOrDowner.Value, out var list))
                        {
                            list = new List<double>();
                            killTimesById[kf.FinisherOrDowner.Value] = list;
                        }
                        list.Add(t);
                    }
                }
            }
        }
        foreach (var kv in killTimesById) kv.Value.Sort();

        // Sample player movements into 1-second buckets keyed by world time offset
        // First find earliest valid post-warmup world time to normalize match clock to t=0
        double minTime = double.MaxValue;
        var playerList = replay.PlayerData?.ToList() ?? new List<PlayerData>();
        int standardLocCount = 0;
        foreach (var p in playerList)
        {
            if (p.Locations == null) continue;
            standardLocCount += p.Locations.Count;
            foreach (var loc in p.Locations)
            {
                double t = PickTime(loc.ReplicatedWorldTimeSecondsDouble, loc.ReplicatedWorldTimeSeconds, loc.LastUpdateTime);
                if (t > 0 && (aircraftStart <= 0 || t >= aircraftStart - 5.0) && t < minTime)
                    minTime = t;
            }
        }
        bool useS3External = standardLocCount == 0 && reader.S3SamplesByChannel.Count > 0;
        if (useS3External)
        {
            // In Season 3, ExternalData timestamps are demo time seconds (starting near 0 in warmup).
            // Detect end of spawn island (warmup): first time players leave spawn island (x < -105000 && y < -95000) or bus starts.
            double firstMainIslandT = double.MaxValue;
            foreach (var kv in reader.S3SamplesByChannel)
            {
                foreach (var s in kv.Value)
                {
                    bool onSpawnIsland = s.X < -105000 && s.Y < -95000;
                    if (!onSpawnIsland && s.Time > 0 && s.Time < firstMainIslandT)
                        firstMainIslandT = s.Time;
                }
            }
            if (firstMainIslandT < double.MaxValue)
                minTime = firstMainIslandT;
            else
                minTime = 0.0;
        }
        if (minTime == double.MaxValue) minTime = aircraftStart > 0 ? aircraftStart : 0.0;

        double safeZoneStart = replay.GameData?.SafeZonesStartTime ?? (minTime + 25.0);
        if (useS3External) safeZoneStart = minTime + 25.0;
        var frames = new SortedDictionary<int, Dictionary<string, object>>();

        int filteredFast = 0;
        int keptSamples = 0;

        if (useS3External)
        {
            var playerById = new Dictionary<int, PlayerData>();
            foreach (var p in playerList)
            {
                if (p.Id.HasValue && !playerById.ContainsKey(p.Id.Value))
                    playerById[p.Id.Value] = p;
            }

            foreach (var (ch, samples) in reader.S3SamplesByChannel)
            {
                if (samples.Count < 3) continue;
                reader.ChannelToPlayerStateId.TryGetValue(ch, out int psId);
                playerById.TryGetValue(psId, out PlayerData? player);

                string id = (player != null && !string.IsNullOrEmpty(player.PlayerId))
                    ? player.PlayerId
                    : $"ch{ch}";
                string name = player?.PlayerName ?? player?.StreamerModeName ?? id;
                bool isBot = player?.IsBot ?? false;
                int team = player?.TeamIndex ?? -1;

                double dtRaw = player != null ? PickTime(player.DeathTimeDouble, player.DeathTime) : -1.0;
                double? deathWorldTime = dtRaw > 0 ? dtRaw : null;
                if (!deathWorldTime.HasValue && psId > 0 && deathTimeById.TryGetValue(psId, out double dt))
                    deathWorldTime = dt;
                List<double>? ktimes = (psId > 0 && killTimesById.TryGetValue(psId, out var kl)) ? kl : null;

                double prevX = 0, prevY = 0, prevZ = 0, prevT = -1;
                int lastSec = -1;

                foreach (var s in samples)
                {
                    double worldT = s.Time;
                    if (worldT < minTime) continue;
                    // Ignore lingering spawn-island coordinates
                    if (s.X < -105000 && s.Y < -95000) continue;

                    int sec = (int)Math.Round(worldT - minTime);
                    if (sec < 0) sec = 0;
                    if (sec == lastSec) continue;

                    double x = s.X, y = s.Y, z = s.Z;
                    if (double.IsNaN(x) || double.IsNaN(y) || double.IsNaN(z)) continue;
                    if (Math.Abs(x) > 250000 || Math.Abs(y) > 250000) continue;

                    bool isSkydiving = s.IsSkydiving || z > 9000.0;
                    double yaw = 0.0;
                    if (Math.Abs(s.Vx) > 5.0 || Math.Abs(s.Vy) > 5.0)
                    {
                        yaw = Math.Atan2(s.Vy, s.Vx) * (180.0 / Math.PI);
                    }
                    else if (prevT > 0 && worldT > prevT)
                    {
                        double dxm = x - prevX, dym = y - prevY;
                        if (dxm * dxm + dym * dym > 25.0)
                            yaw = Math.Atan2(dym, dxm) * (180.0 / Math.PI);
                    }

                    if (!isSkydiving && prevT > 0 && worldT > prevT)
                    {
                        double dtMove = worldT - prevT;
                        double dx = x - prevX, dy = y - prevY;
                        double speed2d = Math.Sqrt(dx * dx + dy * dy) / dtMove;
                        if (speed2d > 850.0)
                        {
                            prevX = x; prevY = y; prevZ = z; prevT = worldT;
                            filteredFast++;
                            continue;
                        }
                    }

                    prevX = x; prevY = y; prevZ = z; prevT = worldT;
                    lastSec = sec;

                    bool alive = !deathWorldTime.HasValue || worldT < deathWorldTime.Value + 1.0;
                    int killsNow = 0;
                    if (ktimes != null)
                    {
                        foreach (double kt in ktimes)
                        {
                            if (kt <= worldT) killsNow++;
                            else break;
                        }
                    }

                    if (!frames.TryGetValue(sec, out var bucket))
                    {
                        bucket = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                        frames[sec] = bucket;
                    }

                    string state = !alive ? "Dead" : (isSkydiving ? "Skydiving" : "Landed");
                    bucket[id] = new
                    {
                        id,
                        name,
                        isBot,
                        x = Math.Round(x),
                        y = Math.Round(y),
                        z = Math.Round(z),
                        yaw = Math.Round(yaw, 1),
                        hp = alive ? (s.IsDBNO ? 30 : 100) : 0,
                        maxHp = 100,
                        shield = 0,
                        maxShield = 100,
                        mats = 150,
                        hasWeapon = true,
                        hasHeals = false,
                        alive,
                        dbno = s.IsDBNO,
                        inStorm = s.IsInStorm,
                        targeting = s.IsTargeting,
                        crouched = s.IsCrouched,
                        sprinting = s.IsSprinting,
                        jumping = s.IsJumping,
                        kills = killsNow,
                        team,
                        state,
                        style = isBot ? "replay-bot" : "replay-human"
                    };
                    keptSamples++;
                }
            }
        }
        else foreach (var player in playerList)
        {
            if (player.Locations == null || player.Locations.Count == 0) continue;

            string id = !string.IsNullOrEmpty(player.PlayerId)
                ? player.PlayerId
                : (player.Id.HasValue ? $"p{player.Id.Value}" : (player.PlayerName ?? Guid.NewGuid().ToString("N")[..8]));
            string name = player.PlayerName ?? player.StreamerModeName ?? id;
            bool isBot = player.IsBot;
            int team = player.TeamIndex ?? -1; // -1 = unknown/solo, never default to 0!

            double dtRaw = PickTime(player.DeathTimeDouble, player.DeathTime);
            double? deathWorldTime = dtRaw > 0 ? dtRaw : null;
            if (!deathWorldTime.HasValue && player.Id.HasValue && deathTimeById.TryGetValue(player.Id.Value, out double dt))
            {
                deathWorldTime = dt;
            }

            List<double>? ktimes = (player.Id.HasValue && killTimesById.TryGetValue(player.Id.Value, out var kl)) ? kl : null;

            double prevX = 0, prevY = 0, prevZ = 0, prevT = -1;
            int lastSec = -1;

            foreach (var loc in player.Locations)
            {
                if (!loc.ReplicatedMovement.HasValue) continue;
                var rep = loc.ReplicatedMovement.Value;
                if (rep.Location is null) continue;

                double worldT = PickTime(loc.ReplicatedWorldTimeSecondsDouble, loc.ReplicatedWorldTimeSeconds, loc.LastUpdateTime);
                if (worldT < minTime) continue;

                int sec = (int)Math.Round(worldT - minTime);
                if (sec < 0) sec = 0;
                if (sec == lastSec) continue; // 1 Hz sampling

                double x = rep.Location.X;
                double y = rep.Location.Y;
                double z = rep.Location.Z;
                if (double.IsNaN(x) || double.IsNaN(y) || double.IsNaN(z)) continue;
                if (Math.Abs(x) > 250000 || Math.Abs(y) > 250000) continue;

                // Filter out modern sliding, ziplining, and fast vehicles/planes (X-4 Stormwing / quadcrasher / rift)
                bool isSkydiving = loc.bIsSkydiving == true || loc.bIsSkydivingFromBus == true || loc.bIsParachuteOpen == true;
                if (loc.bIsSlopeSliding == true || loc.bIsZiplining == true)
                {
                    prevX = x; prevY = y; prevZ = z; prevT = worldT;
                    filteredFast++;
                    continue;
                }
                if (!isSkydiving && prevT > 0 && worldT > prevT)
                {
                    double dtMove = worldT - prevT;
                    double dx = x - prevX, dy = y - prevY;
                    double speed2d = Math.Sqrt(dx * dx + dy * dy) / dtMove;
                    if (speed2d > 850.0)
                    {
                        prevX = x; prevY = y; prevZ = z; prevT = worldT;
                        filteredFast++;
                        continue;
                    }
                }

                prevX = x; prevY = y; prevZ = z; prevT = worldT;
                lastSec = sec;

                bool alive = !deathWorldTime.HasValue || worldT < deathWorldTime.Value + 1.0;
                int killsNow = 0;
                if (ktimes != null)
                {
                    foreach (double kt in ktimes)
                    {
                        if (kt <= worldT) killsNow++;
                        else break;
                    }
                }

                if (!frames.TryGetValue(sec, out var bucket))
                {
                    bucket = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
                    frames[sec] = bucket;
                }

                double yaw = rep.Rotation is not null ? rep.Rotation.Yaw : 0.0;
                string state = !alive ? "Dead" : (isSkydiving ? "Skydiving" : "Landed");

                bucket[id] = new
                {
                    id,
                    name,
                    isBot,
                    x = Math.Round(x),
                    y = Math.Round(y),
                    z = Math.Round(z),
                    yaw = Math.Round(yaw, 1),
                    hp = alive ? (loc.bIsDBNO == true ? 30 : 100) : 0,
                    maxHp = 100,
                    shield = 0,
                    maxShield = 100,
                    mats = 150,
                    hasWeapon = true,
                    hasHeals = false,
                    alive,
                    dbno = loc.bIsDBNO == true,
                    inStorm = loc.bIsInAnyStorm == true,
                    targeting = loc.bIsTargeting == true,
                    crouched = loc.bIsCrouched == true,
                    sprinting = loc.bIsSprinting == true,
                    jumping = loc.bIsJumping == true,
                    kills = killsNow,
                    team,
                    state,
                    style = isBot ? "replay-bot" : "replay-human"
                };
                keptSamples++;
            }
        }

        if (frames.Count < 10 || keptSamples < 30)
        {
            Console.WriteLine($"  -- Too few samples ({frames.Count} frames, {keptSamples} samples) in {Path.GetFileName(file)} [{branch}]");
            skipped++;
            continue;
        }

        var zones = replay.MapData?.SafeZones?.Where(z => z.NextRadius > 0 || z.LastRadius > 0 || z.Radius > 0).ToList()
                    ?? new List<SafeZone>();

        string sourceTag = major == 3 ? "replay-s3"
            : (major == 7 ? "replay-s7"
            : (major > 0 && major <= 10 ? $"replay-ch1-s{major}" : $"replay-s{major}"));
        long baseTs = replay.GameData?.UtcTimeStartedMatch.HasValue == true
            ? new DateTimeOffset(replay.GameData.UtcTimeStartedMatch.Value, TimeSpan.Zero).ToUnixTimeMilliseconds()
            : DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        int totalPlayers = Math.Max(replay.GameData?.MaxPlayers ?? 0, playerList.Count);

        using var sw = new StreamWriter(outFile);
        foreach (var (sec, bucket) in frames)
        {
            double worldT = minTime + sec;
            object? stormObj = null;
            double timeRem = 60.0;
            string phaseStep = "StormWaiting";

            if (zones.Count > 0)
            {
                int zIdx = 0;
                for (int i = 0; i < zones.Count; i++)
                {
                    if (zones[i].StartShrinkTime > 0 && zones[i].StartShrinkTime <= worldT)
                        zIdx = i;
                }
                var z = zones[zIdx];
                double curX = z.LastCenter?.X ?? z.NextCenter?.X ?? 0;
                double curY = z.LastCenter?.Y ?? z.NextCenter?.Y ?? 0;
                double curR = z.LastRadius > 0 ? z.LastRadius : z.Radius;
                double nxtX = z.NextCenter?.X ?? curX;
                double nxtY = z.NextCenter?.Y ?? curY;
                double nxtR = z.NextRadius > 0 ? z.NextRadius : curR;

                if (z.FinishShrinkTime > z.StartShrinkTime && worldT >= z.StartShrinkTime)
                {
                    double frac = Math.Clamp((worldT - z.StartShrinkTime) / (z.FinishShrinkTime - z.StartShrinkTime), 0.0, 1.0);
                    curX = curX + (nxtX - curX) * frac;
                    curY = curY + (nxtY - curY) * frac;
                    curR = curR + (nxtR - curR) * frac;
                    timeRem = Math.Max(0.0, z.FinishShrinkTime - worldT);
                    phaseStep = frac < 1.0 ? "StormShrinking" : "StormHolding";
                }
                else if (z.StartShrinkTime > worldT)
                {
                    timeRem = Math.Min(120.0, z.StartShrinkTime - worldT);
                    phaseStep = "StormWaiting";
                }

                stormObj = new
                {
                    phase = zIdx + 1,
                    x = Math.Round(curX),
                    y = Math.Round(curY),
                    radius = Math.Round(curR),
                    nextX = Math.Round(nxtX),
                    nextY = Math.Round(nxtY),
                    nextRadius = Math.Round(nxtR)
                };
            }

            var actors = bucket.Values;
            var snap = new
            {
                ts = baseTs + sec * 1000L,
                source = sourceTag,
                version = $"{major}.{minor}",
                phase = worldT < safeZoneStart ? "Aircraft" : "SafeZones",
                phaseStep,
                timeRemaining = Math.Round(timeRem, 1),
                playersLeft = actors.Count,
                totalPlayers = Math.Max(totalPlayers, actors.Count),
                storm = stormObj,
                actors
            };
            sw.WriteLine(JsonSerializer.Serialize(snap));
        }

        Console.WriteLine($"  OK {Path.GetFileName(file)} [{major}.{minor}] -> {frames.Count} frames, {keptSamples} samples, {zones.Count} zones (filtered {filteredFast} fast)");
        converted++;
        totalFramesAll += frames.Count;
    }
    catch (Exception ex)
    {
        Console.WriteLine($"  FAIL {Path.GetFileName(file)}: {ex.GetType().Name}: {ex.Message}");
        failed++;
    }
}

Console.WriteLine($"Done: converted={converted}, skipped={skipped}, failed={failed}, total={files.Length}, frames={totalFramesAll}");
return (converted > 0 || skipped > 0) ? 0 : 1;

readonly record struct S3MoveSample(
    double Time, double X, double Y, double Z,
    double Vx, double Vy, double Vz,
    bool IsSkydiving, bool IsDBNO, bool IsInStorm,
    bool IsTargeting, bool IsCrouched, bool IsSprinting, bool IsJumping);

sealed class Chapter1ReplayReader : ReplayReader
{
    public Dictionary<uint, List<S3MoveSample>> S3SamplesByChannel { get; } = new();
    public Dictionary<uint, int> ChannelToPlayerStateId { get; } = new();

    private readonly Dictionary<uint, PawnState> _pawnStates = new();
    private readonly DataBunch _s3Bunch = new();
    private int _s3PktId = 0;
    private int _s3InRel = 0;
    private bool _isPreCharMove = false;

    private struct PawnState
    {
        public bool IsSkydiving;
        public bool IsDBNO;
        public bool IsInStorm;
        public bool IsTargeting;
        public bool IsCrouched;
        public bool IsSprinting;
        public bool IsJumping;
    }

    public Chapter1ReplayReader(ParseMode mode) : base(null!, mode) { }

    public override void ReadReplayHeader(FArchive archive)
    {
        base.ReadReplayHeader(archive);
        _isPreCharMove = archive.NetworkVersion < NetworkVersionHistory.HISTORY_CHARACTER_MOVEMENT;
    }

    protected override void OnExportRead(uint channelIndex, INetFieldExportGroup? exportGroup)
    {
        if (_isPreCharMove && exportGroup is PlayerPawn pp)
        {
            _pawnStates.TryGetValue(channelIndex, out var st);
            if (pp.bIsSkydiving.HasValue || pp.bIsSkydivingFromBus.HasValue || pp.bIsParachuteOpen.HasValue)
                st.IsSkydiving = (pp.bIsSkydiving == true) || (pp.bIsSkydivingFromBus == true) || (pp.bIsParachuteOpen == true);
            if (pp.bIsDBNO.HasValue) st.IsDBNO = pp.bIsDBNO.Value;
            if (pp.bIsCrouched.HasValue) st.IsCrouched = pp.bIsCrouched.Value;
            if (pp.bIsSprinting.HasValue) st.IsSprinting = pp.bIsSprinting.Value;
            if (pp.bIsJumping.HasValue) st.IsJumping = pp.bIsJumping.Value;
            if (pp.bIsTargeting.HasValue) st.IsTargeting = pp.bIsTargeting.Value;
            _pawnStates[channelIndex] = st;

            if (pp.PlayerState.HasValue)
                ChannelToPlayerStateId[channelIndex] = (int)pp.PlayerState.Value;
        }
        base.OnExportRead(channelIndex, exportGroup);
    }

    protected override void OnExternalDataRead(uint channelIndex, IExternalData? externalData)
    {
        if (_isPreCharMove && externalData is ExternalData ed)
        {
            try
            {
                long startPos = ed.Archive.Position;
                var bytes = new List<byte>(32);
                while (!ed.Archive.AtEnd() && bytes.Count < 128)
                    bytes.Add(ed.Archive.ReadByte());
                ed.Archive.Seek((int)startPos, SeekOrigin.Begin);

                if (bytes.Count >= 12)
                {
                    byte[] arr = bytes.ToArray();
                    float t = BitConverter.ToSingle(arr, arr.Length - 4);
                    if (float.IsFinite(t) && t >= 0f && t < 7200f)
                    {
                        var br = new S3BitReader(arr);
                        if (br.TryReadPackedVector(10.0, 24, out double x, out double y, out double z))
                        {
                            double vx = 0, vy = 0, vz = 0;
                            br.TryReadPackedVector(10.0, 24, out vx, out vy, out vz);
                            if (Math.Abs(x) <= 250000 && Math.Abs(y) <= 250000 && Math.Abs(z) <= 100000)
                            {
                                _pawnStates.TryGetValue(channelIndex, out var st);
                                if (!S3SamplesByChannel.TryGetValue(channelIndex, out var list))
                                {
                                    list = new List<S3MoveSample>(256);
                                    S3SamplesByChannel[channelIndex] = list;
                                }
                                list.Add(new S3MoveSample(
                                    t, x, y, z, vx, vy, vz,
                                    st.IsSkydiving, st.IsDBNO, st.IsInStorm,
                                    st.IsTargeting, st.IsCrouched, st.IsSprinting, st.IsJumping));
                            }
                        }
                    }
                }
            }
            catch { }

            try { base.OnExternalDataRead(channelIndex, externalData); }
            catch { }
            return;
        }
        base.OnExternalDataRead(channelIndex, externalData);
    }

    public override void ReceivedPacket(FBitArchive bitReader)
    {
        if (bitReader.EngineNetworkVersion >= EngineNetworkVersionHistory.HISTORY_CHANNEL_NAMES)
        {
            base.ReceivedPacket(bitReader);
            return;
        }

        // UE 4.19 (Season 3 v3.5/v3.6, EngineNetworkVersion=5):
        // ChannelType is only serialized when (bReliable || bOpen); Unreal.Core 2.4.0 reads it unconditionally.
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
                bunch.CloseReason = bunch.bClose
                    ? (ChannelCloseReason)bitReader.ReadSerializedInt((int)ChannelCloseReason.MAX)
                    : ChannelCloseReason.Destroyed;
                bunch.bDormant = bunch.CloseReason == ChannelCloseReason.Dormancy;
            }
            bunch.bIsReplicationPaused = bitReader.ReadBit();
            bunch.bReliable = bitReader.ReadBit();
            bunch.ChIndex = bitReader.EngineNetworkVersion < EngineNetworkVersionHistory.HISTORY_MAX_ACTOR_CHANNELS_CUSTOMIZATION
                ? bitReader.ReadSerializedInt(10240)
                : bitReader.ReadIntPacked();
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

            if (bunch.bHasPackageMapExports)
                ReceiveNetGUIDBunch(bunch.Archive);

            if (bunch.ChIndex >= Channels.Length)
            {
                if (!bunch.bPartial) bitReader.RestoreTempEnd(FBitArchiveEndIndex.BUNCH);
                break;
            }
            if (Channels[bunch.ChIndex] == null)
                Channels[bunch.ChIndex] = new UChannel { ChannelIndex = bunch.ChIndex };

            try { ReceivedRawBunch(bunch); }
            catch { }
            finally
            {
                if (!bunch.bPartial) bitReader.RestoreTempEnd(FBitArchiveEndIndex.BUNCH);
            }
        }
    }

    protected override FArchive Decompress(FArchive archive)
    {
        if (!Replay.Info.IsCompressed)
        {
            return archive;
        }

        int decompressedSize = archive.ReadInt32();
        int compressedSize = archive.ReadInt32();
        byte[] compressedBuffer = archive.ReadBytes(compressedSize).ToArray();

        ReadOnlyMemory<byte> output;
        try
        {
            output = Oodle.DecompressReplayData(compressedBuffer, decompressedSize);
        }
        catch
        {
            if (!NativeOodle.IsLoaded) throw;
            output = NativeOodle.Decompress(compressedBuffer, decompressedSize);
        }

        return new Unreal.Core.BinaryReader(output)
        {
            EngineNetworkVersion = archive.EngineNetworkVersion,
            NetworkVersion = archive.NetworkVersion,
            ReplayHeaderFlags = archive.ReplayHeaderFlags,
            ReplayVersion = archive.ReplayVersion,
        };
    }
}

ref struct S3BitReader
{
    private readonly ReadOnlySpan<byte> _data;
    private int _pos;
    private readonly int _maxBits;

    public S3BitReader(ReadOnlySpan<byte> data)
    {
        _data = data;
        _pos = 0;
        _maxBits = (data.Length - 4) * 8; // reserve trailing float timestamp
    }

    private bool ReadBit(out int bit)
    {
        if (_pos >= _maxBits) { bit = 0; return false; }
        bit = (_data[_pos >> 3] >> (_pos & 7)) & 1;
        _pos++;
        return true;
    }

    private bool ReadSerializedInt(uint maxVal, out uint val)
    {
        val = 0;
        uint mask = 1;
        while (val + mask < maxVal)
        {
            if (!ReadBit(out int b)) return false;
            if (b != 0) val |= mask;
            mask <<= 1;
        }
        return true;
    }

    public bool TryReadPackedVector(double scale, uint maxBits, out double x, out double y, out double z)
    {
        x = y = z = 0;
        if (!ReadSerializedInt(maxBits, out uint bits)) return false;
        if (bits > 29) return false;
        uint bias = 1u << ((int)bits + 1);
        uint maxV = 1u << ((int)bits + 2);
        if (!ReadSerializedInt(maxV, out uint dx)) return false;
        if (!ReadSerializedInt(maxV, out uint dy)) return false;
        if (!ReadSerializedInt(maxV, out uint dz)) return false;
        x = ((double)dx - bias) / scale;
        y = ((double)dy - bias) / scale;
        z = ((double)dz - bias) / scale;
        return true;
    }
}

static class NativeOodle
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate long OodleLZDecompressDelegate(
        byte[] compBuf, long compBufSize,
        byte[] rawBuf, long rawLen,
        int fuzzSafe, int checkCRC, int verbosity,
        IntPtr decBufBase, long decBufSize,
        IntPtr fpCallback, IntPtr callbackUserData,
        IntPtr decoderMemory, long decoderMemorySize,
        int threadPhase);

    private static OodleLZDecompressDelegate? _decompress;
    public static bool IsLoaded => _decompress != null;

    public static void Init(string? explicitPath)
    {
        var candidates = new List<string>();
        if (!string.IsNullOrEmpty(explicitPath)) candidates.Add(explicitPath);

        // Try pc_build from server/settings.json
        foreach (string settingsPath in new[] { "server/settings.json", "../server/settings.json" })
        {
            try
            {
                if (File.Exists(settingsPath))
                {
                    using var doc = JsonDocument.Parse(File.ReadAllText(settingsPath));
                    if (doc.RootElement.TryGetProperty("pc_build", out var pcBuild))
                    {
                        string? root = pcBuild.GetString();
                        if (!string.IsNullOrEmpty(root))
                        {
                            candidates.Add(Path.Combine(root, "FortniteGame", "Binaries", "ThirdParty", "Oodle", "Win64", "oo2core_5_win64.dll"));
                            candidates.Add(Path.Combine(root, "Engine", "Binaries", "ThirdParty", "Oodle", "Win64", "oo2core_5_win64.dll"));
                        }
                    }
                }
            }
            catch { }
        }

        candidates.Add(Path.Combine(AppContext.BaseDirectory, "oo2core_5_win64.dll"));

        foreach (string path in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (!File.Exists(path)) continue;
            if (NativeLibrary.TryLoad(path, out IntPtr lib) &&
                NativeLibrary.TryGetExport(lib, "OodleLZ_Decompress", out IntPtr fn))
            {
                _decompress = Marshal.GetDelegateForFunctionPointer<OodleLZDecompressDelegate>(fn);
                Console.WriteLine($"[Oodle] Loaded native LZHLW decompressor: {path}");
                return;
            }
        }
        Console.WriteLine("[Oodle] Native oo2core_5_win64.dll not found (Chapter 1 LZHLW replays require it).");
    }

    public static byte[] Decompress(byte[] compressed, int decompressedSize)
    {
        if (_decompress == null) throw new InvalidOperationException("Native Oodle DLL not loaded.");
        byte[] output = new byte[decompressedSize];
        long res = _decompress(
            compressed, compressed.Length,
            output, decompressedSize,
            1, 0, 0,
            IntPtr.Zero, 0,
            IntPtr.Zero, IntPtr.Zero,
            IntPtr.Zero, 0,
            0);
        if (res != decompressedSize)
        {
            throw new Exception($"Native OodleLZ_Decompress returned {res} (expected {decompressedSize})");
        }
        return output;
    }
}
