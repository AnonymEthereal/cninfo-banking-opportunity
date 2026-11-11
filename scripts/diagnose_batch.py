import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.live import live_agents
from core.batch import analyze_one
from core.models import Announcement
from core.storage import atomic_write_json

if __name__ == '__main__':
    folder = Path('data/real_batch_acceptance/results/scan_5d060b88999543a78809')
    scan = json.loads((folder/'scan.json').read_text(encoding='utf-8'))
    failed = {e['announcement_id'] for e in scan['errors'] if e['code']=='AGENT_FAILED'}
    items = json.loads((folder/'announcements.json').read_text(encoding='utf-8'))['items']
    client, agents = live_agents()
    try:
        for item in [x for x in items if x['announcement_id'] in failed][:3]:
            a = Announcement.parse_obj(item)
            target = folder/'diagnostics'/a.announcement_id
            result = analyze_one(a, agents, target, 180)
            atomic_write_json(target/'result.json', result)
            print(a.announcement_id, result['status'], result.get('stage'),result.get('code'), flush=True)
    finally:
        client.close()
