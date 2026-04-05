#!/bin/bash
# Close all open pinchtab tabs. Run via cron every 30 minutes.

LOG=/root/OpenClaude/logs/pinchtab-cleanup.log
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Discover Chrome CDP port dynamically
CDP_PORT=$(ss -tlnp 2>/dev/null | grep -oP '127\.0\.0\.1:\K\d+' | while read p; do
    curl -s --max-time 1 "http://localhost:$p/json/version" 2>/dev/null | grep -q Chrome && echo $p && break
done)

if [ -z "$CDP_PORT" ]; then
    log "ERROR: Chrome CDP port not found"; exit 1
fi

RESULT=$(curl -s "http://localhost:$CDP_PORT/json" | python3 - <<'PYEOF'
import json, sys, urllib.request, os
cdp = os.environ.get('CDP_PORT', '46373')
targets = json.load(sys.stdin)
pages = [t for t in targets if t.get('type') == 'page']
closed = 0
for t in pages:
    try:
        urllib.request.urlopen(f'http://localhost:{cdp}/json/close/{t["id"]}', timeout=3)
        closed += 1
    except: pass
print(f'{closed}/{len(pages)}')
PYEOF
)

PROCS=$(ps aux | grep -c '[c]hrome')
MEM=$(free -h | awk '/^Mem:/{print $3"/"$2}')
log "tabs closed: $RESULT | chrome procs: $PROCS | mem: $MEM"
