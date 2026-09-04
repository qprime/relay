# CAN contention scenario

Both producers see the same plant edge and publish in the same 10 ms scan. At
10,000 bit/s, a frame of `N` encoded bits occupies `N / 10` ms. The bus first
selects ID `0x080`; ID `0x300` waits for that complete frame before starting.
The consumer samples completed frames only at scan tops. Consequently the
the repeated Boolean publications leave a small deterministic queue: the
critical frame is visible 20 ms after publication in the good spec and 50 ms
after publication when the two IDs are swapped. The 30 ms `PRECEDES` budget
lies between those observable gaps.

The YAML files differ only in `System.name` and the two `can_id` values. Their
expectation artifacts pin the good all-pass verdict and the intentionally bad
priority's single `PRECEDES` failure.
