# Quick comparison of simulator runs: hit rate (per gun and range band), still %, kills, deaths, chests, breaks and
# kill distances by weapon (ml/analyze_fights.py).  python -I sim/runstats.py <label> [<label> ...]  (labels as in --label)
import sys,re,json,glob,os,subprocess
root=r'C:\Users\jj\Fortnite-Offline-Server-PS4-PS5-PC'
for label in sys.argv[1:]:
    r=sorted(glob.glob(os.path.join(root,'_local','sim','*-'+label)))[-1]
    h=s=0; bands={}
    for f in glob.glob(os.path.join(r,'match-*.server.log')):
        last=[l for l in open(f,encoding='utf-8',errors='replace') if 'hit rates after' in l]
        if not last: continue
        for name,a,b in re.findall(r'([A-Za-z ]+?) (?:\d+)% \((\d+)/(\d+)\)',last[-1]):
            h+=int(a); s+=int(b); k=name.strip(); x=bands.setdefault(k,[0,0]); x[0]+=int(a); x[1]+=int(b)
    m=json.load(open(os.path.join(r,'summary.json'))); m=m.get('metrics',m)
    print(f"== {label}: hits {h}/{s} = {100*h/max(s,1):.1f}%  still {m.get('still%')} kills/bot {m.get('killsPerBot')} shots/bot {m.get('shotsPerBot')} storm {m.get('stormDeaths%')} fall {m.get('fallDeaths%')} retreats {m.get('retreatsPerBot')} chests {m.get('chestsPerBot')} breaks {m.get('breakThroughsPerBot')}")
    print('   ', ', '.join(f"{k} {100*v[0]/v[1]:.0f}% ({v[1]})" for k,v in sorted(bands.items()) if v[1]>=10))
    out=subprocess.run([sys.executable,'-I',os.path.join(root,'ml','analyze_fights.py'),r],capture_output=True,text=True).stdout.splitlines()[1:6]
    print('   kills: '+' | '.join(' '.join(l.split()[:1]+l.split()[1:2]+['m='+l.split()[3] if len(l.split())>3 else '']) for l in out))
