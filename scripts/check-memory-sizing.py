#!/usr/bin/env python3
"""Container memory limits are sized from Docker's memory, and a hand-set value wins.

WHY THIS EXISTS. With no limits, every service sized itself against the whole Docker
VM, and an ingest that had the app, the graph and docling busy together ran the VM
out of memory; the kernel killed the app JVM with nothing in its log (#111). The limits
only help if they add up to what Docker has, stay inside each service's minimum and
maximum, and never overrule something the operator wrote in .env.

    python3 scripts/check-memory-sizing.py
"""
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embabel_setup import dockerlib, memory  # noqa: E402

GIB = memory.GIB
SIZED = [memory.APP, memory.NEO4J, memory.DOCLING]
failures = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def budget(docker_memory: int) -> int:
    return docker_memory - memory.RESERVE_BYTES - sum(memory.MONITORING_LIMITS.values())


# The crash that prompted this: 7.65GB of Docker memory. Every byte of the budget is
# handed out, and every service lands between its minimum and maximum.
small = int(7.65 * GIB)
limits = memory.split(budget(small), SIZED)
check(abs(sum(limits.values()) - budget(small)) <= len(SIZED),
      f"7.65GB: limits {limits} do not add up to the budget {budget(small)}")
for share in SIZED:
    size = limits[share.var]
    check(share.minimum < size < share.maximum,
          f"7.65GB: {share.var}={size} outside ({share.minimum}, {share.maximum})")

# A large machine: everybody at their maximum, the rest left unallocated.
large = memory.split(budget(64 * GIB), SIZED)
check(all(large[s.var] == s.maximum for s in SIZED), f"64GB: not all at maximum: {large}")

# One service capped while the others are not: its excess goes to them.
capped = [memory.Share("A", GIB, 50, 2 * GIB), memory.Share("B", GIB, 50, 100 * GIB)]
spread = memory.split(10 * GIB, capped)
check(spread == {"A": 2 * GIB, "B": 8 * GIB}, f"a capped share's excess is not passed on: {spread}")

# Less than the minimums: the minimums, not a refusal and not a proportional shrink.
tight = memory.split(GIB, SIZED)
check(all(tight[s.var] == s.minimum for s in SIZED), f"below the minimums: {tight}")

# The in-memory engine has no graph container; its share is the app's.
check("NEO4J_MEM_LIMIT" not in memory.plan(small, "MEMORY"), "MEMORY engine was given a neo4j limit")
check(memory.plan(small, "MEMORY")["APP_MEM_LIMIT"] > memory.plan(small)["APP_MEM_LIMIT"],
      "MEMORY engine: the app did not absorb the graph's share")

# Neo4j's page cache is what its limit leaves after the heap and the process itself.
heap, pagecache = memory.neo4j_memory(4 * GIB)
check(heap + pagecache + memory.NEO4J_OVERHEAD == 4 * GIB, f"neo4j 4GB: heap {heap} + cache {pagecache}")


def resolved_with(settings: dict[str, str], docker_memory: int = small) -> dict[str, str]:
    with patch.object(memory, "chosen", side_effect=settings.get):
        return memory.resolve(memory.plan(docker_memory))


# The troubleshooting guide's advice, NEO4J_HEAP=1G, is kept, and the page cache is
# the remainder of the limit around it.
kept = resolved_with({"NEO4J_HEAP": "1G"})
limit = memory.parse_size(kept["NEO4J_MEM_LIMIT"])
check(kept["NEO4J_HEAP"] == "1G", f"hand-set NEO4J_HEAP was replaced: {kept['NEO4J_HEAP']}")
check(memory.parse_size(kept["NEO4J_PAGECACHE"]) == limit - GIB - memory.NEO4J_OVERHEAD,
      f"page cache is not the remainder around a hand-set heap: {kept}")

# A heap too big for the computed limit is the operator's wish: the limit grows.
grown = resolved_with({"NEO4J_HEAP": "6G"})
check(memory.parse_size(grown["NEO4J_MEM_LIMIT"]) >= 6 * GIB + memory.NEO4J_OVERHEAD,
      f"the limit does not hold a hand-set 6G heap: {grown}")

# Any limit set by hand passes through untouched.
check(resolved_with({"APP_MEM_LIMIT": "5g"})["APP_MEM_LIMIT"] == "5g", "hand-set APP_MEM_LIMIT was replaced")

# compose_env carries the limits, and asks docker once however often it is built.
memory.memory_env.cache_clear()
calls = []


def capacity():
    calls.append(1)
    return {"memory": small, "cpus": 4, "disk": 0, "disk_where": ""}


with patch("embabel_setup.capacity.docker_capacity", side_effect=capacity), \
        patch.object(memory, "chosen", return_value=None), \
        patch.object(dockerlib, "github_token", return_value=None):
    env = dockerlib.compose_env()
    dockerlib.compose_env()
check(all(var in env for var in ("APP_MEM_LIMIT", "NEO4J_MEM_LIMIT", "DOCLING_MEM_LIMIT")),
      "compose_env does not carry the memory limits")
check(len(calls) == 1, f"docker was asked for its memory {len(calls)} times")
memory.memory_env.cache_clear()

# Every limit the plan computes is read by a compose file; one nobody reads is a
# limit that silently does not apply.
compose_text = "".join(open(os.path.join(ROOT, f)).read()
                       for f in ("infra.yml", "docker-compose-worlds.yml", "docker-compose-me.yml"))
for var in memory.plan(small):
    check(f"${{{var}:-" in compose_text, f"no compose file reads {var}")

if failures:
    print("\n".join(f"FAIL: {f}" for f in failures))
    sys.exit(1)
print("ok: container memory limits are sized from Docker's memory, and hand-set values win")
