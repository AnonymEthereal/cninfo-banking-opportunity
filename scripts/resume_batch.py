import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.scanner import ScanOrchestrator
from core.task_manager import TaskManager
from agents.live import live_agents

if __name__ == '__main__':
    runner = ScanOrchestrator(None, Path('data/real_batch_acceptance'))
    client, agents = live_agents()
    manager = TaskManager(runner)
    future = manager.submit(sys.argv[1], agents, resources=[client])
    try:
        result = future.result()
        print(result.scan_id, result.status, result.counts.dict(), flush=True)
    finally:
        manager.shutdown()
