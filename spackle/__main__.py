"""Command line for the Spackle Wireworld testbed.

  python -m spackle generate --seed 0 --out tasks.jsonl   build a suite
  python -m spackle show tasks.jsonl T2-s0-003 [--encoding coords]
  python -m spackle score tasks.jsonl T2-s0-003 answer.txt
  python -m spackle validate                               Gate 1 checks
  python -m spackle search --geoms XOR/a --seconds 60      extend best-known designs
"""

from __future__ import annotations

import argparse
import json
import sys

from . import generate, prompts, scoring, wireworld


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["validate"]:
        from . import validate
        return validate.main(argv[1:])
    if argv[:1] == ["search"]:
        from . import search
        search.main(argv[1:])
        return 0
    ap = argparse.ArgumentParser(prog="spackle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--t1", type=int, default=40)
    g.add_argument("--t2", type=int, default=40)
    g.add_argument("--out", required=True)
    s = sub.add_parser("show")
    s.add_argument("tasks")
    s.add_argument("id")
    s.add_argument("--encoding", choices=wireworld.ENCODINGS, default="ascii")
    c = sub.add_parser("score")
    c.add_argument("tasks")
    c.add_argument("id")
    c.add_argument("answer", help="file with the model's answer, or - for stdin")
    c.add_argument("--encoding", choices=wireworld.ENCODINGS, default="ascii")
    sub.add_parser("validate")
    sub.add_parser("search")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        tasks = generate.build_suite(args.seed, args.t1, args.t2)
        generate.dump_jsonl(tasks, args.out)
        print(f"wrote {len(tasks)} tasks to {args.out} (suite hash {generate.suite_hash(tasks)}, "
              f"prompt version {prompts.PROMPT_VERSION})")
        return 0
    tasks = {t["id"]: t for t in generate.load_jsonl(args.tasks)}
    if args.id not in tasks:
        print(f"unknown task id {args.id}", file=sys.stderr)
        return 2
    task = tasks[args.id]
    if args.cmd == "show":
        print(prompts.render(task, args.encoding))
        return 0
    text = sys.stdin.read() if args.answer == "-" else open(args.answer).read()
    result = scoring.score(task, text, args.encoding)
    print(result.pop("trace"))
    result.pop("grid")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
