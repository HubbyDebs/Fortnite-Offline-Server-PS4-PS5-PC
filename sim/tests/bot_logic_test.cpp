// Tests the player bots' hearing and low-health retreat (CoreAthenaBotAwareness.h) without the game.
// Build and run (Developer Command Prompt for Visual Studio 2022, from the project folder):
//   cl /nologo /std:c++20 /EHsc /I corev2\src\FortniteGame\Source\FortniteGame\Private\Athena\AI sim\tests\bot_logic_test.cpp /Fe:_local\bot_logic_test.exe && _local\bot_logic_test.exe
// corev2\src is CoreV2 with corev2.patch applied (node corev2/setup.mjs); /I can point at any CoreV2 working copy instead.
#include <cstdio>
#include "CoreAthenaBotAwareness.h"

namespace A = CoreAthenaBotAwareness;
static int Failures = 0;
static int Checks = 0;
#define CHECK(Cond) do { ++Checks; if (!(Cond)) { ++Failures; std::printf("FAILED line %d: %s\n", __LINE__, #Cond); } } while (0)

int main()
{
    // Loudness: a sniper carries furthest, a pistol least; an unknown weapon gets the middle.
    CHECK(A::ShotLoudness(5) > A::ShotLoudness(3) && A::ShotLoudness(3) > A::ShotLoudness(1));
    CHECK(A::ShotLoudness(0) == 3500.0f && A::ShotLoudness(42) == 3500.0f);
    // A player's weapon by name: guns carry, the rest is never a shot.
    CHECK(A::ShotLoudnessOfName(L"WID_Assault_Auto_Athena_R_Ore_T03") == A::ShotLoudness(3));
    CHECK(A::ShotLoudnessOfName(L"WID_Shotgun_Standard_Athena_UC_Ore_T03") == A::ShotLoudness(4));
    CHECK(A::ShotLoudnessOfName(L"WID_Pistol_AutoHeavy_Athena_R_Ore_T03") == A::ShotLoudness(2));
    CHECK(A::ShotLoudnessOfName(L"WID_Pistol_SemiAuto_Athena_C_Ore_T02") == A::ShotLoudness(1));
    CHECK(A::ShotLoudnessOfName(L"WID_Sniper_BoltAction_Scope_Athena_R_Ore_T03") == A::ShotLoudness(5));
    CHECK(A::ShotLoudnessOfName(L"WID_Launcher_Rocket_Athena_R_Ore_T03") == A::ShotLoudness(6));
    for (const wchar_t* NotAGun : { L"WID_Harvest_Pickaxe_Athena_C_T01", L"BuildingItemData_Wall", L"EditTool", L"Athena_Bandage", L"TID_Floor_Spikes_Athena_R_T03", L"" })
    {
        CHECK(A::ShotLoudnessOfName(NotAGun) == 0.0f);
    }
    // Hearing: 75 % at Awareness 0, 110 % at 1, clamped.
    CHECK(A::HearingScale(0.0f) == 0.75f && A::HearingScale(1.0f) > 1.09f && A::HearingScale(1.0f) < 1.11f);
    CHECK(A::HearingScale(-3.0f) == 0.75f && A::HearingScale(7.0f) == A::HearingScale(1.0f));

    // Going to check shots: closer and more aggressive is likelier; one gun halves it; always between 5 % and 95 %.
    const float Close = A::CheckShotsChance(500.0f, 4000.0f, 0.5f, 2);
    const float Far = A::CheckShotsChance(3900.0f, 4000.0f, 0.5f, 2);
    CHECK(Close > Far);
    CHECK(A::CheckShotsChance(2000.0f, 4000.0f, 1.0f, 2) > A::CheckShotsChance(2000.0f, 4000.0f, 0.0f, 2));
    CHECK(A::CheckShotsChance(2000.0f, 4000.0f, 0.5f, 1) * 2.0f == A::CheckShotsChance(2000.0f, 4000.0f, 0.5f, 2));
    for (float Distance : { 0.0f, 1000.0f, 4000.0f, 9000.0f })
    {
        for (float Aggression : { 0.0f, 0.5f, 1.0f })
        {
            const float Chance = A::CheckShotsChance(Distance, 4000.0f, Aggression, 2);
            CHECK(Chance >= 0.1f && Chance <= 0.95f);
            CHECK(A::CheckShotsChance(Distance, 4000.0f, Aggression, 0) >= 0.05f);
        }
    }
    CHECK(A::CheckShotsChance(100.0f, 0.0f, 0.5f, 2) > 0.0f); // no division by zero

    // Alert: quicker, never under 0.12 s; not alert: unchanged.
    CHECK(A::AlertedReaction(0.6f, false) == 0.6f);
    CHECK(A::AlertedReaction(0.6f, true) < 0.6f && A::AlertedReaction(0.6f, true) > 0.35f);
    CHECK(A::AlertedReaction(0.1f, true) == 0.12f);

    // Retreat thresholds by the Healing trait.
    CHECK(A::RetreatBelow(0.0f) == 30.0f && A::RetreatBelow(1.0f) == 50.0f && A::RetreatRecovered(0.5f) == 70.0f);

    A::FRetreatSituation S;
    S.Vitals = 30.0f; S.Healing = 0.5f; S.bHasHeals = true; S.bUnderFire = true; S.bEnemyKnown = true;
    S.bEnemyInSight = true; S.EnemyDistance = 1500.0f; S.EnemyVitals = 150.0f;
    CHECK(A::ShouldStartRetreat(S));                         // low, shot at from 15 m by a healthier enemy
    { auto T = S; T.bHasHeals = false; CHECK(!A::ShouldStartRetreat(T)); }      // nothing to heal with: fights
    { auto T = S; T.Vitals = 60.0f; CHECK(!A::ShouldStartRetreat(T)); }         // not low enough
    { auto T = S; T.EnemyDistance = 300.0f; CHECK(!A::ShouldStartRetreat(T)); } // cornered: fights
    { auto T = S; T.EnemyVitals = 10.0f; CHECK(!A::ShouldStartRetreat(T)); }    // weaker enemy: finishes it
    { auto T = S; T.bStorm = true; CHECK(!A::ShouldStartRetreat(T)); }          // the storm comes first
    { auto T = S; T.bResting = true; CHECK(!A::ShouldStartRetreat(T)); }        // just broke off: fights a while
    { auto T = S; T.bUnderFire = false; CHECK(!A::ShouldStartRetreat(T)); }     // nobody shooting: heals normally
    { auto T = S; T.bEnemyInSight = false; T.EnemyVitals = -1.0f; T.EnemyDistance = 1e9f; CHECK(A::ShouldStartRetreat(T)); } // hit from out of sight
    { auto T = S; T.Healing = 0.0f; T.Vitals = 35.0f; CHECK(!A::ShouldStartRetreat(T)); } // a low-Healing bot holds on longer
    { auto T = S; T.Healing = 1.0f; T.Vitals = 45.0f; CHECK(A::ShouldStartRetreat(T)); }

    std::printf("%d checks, %d failed\n", Checks, Failures);
    return Failures == 0 ? 0 : 1;
}
