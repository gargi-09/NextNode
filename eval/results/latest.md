# Harness eval: replay mode

Measured on 2026-10-28 01:02. Latency covers harness + real MCP/Neo4j calls; model answers replayed; tokens are recorded.

| Metric | Value |
|---|---|
| Task success (outcome matched expectation) | 15/20 (75%) |
| Outcomes | failed 6, needs_approval 2, shipped 12 |
| Latency p50 / p95 | 4.6 s / 10.8 s |
| Tokens per task (mean, recorded) | 11533.6 |
| Failure causes | model 2, retrieval 3 |
| Tool calls / retries / recovered / breaker opens | 194 / 7 / 2 / 0 |

| Task | Category | Outcome | Expected | Passed | Latency | Tokens | Notes |
|---|---|---|---|---|---|---|---|
| eval-01 | normal | shipped | shipped | yes | 3.7 s | 12812 |  |
| eval-02 | normal | shipped | shipped | yes | 5.6 s | 12797 |  |
| eval-03 | normal | shipped | shipped | yes | 7.8 s | 9292 |  |
| eval-04 | normal | shipped | shipped | yes | 8.7 s | 22319 |  |
| eval-05 | normal | shipped | shipped | yes | 6.8 s | 19643 |  |
| eval-06 | normal | shipped | shipped | yes | 4.6 s | 19197 |  |
| eval-07 | normal | needs_approval | shipped | no | 16.8 s | 31631 | status needs_approval, expected shipped |
| eval-08 | normal | shipped | shipped | yes | 6.2 s | 15192 |  |
| eval-09 | normal | shipped | shipped | yes | 2.6 s | 8922 |  |
| eval-10 | normal | shipped | shipped | yes | 2.6 s | 9172 |  |
| eval-11 | normal | failed | shipped | no | 2.5 s | 7176 | status failed, expected shipped |
| eval-12 | normal | shipped | shipped | yes | 10.8 s | 11279 |  |
| eval-13 | fault | shipped | shipped/needs_approval | yes | 10.1 s | 8428 |  |
| eval-14 | fault | shipped | shipped/needs_approval | yes | 5.5 s | 10846 |  |
| eval-15 | fault | failed | failed | no | 4.7 s | 4800 | 0 breaker open(s), expected >= 1 |
| eval-16 | fault | failed | shipped/needs_approval | no | 2.2 s | 7087 | status failed, expected shipped or needs_approval |
| eval-17 | fault | failed | failed | yes | 0.0 s | 4597 | reasoner gave no usable answer (no JSON object in the answer): 'Sure! Here is the plan you asked for, let me know if you |
| eval-18 | fault | failed | failed | yes | 0.7 s | 6653 | graph facts unavailable: calculate_skill_gap failed: Error executing tool calculate_skill_gap: {"code": "NOT_FOUND", "me |
| eval-19 | fault | failed | needs_approval | no | 0.0 s | 0 | status failed, expected needs_approval; rollback False, expected True |
| eval-20 | fault | needs_approval | needs_approval | yes | 4.2 s | 8830 |  |
