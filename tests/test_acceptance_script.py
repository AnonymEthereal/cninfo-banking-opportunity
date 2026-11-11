import json
import subprocess
import sys

def test_acceptance_script():
    result = subprocess.run([sys.executable, "scripts/acceptance.py"], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["passed"] is True
