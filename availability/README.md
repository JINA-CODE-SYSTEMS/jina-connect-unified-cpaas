# Availability reporting (partner agreement Cl. 5.4)

The agreement commits to a **99.5%** availability figure, reported monthly.
Four pieces produce it:

| Piece | Where | Runs |
| --- | --- | --- |
| Synthetic checks | Grafana Cloud (external) | every 2 min |
| `aggregate_availability` | this app | 01:30 daily, via django-crontab |
| `build_availability_report` | `services/monthly_report.py` | on demand |
| `send_availability_report` | this app | 06:30 on the 1st, via django-crontab |

Grafana is the instrument; `DailyAvailability` is the record. The counts are
copied locally because Grafana Cloud's free tier retains metrics for **14
days** and a calendar-month report needs 31 — and because a contractual figure
that may have to be defended in eighteen months should not rest on a third
party's retention policy.

There is no celery beat on these deployments. Every periodic job runs through
django-crontab (#230); a Celery task alone would never fire.

---

## Setting it up on a new deployment

Each deployment measures itself, so this is done once per host.

### 1. Create the two synthetic checks

In Grafana Cloud → **Testing & synthetics → Synthetics → Checks → Add check**,
create one HTTP check per probe target. The two must be separate checks: the
report treats API and UI as independent targets and sums their outages.

| | API check | UI check |
| --- | --- | --- |
| Job name | `jc-api` | `jc-ui` |
| Target | `https://<host>/healthz` | `https://<host>/` |
| Frequency | 2 minutes | 2 minutes |
| Probe locations | 3, spread across regions | same 3 |
| Valid status codes | 200 | 200 |

Three locations, not one. An interval is counted as failed only when the
majority of locations failed, so a fault on a single location's own network
path is not billed to us as an outage. With one location there is no majority
to take and every blip on that path becomes downtime.

**The frequency must match `AVAILABILITY_PROBE_INTERVAL_SECONDS`.** A failed
check is recorded as exactly one interval of downtime, so a check running
every minute against a setting of 120 halves the reported outage.

Budget: the free tier allows 100k check executions/month. Two checks × three
locations × 2-minute frequency is ~65k. A third check, or a 1-minute
frequency, exceeds it.

<details>
<summary>Creating the same checks over the API instead</summary>

```bash
# Token needs the synthetic-monitoring:write scope.
curl -sS -X POST "https://synthetic-monitoring-api-<region>.grafana.net/api/v1/check/add" \
  -H "Authorization: Bearer $SM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "job": "jc-api",
        "target": "https://<host>/healthz",
        "frequency": 120000,
        "timeout": 10000,
        "enabled": true,
        "probes": [<probe ids>],
        "settings": {"http": {"method": "GET", "validStatusCodes": [200]}}
      }'
```

`GET /api/v1/probe/list` returns the probe IDs for your region. `frequency`
and `timeout` are milliseconds.
</details>

### 2. Create a metrics-read token

**Administration → Users and access → Cloud access policies**, scope
`metrics:read`, then generate a token from it. Note the **Prometheus instance
ID** (a number) and the **query endpoint** from the Prometheus datasource
details page — they are per-stack and not guessable.

### 3. Set the env

In the deployment's active `.env` (see `ACTIVE_ENV_FILE`; `jina_connect/.env`
wins over the repo root):

```ini
GRAFANA_PROM_URL=https://prometheus-prod-01-eu-west-0.grafana.net/api/prom
GRAFANA_PROM_USER=123456
GRAFANA_PROM_TOKEN=glc_...

AVAILABILITY_PROBE_SELECTOR_API=job="jc-api"
AVAILABILITY_PROBE_SELECTOR_UI=job="jc-ui"
AVAILABILITY_PROBE_INTERVAL_SECONDS=120

PARTNER_REPORT_RECIPIENTS=someone@example.com
```

The selectors are PromQL label selectors, without the braces. Narrow them with
`instance="..."` as well if one job name ends up covering several checks — the
client refuses to guess when a selector matches more than one series.

Unset credentials are not a broken deployment. A host with no monitoring
records no availability, which is the honest outcome; the command says so and
exits non-zero rather than writing zeros.

### 4. Verify before trusting it

```bash
python manage.py aggregate_availability --dry-run
```

Expect `720/720 intervals` per target for a healthy day. A count well under
720 means the check was created part-way through the day, a probe location is
failing to report, or the frequency does not match the setting.

Then backfill what Grafana still holds and read the month back:

```bash
python manage.py aggregate_availability --days 14
python manage.py send_availability_report --dry-run
```

### 5. Install the schedule

```bash
python manage.py crontab add && python manage.py crontab show
```

Both jobs must appear. Install them as the **same user the app runs as** — a
crontab under `root` while the app runs as `tech` will fail on file
permissions and log nowhere useful.

---

## Reading the numbers

**A failed check is one whole interval of downtime.** An outage shorter than
the interval is invisible; a longer one rounds up to the next interval. Every
synthetic-monitoring SLA makes this approximation. The report states it.

**Downtime is summed across targets, not maxed.** The platform is unusable if
either the API or the UI is down, and daily aggregates cannot reconstruct the
union of their outages. Summing never overstates availability; taking the
greater would.

**Missing days are never assumed healthy.** A day with no row does not count
towards coverage, and neither does one holding fewer than
`AVAILABILITY_MIN_DAY_COVERAGE` (default 90%) of its due checks — a row with
five of 720 intervals is a monitoring gap wearing the shape of a measurement.
The report prints incomplete coverage above the headline figure, because a
report computed from partial data can look excellent precisely *because* the
monitoring failed.

**Planned maintenance is excluded from both sides of the fraction.** Record it
in `MaintenanceWindow` *before* the month closes; the report reads the table at
render time, so a window added afterwards silently changes an already-sent
figure.

## When the nightly job fails

It logs to `jina_cron_availability_aggregate.log` in `BASE_DIR`. A run that
recorded **nothing at all** exits non-zero: an empty run that exited 0 would
read, in the log tail and to any wrapper watching it, exactly like a quiet
night. A partial run — one target, or one day of several — exits 0 with a
warning naming what it skipped, because the rows it did write are real.

- **`GRAFANA_PROM_TOKEN is not set` / 401** — the access policy token expired
  or was rotated. Re-issue it; retrying tonight will not help.
- **`no check results`** — Grafana held nothing for that day. No row is
  written, on purpose. Fix the check, then `--date` the missing day back in
  while it is still inside Grafana's 14-day window.
- **`Expected one aggregated series`** — the selector matches more than one
  check. Narrow it with `instance="..."`.

A gap older than 14 days cannot be recovered: Grafana has dropped it. The
month is reported as incomplete, which is the correct outcome and the reason
coverage is on the report at all.
