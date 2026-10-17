// NFR-1 (make bench): 20 virtual users for 60 s against the seeded compose stack (400 accounts).
// GET /api/v1/queue?limit=50 p95 < 300 ms; GET /api/v1/accounts/{id} p95 < 200 ms.
// scripts/bench.sh raises the API rate limit for the run (the limiter still runs on every request).
// Each request opens a connection, so the kernel spreads them over the API container's worker
// processes the way the ALB spreads requests over tasks in AWS. With keep-alive, every VU would stay
// on one worker for the whole run, a pinning the AWS layout does not have (docs/DECISIONS.md).
import http from "k6/http";
import { check } from "k6";

const API = __ENV.CC_API_URL || "http://localhost:8310";
const OIDC = __ENV.CC_OIDC_URL || "http://localhost:8345";

export const options = {
  noConnectionReuse: true,
  scenarios: {
    queue: {
      executor: "constant-vus",
      vus: 20,
      duration: "60s",
    },
  },
  thresholds: {
    "http_req_duration{route:queue}": ["p(95)<300"],
    "http_req_duration{route:account}": ["p(95)<200"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "max", "count"],
};

export function setup() {
  const token = http
    .post(`${OIDC}/dev/token`, JSON.stringify({ username: "ana.analyst" }), {
      headers: { "Content-Type": "application/json" },
      tags: { route: "setup" },
    })
    .json("access_token");
  const auth = { Authorization: `Bearer ${token}` };
  // Every account id, so the account route samples all 400 rather than the first page.
  const ids = [];
  let cursor = null;
  do {
    const url = `${API}/api/v1/queue?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`;
    const page = http.get(url, { headers: auth, tags: { route: "setup" } });
    if (page.status !== 200) throw new Error(`queue returned ${page.status}`);
    page.json("items").forEach((item) => ids.push(item.account_id));
    cursor = page.json("next_cursor");
  } while (cursor);
  if (ids.length < 400) throw new Error(`expected the seeded 400 accounts, got ${ids.length}`);
  return { auth, ids };
}

export default function (data) {
  const queue = http.get(`${API}/api/v1/queue?limit=50`, {
    headers: data.auth,
    tags: { route: "queue" },
  });
  check(queue, { "queue 200": (r) => r.status === 200 });
  const id = data.ids[Math.floor(Math.random() * data.ids.length)];
  const account = http.get(`${API}/api/v1/accounts/${id}`, {
    headers: data.auth,
    tags: { route: "account" },
  });
  check(account, { "account 200": (r) => r.status === 200 });
}
