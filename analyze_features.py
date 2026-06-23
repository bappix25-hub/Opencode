import json
import statistics

with open('telegram_tracked_tokens.json') as f:
    data = json.load(f)

all_tokens = list(data.values())
winners = [t for t in all_tokens if t.get('status') in ('winner', 'mega_winner') and t.get('ath_multiplier', 0) >= 5]
non_winners = [t for t in all_tokens if t.get('status') not in ('winner', 'mega_winner')]

print(f"Total: {len(all_tokens)}, Winners: {len(winners)}, Non-winners: {len(non_winners)}")

def feature_stats(tokens, label):
    liqs = [t.get('liq_usd', 0) for t in tokens if t.get('liq_usd', 0) > 0]
    holds = [t.get('holders', 0) for t in tokens if t.get('holders', 0) > 0]
    mcps = [t.get('mcp', 0) for t in tokens if t.get('mcp', 0) > 0]
    top10s = [t.get('top10_pct', 0) for t in tokens if t.get('top10_pct', 0) > 0]

    print(f"\n{label}:")
    if liqs:
        print(f"  Liq: median=${statistics.median(liqs):,.0f} mean=${statistics.mean(liqs):,.0f}")
    if holds:
        print(f"  Holders: median={statistics.median(holds):.0f} mean={statistics.mean(holds):.0f}")
    if mcps:
        print(f"  MCP: median=${statistics.median(mcps):,.0f}")
    if top10s:
        print(f"  Top10: median={statistics.median(top10s):.1f}%")

feature_stats(winners, "WINNERS (5x+)")
feature_stats(non_winners, "NON-WINNERS")

print("\n=== WINNER FEATURES AT SIGNAL TIME ===")
for t in winners[:10]:
    print(f"  {t.get('symbol','?'):15} Liq=${t.get('liq_usd',0):>8,.0f} Hold={t.get('holders',0):>3} Top10={t.get('top10_pct',0):>5.1f}% Sig={t.get('signal_type','?'):15} NoMint={t.get('no_mint',False)} Burnt={t.get('burnt',False)}")

print("\n=== CHANNEL COMBOS (from convergence data) ===")
try:
    with open('convergence_data.json') as f:
        conv = json.load(f)
    sightings = conv.get('sightings', {})
    print(f"Total convergence entries: {len(sightings)}")
    for addr, s in list(sightings.items())[:5]:
        print(f"  {s.get('symbol','?')} score={s.get('convergence_score',0):.0f} src={s.get('source_count',0)} ch={s.get('channel_count',0)} summary={s.get('source_summary','')}")
except Exception as e:
    print(f"Error: {e}")
