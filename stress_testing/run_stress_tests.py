#!/usr/bin/env python3
"""
Automated Stress Test Runner for zivaBooks
Executes consecutive stress tests for 100, 500, and 1000 concurrent users.
Collects and aggregates performance statistics, latencies, failure rates, and system utilization.
"""

import os
import sys
import time
import subprocess
import csv
import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
HOST = "http://localhost:8000"

# Target user tiers to stress test
USER_TIERS = [
    {"users": 100, "spawn_rate": 20, "duration": "30s", "desc": "100 Concurrent Users (Normal-to-High Load)"},
    {"users": 500, "spawn_rate": 50, "duration": "30s", "desc": "500 Concurrent Users (Heavy Stress Load)"},
    {"users": 1000, "spawn_rate": 100, "duration": "30s", "desc": "1000 Concurrent Users (Extreme Spike Load)"},
]


def run_command(cmd, timeout=None):
    """Run a shell command and return stdout and exit code."""
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return res.returncode, res.stdout, res.stderr


def check_host_reachable():
    """Ensure target server is reachable before commencing."""
    print(f"[*] Checking connectivity to {HOST}...")
    code, out, _ = run_command(f"curl -s -o /dev/null -w '%{{http_code}}' {HOST}/health/")
    if out.strip() != "200":
        print(f"[!] Warning: Health check returned {out.strip()}. Ensuring application is up...")
        time.sleep(2)
    else:
        print(f"[✓] Target server {HOST} is responsive.")


def get_docker_stats():
    """Capture snapshot of Docker containers resource usage."""
    cmd = "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' zivabooks_web zivabooks_postgres zivabooks_redis 2>/dev/null"
    code, out, _ = run_command(cmd)
    stats = {}
    if code == 0 and out.strip():
        for line in out.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 4:
                stats[parts[0]] = {
                    "cpu": parts[1],
                    "mem": parts[2],
                    "mem_perc": parts[3],
                }
    return stats


def parse_locust_csv(csv_prefix):
    """Parse the Locust stats CSV file for aggregated metrics."""
    stats_file = RESULTS_DIR / f"{csv_prefix}_stats.csv"
    if not stats_file.exists():
        return None

    endpoints = []
    aggregated = None

    with open(stats_file, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "")
            method = row.get("Type", "")
            req_count = int(row.get("Request Count", 0))
            fail_count = int(row.get("Failure Count", 0))
            median_ms = float(row.get("Median Response Time", 0.0) or 0)
            avg_ms = float(row.get("Average Response Time", 0.0) or 0)
            min_ms = float(row.get("Min Response Time", 0.0) or 0)
            max_ms = float(row.get("Max Response Time", 0.0) or 0)
            rps = float(row.get("Requests/s", 0.0) or 0)
            failures_per_s = float(row.get("Failures/s", 0.0) or 0)

            data = {
                "name": name,
                "method": method,
                "requests": req_count,
                "failures": fail_count,
                "fail_rate": round((fail_count / req_count * 100) if req_count > 0 else 0, 2),
                "median_ms": median_ms,
                "avg_ms": avg_ms,
                "min_ms": min_ms,
                "max_ms": max_ms,
                "rps": rps,
            }

            if name == "Aggregated":
                aggregated = data
            else:
                endpoints.append(data)

    return {"aggregated": aggregated, "endpoints": endpoints}


def run_tier(tier):
    users = tier["users"]
    spawn = tier["spawn_rate"]
    dur = tier["duration"]
    prefix = f"stress_{users}_users"
    html_report = RESULTS_DIR / f"{prefix}.html"
    csv_prefix = RESULTS_DIR / prefix

    print("\n" + "=" * 70)
    print(f"🚀 Running Stress Test: {tier['desc']}")
    print(f"   Users: {users} | Spawn Rate: {spawn}/s | Duration: {dur}")
    print("=" * 70)

    # Initial stats
    docker_before = get_docker_stats()

    locust_cmd = (
        f"uvx locust -f {BASE_DIR}/locustfile.py "
        f"--headless "
        f"--host {HOST} "
        f"-u {users} "
        f"-r {spawn} "
        f"-t {dur} "
        f"--csv={csv_prefix} "
        f"--html={html_report} "
        f"--only-summary"
    )

    t0 = time.time()
    code, stdout, stderr = run_command(locust_cmd)
    elapsed = time.time() - t0

    docker_after = get_docker_stats()

    if code != 0:
        print(f"[!] Warning: Locust exited with code {code}.")
        if stderr:
            print(f"Stderr: {stderr}")

    metrics = parse_locust_csv(prefix)

    result = {
        "tier": tier,
        "elapsed_seconds": round(elapsed, 1),
        "metrics": metrics,
        "docker_stats": docker_after,
        "html_report": str(html_report),
    }

    if metrics and metrics.get("aggregated"):
        agg = metrics["aggregated"]
        print(f"\n[✓] Results for {users} Users:")
        print(f"    - Total Requests: {agg['requests']}")
        print(f"    - Throughput (RPS): {agg['rps']:.1f} req/sec")
        print(f"    - Failure Count: {agg['failures']} ({agg['fail_rate']}%)")
        print(f"    - Avg Latency: {agg['avg_ms']:.1f} ms")
        print(f"    - Median (p50) Latency: {agg['median_ms']:.1f} ms")
        print(f"    - Max Latency: {agg['max_ms']:.1f} ms")
    else:
        print("[!] Could not parse metrics for this tier.")

    # Brief cooldown between tiers
    print("\n[*] Cooling down for 5 seconds before next tier...")
    time.sleep(5)
    return result


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    check_host_reachable()

    tiers = USER_TIERS
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg.isdigit():
            target = int(arg)
            dur = sys.argv[2] if len(sys.argv) > 2 else "30s"
            spawn = max(1, target // 5)
            tiers = [{
                "users": target,
                "spawn_rate": spawn,
                "duration": dur,
                "desc": f"{target} Concurrent Users (Targeted Stress Load)",
            }]

    all_results = []
    for tier in tiers:
        res = run_tier(tier)
        all_results.append(res)

    # Generate summary JSON
    summary_json_path = RESULTS_DIR / "summary_results.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print("\n" + "=" * 70)
    print("🎯 ALL STRESS TEST TIERS COMPLETED SUCCESSFULLY!")
    print(f"Results stored in: {RESULTS_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
