# Harness eval: replay mode

Measured on 2026-10-28 16:17. Latency covers harness + real MCP/Neo4j calls; model answers replayed; tokens are recorded.

| Metric | Value |
|---|---|
| Task success (outcome matched expectation) | 18/20 (90%) |
| Outcomes | failed 3, needs_approval 4, shipped 13 |
| Latency p50 / p95 | 3.6 s / 7.7 s |
| Tokens per task (mean, recorded) | 10738.5 |
| Failure causes | model 2 |
| Tool calls / retries / recovered / breaker opens | 278 / 5 / 2 / 1 |

| Task | Category | Outcome | Expected | Passed | Latency | Tokens | Notes |
|---|---|---|---|---|---|---|---|
| eval-01 | normal | shipped | shipped | yes | 6.9 s | 7676 |  |
| eval-02 | normal | shipped | shipped | yes | 7.3 s | 19758 |  |
| eval-03 | normal | shipped | shipped | yes | 3.0 s | 7569 |  |
| eval-04 | normal | shipped | shipped | yes | 3.8 s | 16728 |  |
| eval-05 | normal | shipped | shipped | yes | 3.6 s | 13403 |  |
| eval-06 | normal | shipped | shipped | yes | 6.8 s | 14621 |  |
| eval-07 | normal | needs_approval | shipped | no | 7.7 s | 16485 | status needs_approval, expected shipped |
| eval-08 | normal | needs_approval | shipped | no | 3.6 s | 14009 | status needs_approval, expected shipped |
| eval-09 | normal | shipped | shipped | yes | 2.7 s | 7405 |  |
| eval-10 | normal | shipped | shipped | yes | 2.4 s | 7400 |  |
| eval-11 | normal | shipped | shipped | yes | 7.7 s | 22839 |  |
| eval-12 | normal | shipped | shipped | yes | 2.5 s | 9496 |  |
| eval-13 | fault | shipped | shipped/needs_approval | yes | 4.9 s | 7600 |  |
| eval-14 | fault | shipped | shipped/needs_approval | yes | 5.5 s | 4995 |  |
| eval-15 | fault | failed | failed | yes | 2.6 s | 2681 | graph facts unavailable: calculate_skill_gap failed: {"code": "CIRCUIT_OPEN", "message": "calculate_skill_gap is failing |
| eval-16 | fault | shipped | shipped/needs_approval | yes | 3.5 s | 10740 |  |
| eval-17 | fault | failed | failed | yes | 0.8 s | 2666 | reasoner gave no usable answer (no JSON object in the answer): 'Sure! Here is the plan you asked for, let me know if you |
| eval-18 | fault | failed | failed | yes | 0.9 s | 1318 | graph facts unavailable: calculate_skill_gap failed: Error executing tool calculate_skill_gap: {"code": "NOT_FOUND", "me |
| eval-19 | fault | needs_approval | needs_approval | yes | 5.9 s | 17311 |  |
| eval-20 | fault | needs_approval | needs_approval | yes | 4.6 s | 10070 |  |
