import argparse
from pathlib import Path

from backend.providers import MockDataProvider, encode_batch

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
parser.add_argument("--count", type=int, default=5000)
parser.add_argument("--frames", type=int, default=60)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open("w", encoding="utf-8") as output:
    for index, frame in enumerate(MockDataProvider(args.count).frames()):
        if index >= args.frames:
            break
        output.write(encode_batch(frame) + "\n")

