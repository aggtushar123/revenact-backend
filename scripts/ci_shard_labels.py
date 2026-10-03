#!/usr/bin/env python
"""Split the Django test suite into N balanced shards for parallel CI runners.

    python scripts/ci_shard_labels.py 1 4     # labels for shard 1 of 4, one per line
    python scripts/ci_shard_labels.py --check 4

Discovery is Django's own (DiscoverRunner.build_suite with no labels, the
same suite `manage.py test` runs), so a new test app or module is picked up
with no change here. The unit of a shard is a test class (``module.Class``):
Django's --parallel already splits work by class, and classes are small
enough to balance well. Classes are dealt largest first to the shard with the
fewest tests (ties go to the lower shard), so the split is deterministic.

--check rebuilds every shard from its own labels and fails unless the shards
are disjoint and their union is exactly the full discovered suite.
"""

import argparse
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _setup_django():
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()


def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _discover(labels=None):
    from django.test.runner import DiscoverRunner

    runner = DiscoverRunner(verbosity=0, interactive=False)
    tests = list(_iter_tests(runner.build_suite(labels or None)))
    broken = [t.id() for t in tests if type(t).__name__ in {"_FailedTest", "ModuleImportFailure"}]
    if broken:
        sys.exit("test discovery failed to import: " + ", ".join(broken))
    return tests


def _class_label(test):
    cls = type(test)
    return f"{cls.__module__}.{cls.__qualname__}"


def shard_labels(total):
    """Return a list of `total` sorted label lists covering the whole suite."""
    counts = Counter(_class_label(t) for t in _discover())
    shards = [[] for _ in range(total)]
    loads = [0] * total
    for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        i = min(range(total), key=lambda k: (loads[k], k))
        shards[i].append(label)
        loads[i] += n
    return [sorted(s) for s in shards]


def check(total):
    full = Counter(t.id() for t in _discover())
    seen = Counter()
    for i, labels in enumerate(shard_labels(total), start=1):
        ids = Counter(t.id() for t in _discover(labels))
        print(f"shard {i}/{total}: {len(labels)} classes, {sum(ids.values())} tests")
        seen += ids
    missing = set(full) - set(seen)
    extra = set(seen) - set(full)
    dupes = sorted(t for t, n in seen.items() if n > 1)
    print(f"full suite: {sum(full.values())} tests; shards together: {sum(seen.values())}")
    problems = [
        f"{name}: {len(ids)} e.g. {sorted(ids)[:3]}"
        for name, ids in (("missing", missing), ("extra", extra), ("duplicated", dupes))
        if ids
    ]
    if problems:
        sys.exit("shards do not cover the suite exactly once\n" + "\n".join(problems))
    print("OK: every test is in exactly one shard")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the shards cover the suite")
    parser.add_argument("index", nargs="?", type=int, help="1-based shard number")
    parser.add_argument("total", type=int, help="number of shards")
    args = parser.parse_args(argv)
    if args.total < 1:
        parser.error("total must be at least 1")
    _setup_django()
    if args.check:
        check(args.total)
        return
    if args.index is None or not 1 <= args.index <= args.total:
        parser.error("index must be between 1 and total")
    print("\n".join(shard_labels(args.total)[args.index - 1]))


if __name__ == "__main__":
    main()
