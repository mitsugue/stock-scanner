# Checkpoint V2 mapping attribution

The v13.4.3 acceptance gate uses mapping categories, allocator bytes, Python
reachability, RSS/PSS and cgroup limits. It intentionally does not fail on the
raw total number of `/proc/self/maps` records alone. Linux may split or coalesce
anonymous allocator mappings without changing application ownership.

## Exact 32-cycle evidence

The isolated natural CI run used one Python 3.12 process, a 4 GiB cgroup, the
exact public production-shaped snapshot (47 sections, 45,203 rows,
158,736,384 generation bytes), retention 4 and no process reset.

- all 32 writes and the final full restore verified;
- pending generations returned to 0 and retained generations stayed at 4;
- active/retained SQLite, V2 temp, deleted, incident and unknown mappings were
  0 after return;
- connection, cursor, future, generation-context, thread and descriptor growth
  was 0;
- raw-payload telemetry owners and reachable large tracked containers were 0;
- steady total mappings fluctuated from 275 to 308 and ended at 305;
- allocator-large anonymous mappings fluctuated from 38 to 71 and ended at 68;
- final allocator anonymous RSS was 199,614,464 bytes;
- cycles 27-32 stayed in a 9,445,376-byte RSS band and a 3-record mapping band;
- steady RSS/PSS growth was 120,922,112 bytes, below the existing 128 MiB gate;
- cgroup peak was 1,400,467,456 bytes and disk free was 92,691,406,848 bytes.

The surviving anonymous mappings are predominantly 1 MiB or merged multiples
of 1 MiB created by the Python process with `MAP_PRIVATE|MAP_ANONYMOUS`. This
matches CPython arena-sized allocator retention. `mallinfo2` remained bounded
(14,667,776 system bytes and 7,396,672 in-use bytes at cycle 32), while syscall
tracing recorded 1,791 mmap calls, 1,601 munmap calls and no generation-file
mapping survivor. This separates Python allocator arena retention from glibc
arena growth and from an application-owned generation leak.

## Controlled trim comparison

The four-cycle tracemalloc comparison used identical data and topology.

| Variant | final RSS | allocator system bytes | in-use bytes | final maps |
| --- | ---: | ---: | ---: | ---: |
| pre-fix, no trim | 810,917,888 | 713,650,176 | 13,668,880 | 271 |
| candidate, no trim | 643,084,288 | 546,111,488 | 13,781,888 | 274 |
| candidate, trim | 120,811,520 | 511,258,624 | 13,846,512 | 282 |

Application ownership is released before trim. Trim materially reduces
resident free pages, the 32-cycle invocation remains inside the resource
envelope, and live/in-use and Python reachability do not grow. Therefore trim
is retained; it is not used as evidence that live objects were released.

## Precise gate

The gate keeps exact zero checks for generation, temp, deleted, incident and
unknown mappings. It also checks reachability/resource growth, a 256 MiB
anonymous allocator ceiling, 4 glibc arena mappings, 96 allocator-large mapping
records, bounded mapping/category bands, a 32 MiB final-six-cycle plateau band,
the existing 128 MiB RSS/PSS envelope and cgroup peak below 3 GiB.

These limits include measured headroom over the 32-cycle maxima. Removing raw
map-count checks entirely would lose useful leak detection; using total map
count alone was over-broad because it could not distinguish allocator arena
reuse from a surviving generation-owned resource.

## v13.5.64 — normal-use snapshot evidence and the allocator bound split

**Why the gate started failing (2026-09-07).** The exact source the gate
restores is the live `/api/argus/osint/memory-snapshot`. Right after a
redeploy it is a few KB; once the owner has opened Holdings it carries the
asset chart reports (22 records for 11 symbols) and weighs 10,698,752
generation bytes (50 sections, 54 rows). Every cycle restores and verifies
that whole document — the recovered data range is the full production
memory snapshot including the chart reports. Nothing is excluded.

**Measured (run 34167048143, attempt 2, `candidate_with_trim`, 32 cycles):**

| metric | baseline | quiet, last cycle | bound before | bound after |
|---|---|---|---|---|
| glibc main-arena system bytes | 9,494,528 | 41,861,120 | 32 MiB absolute → **fail** | 32 MiB + 4 B/source byte = 74,349,824 |
| in-use bytes (uordblks + hblkhd) | 6,295,904 | 7,505,040 | — | 32 MiB (new, strict) |
| free-but-unreturned chunks | 3,198,624 | 34,356,080 | — | covered by the growth bound |
| system growth over 30 steady cycles | — | 1,925,120 | 16 MiB | 16 MiB (unchanged) |
| allocator anonymous RSS | — | 119,926,784 | 256 MiB | 256 MiB (unchanged) |
| all writes / final restore verified | — | yes | required | required |

**What changed and why.** The old absolute rule read the arena size as
application retention. With a snapshot made of small nested objects the
generation's objects are freed each cycle, but glibc keeps ~34 MB of free
chunks it cannot return (`malloc_trim` only releases the top of the arena),
so the arena stays at ~42 MB without growing. The gate now (1) bounds what the
application still holds — in-use bytes, 32 MiB, strict — and (2) bounds the
arena relative to the source it restores; the growth, plateau, mapping,
reachability and cgroup rules are unchanged. Reports from an older probe (no
in-use samples) are still judged by the old absolute rule.

**Recovery guarantee.** Unchanged: 32 verified writes, one verified full
restore of the normal-use snapshot, zero surviving generation/temp/deleted
mappings, bounded mapping and RSS bands, and a 4 GiB cgroup with a 3 GiB peak
ceiling. The proof is stronger than before because the restored document is
the real one.
