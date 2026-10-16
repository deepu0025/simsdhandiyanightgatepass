#!/usr/bin/env python3
"""Read-only scanner-validation load test for a dedicated non-production target."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass


SCENARIOS = {
    "baseline": {"clients": 4, "minutes": 5},
    "normal": {"clients": 20, "minutes": 10},
    "peak": {"clients": 50, "minutes": 10},
    "stress": {"clients": 100, "minutes": 10},
    "soak": {"clients": 20, "minutes": 60},
    "burst": {"clients": 100, "minutes": 2},
    "recovery": {"clients": 20, "minutes": 10},
}
KNOWN_PRODUCTION_HOST = "simsdhandiyanightgatepass.onrender.com"
MAX_BODY_BYTES = 16_384
MAX_RESPONSE_BYTES = 65_536


@dataclass
class Result:
    status: int | None
    outcome: str
    latency_ms: float


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class ApiClient:
    def __init__(self, base_url: str, email: str, password: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            NoRedirectHandler(),
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        csrf_status, csrf = self._request("GET", "/api/csrf/")
        if csrf_status != 200:
            raise RuntimeError(f"CSRF initialization failed with HTTP {csrf_status}.")
        token = csrf.get("csrfToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("CSRF initialization returned no token.")
        login_status, _ = self._request(
            "POST",
            "/api/auth/login/",
            {"email": email, "password": password},
            token,
        )
        if not 200 <= login_status < 300:
            raise RuntimeError(f"Scanner login failed with HTTP {login_status}.")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        csrf_token: str | None = None,
    ) -> tuple[int, dict]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("Request body exceeds the configured safety limit.")
            headers["Content-Type"] = "application/json"
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Response exceeded the configured safety limit.")
                return response.status, json.loads(raw or b"{}")
        except urllib.error.HTTPError as error:
            raw = error.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Error response exceeded the configured safety limit.") from error
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            return error.code, payload

    def validate(self, ticket_number: str) -> Result:
        started = time.perf_counter()
        try:
            status, payload = self._request(
                "POST",
                "/api/scanner/validate/",
                {"credential": ticket_number, "method": "QR"},
            )
            outcome = str(payload.get("result", "UNKNOWN"))
            return Result(status, outcome, (time.perf_counter() - started) * 1000)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            return Result(None, "TRANSPORT_ERROR", (time.perf_counter() - started) * 1000)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 2)


def load_accounts() -> list[tuple[str, str]]:
    raw = os.environ.get("QA_SCANNER_ACCOUNTS_JSON", "")
    if not raw:
        raise SystemExit(
            "Set QA_SCANNER_ACCOUNTS_JSON to a JSON list of dedicated test scanner "
            "accounts; credentials are read only from the environment."
        )
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit("QA_SCANNER_ACCOUNTS_JSON is not valid JSON.") from error
    if not isinstance(entries, list) or not entries:
        raise SystemExit("QA_SCANNER_ACCOUNTS_JSON must be a non-empty JSON list.")
    accounts = []
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key]
            for key in ("email", "password")
        ):
            raise SystemExit("Every test scanner entry needs email and password strings.")
        accounts.append((entry["email"], entry["password"]))
    return accounts


def load_ticket_ids() -> list[str]:
    values = [
        ticket.strip()
        for ticket in os.environ.get("QA_VALID_TICKET_IDS", "").split(",")
        if ticket.strip()
    ]
    if not values:
        raise SystemExit(
            "Set QA_VALID_TICKET_IDS to unused, issued ticket numbers from the "
            "dedicated test database."
        )
    return values


def verify_target(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("QA_BASE_URL must be an absolute http(s) URL.")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise SystemExit("QA_BASE_URL must not contain user info, a query, or a fragment.")
    if parsed.hostname.lower() == KNOWN_PRODUCTION_HOST:
        raise SystemExit("Refusing to run against the configured production host.")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.hostname.lower() not in local_hosts:
        if parsed.scheme != "https":
            raise SystemExit("Remote test targets must use HTTPS.")
        if os.environ.get("QA_ALLOW_REMOTE_TEST_TARGET") != "YES":
            raise SystemExit(
                "Remote targets require QA_ALLOW_REMOTE_TEST_TARGET=YES after "
                "confirming this is a dedicated non-production environment."
            )
        if os.environ.get("QA_TEST_RUN_ID", "").strip() == "":
            raise SystemExit("Set QA_TEST_RUN_ID to identify this isolated test run.")


def run_worker(
    index: int,
    account: tuple[str, str],
    ticket_ids: list[str],
    base_url: str,
    timeout: float,
    start_at: float,
    stop_at: float,
    interval: float,
) -> list[Result]:
    client = ApiClient(base_url, *account, timeout=timeout)
    results: list[Result] = []
    request_index = index
    while time.monotonic() < stop_at:
        wait = start_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        ticket = ticket_ids[request_index % len(ticket_ids)]
        results.append(client.validate(ticket))
        request_index += 1
        if interval > 0:
            remaining = stop_at - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="baseline")
    parser.add_argument("--clients", type=int)
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=0,
        help="Target aggregate rate; zero runs clients as fast as responses allow.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=5)
    args = parser.parse_args()

    if os.environ.get("QA_TEST_TARGET") != "YES":
        raise SystemExit("Set QA_TEST_TARGET=YES to confirm this is a test environment.")
    base_url = os.environ.get("QA_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("Set QA_BASE_URL to the dedicated non-production API origin.")
    verify_target(base_url)
    clients = (
        SCENARIOS[args.scenario]["clients"]
        if args.clients is None
        else args.clients
    )
    duration = (
        SCENARIOS[args.scenario]["minutes"] * 60
        if args.duration_seconds is None
        else args.duration_seconds
    )
    if not 1 <= clients <= 200:
        raise SystemExit("--clients must be between 1 and 200.")
    if not 1 <= duration <= 86_400:
        raise SystemExit("--duration-seconds must be between 1 and 86400.")
    if (
        not math.isfinite(args.timeout_seconds)
        or not math.isfinite(args.requests_per_second)
        or args.timeout_seconds <= 0
        or args.requests_per_second < 0
    ):
        raise SystemExit(
            "Timeout must be finite and positive; request rate must be finite and non-negative."
        )

    accounts = load_accounts()
    ticket_ids = load_ticket_ids()
    for account in accounts:
        ApiClient(base_url, *account, timeout=args.timeout_seconds)
    for index, ticket in enumerate(ticket_ids):
        status, payload = ApiClient(
            base_url,
            *accounts[index % len(accounts)],
            timeout=args.timeout_seconds,
        )._request("POST", "/api/scanner/validate/", {"credential": ticket, "method": "QR"})
        if status != 200 or payload.get("result") != "VALID":
            raise SystemExit(
                f"Preflight failed for a supplied ticket (HTTP {status}, "
                f"result {payload.get('result', 'UNKNOWN')}); no load was started."
            )

    start_at = time.monotonic() + 1
    stop_at = start_at + duration
    interval = (
        clients / args.requests_per_second
        if args.requests_per_second > 0
        else 0
    )
    results: list[Result] = []
    errors: list[str] = []
    result_lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            worker_results = run_worker(
                index,
                accounts[index % len(accounts)],
                ticket_ids,
                base_url,
                args.timeout_seconds,
                start_at,
                stop_at,
                interval,
            )
            with result_lock:
                results.extend(worker_results)
        except Exception as error:  # noqa: BLE001 - collect worker failure as test output
            with result_lock:
                errors.append(type(error).__name__)

    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [executor.submit(worker, index) for index in range(clients)]
        concurrent.futures.wait(futures)

    latencies = [item.latency_ms for item in results]
    outcomes = Counter(item.outcome for item in results)
    statuses = Counter(str(item.status) for item in results)
    request_count = len(results)
    summary = {
        "scenario": args.scenario,
        "target_host": urllib.parse.urlparse(base_url).hostname,
        "clients": clients,
        "configured_duration_seconds": duration,
        "configured_requests_per_second": args.requests_per_second or None,
        "scanner_accounts": len(accounts),
        "valid_test_tickets": len(ticket_ids),
        "endpoint": "POST /api/scanner/validate/ (QR method; no check-in mutation)",
        "request_count": request_count,
        "requests_per_second_observed": round(request_count / duration, 3),
        "outcomes": dict(outcomes),
        "http_statuses": dict(statuses),
        "transport_or_worker_errors": outcomes.get("TRANSPORT_ERROR", 0) + len(errors),
        "worker_errors_by_type": dict(Counter(errors)),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2) if latencies else None,
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return int(not request_count or outcomes.get("VALID", 0) != request_count or bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
