import json

from backend.app import run_once
from backend.sample import get_sample_email


if __name__ == "__main__":
    sample = get_sample_email()
    print(json.dumps(run_once(sample), indent=2))
