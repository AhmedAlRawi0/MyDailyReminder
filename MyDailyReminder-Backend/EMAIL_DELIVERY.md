# Daily reminder delivery

The scheduler calls `GET /send-email` every two minutes. Each request sends at most
five individual emails using one SMTP connection. A 10-second cooperative request
budget reserves the final two seconds for recording results; no new recipient
starts with three seconds or less remaining. SMTP and database operations have
explicit timeouts. This budget is not a hard operating-system deadline (DNS,
process suspension, cold starts, or network stalls can still add latency).

Delivery starts at **10 a.m. America/Toronto**, automatically following daylight
saving time. The campaign continues through the following morning until 10 a.m.
A new date expires unfinished work from older campaigns. With five recipients
per two-minute call, 500 recipients take roughly 3 hours 20 minutes, plus content
preparation, retries, and slower batches.

## Deploy and configure

The existing paid Render web service supports the current Gmail SMTP transport.
[Free Render web services block SMTP ports](https://render.com/docs/free), so this
transport requires retaining the existing paid instance. No worker, Redis, or
additional paid service is required.

1. Disable the old cron-job.org trigger and let any active old send finish.
2. Deploy the backend and `mydailyhadith-frontend` changes together using the
   project's existing deployment flow. Keep the backend start command
   `gunicorn app:app` and the existing MongoDB/Gmail variables.
3. Set `EMAIL_JOB_START_DATE=YYYY-MM-DD` to the **next unsent day in Toronto**.
   If today's reminder already went out, use tomorrow's date. This prevents a
   duplicate first campaign: the old `last_updated_syd` value cannot establish
   whether delivery actually completed. Leave this date fixed after cutover.
4. In cron-job.org, configure:
   - URL: `https://mydailyhadith.onrender.com/send-email`
   - Method: `GET`
   - Schedule: every two minutes, every hour, every day (`*/2 * * * *` where cron
     syntax is available). The backend, not the scheduler timezone, enforces 10 a.m.
   - No authorization header is required.
   - Timeout: 30 seconds; enable failure/recovery notifications.
5. Before the configured start date, use the scheduler's test run. Expect HTTP
   `200` and `status: "not_due"`, with no mail sent. Enable recurring execution
   for the new schedule.
6. At the first 10 a.m. boundary, check response counts and Render logs. Successful
   calls progress from `preparing` to `running` to `complete` or `needs_review`.
   Repeated calls after completion do not send more mail.

[cron-job.org supports minutely schedules](https://cron-job.org/en/faq/).
Its default request timeout is 30 seconds. Do not configure the old once-daily
schedule: a single request now sends at most five emails.

The scheduler API/Render account configuration is external to this repository;
local `.env` changes do not change Render's environment or cron-job.org settings.

## Persistence and delivery behavior

Collections are in `MONGO_SUBSCRIBERS_DB_NAME`:

- `email_campaigns`: unique `_id` equal to the Toronto campaign date, frozen
  content, recipient snapshot, initialization offset, status, and a 30-second
  owner lease. A completed snapshot is removed after its recipient rows exist.
- `email_deliveries`: unique `(campaign, email)` index, original subscriber ID,
  status, attempt count, timestamps, and sanitized error code. Rows are
  initialized in chunks of 100 with idempotent upserts.
- `daily_content`: unique `_id` equal to `kind:YYYY-MM-DD`, translation
  checkpoints, content readiness, and a separate preparation lease. The public
  hadith/verse endpoints and campaign preparation share this content.

Indexes are created idempotently on the first batch request. The
MongoDB user needs normal index creation and read/write permissions on these
collections. Existing hadith and verse state remains compatible. No existing
subscriber records need migration. Do not delete campaign/delivery records and
rerun the same date: deleting that history removes duplicate protection.

The current audience (under 500 subscribers) is captured as one MongoDB snapshot
at campaign creation. A campaign does not include subscriptions made afterward.
Before each send, the original subscriber ID and email must still exist; someone
who unsubscribed and resubscribed during the campaign starts receiving mail the
next day. Recipient addresses remain individual; this does not use BCC.

`sent` means SMTP accepted the message, not confirmed inbox delivery. Explicit
SMTP 4xx failures retry up to **three times after the initial attempt**, after
2, 4, and 8 minutes. Explicit permanent failures and exhausted retries become
`permanent_failure`. Authentication/connection setup failures return `503` and
leave recipients pending. A failed SMTP session ends the batch.

A disconnect during SMTP DATA, interrupted `sending` record, or crash after SMTP
acceptance but before the MongoDB write becomes `uncertain`. These records are
never automatically resent. A stable Message-ID aids investigation but is not
an SMTP deduplication guarantee. Expired leases let another request resume the
campaign; they do not authorize resending an ambiguous submission.

## Responses and monitoring

HTTP `200` includes `campaign_date`, `status`, `counts`, `total`, and `initialized`.
Statuses are `not_due`, `active`, `preparing`, `running`, `complete`, `needs_review`,
or `expired`. Counts include `pending`, `sending`, `sent`, `retryable_failure`,
`permanent_failure`, `uncertain`, `unsubscribed`, and `expired`.

The endpoint is public and requires no authentication. HTTP `503` means infrastructure or content preparation is unavailable; `counts` is null
because the database may be unreachable. Retry on the next scheduled call.
Public daily-content endpoints also return `503` with `Retry-After: 5` when
preparation is incomplete or unavailable.

Logs contain campaign dates, statuses, counts, and exception class names. They
exclude addresses, credentials, SMTP response text, and message bodies. Review
`needs_review` in campaign records/logs: it is a successful HTTP request, so the
scheduler's HTTP failure notification will not detect it.

To review uncertain/permanent deliveries, filter `email_deliveries` by campaign
and status in your existing MongoDB admin tool. Determine whether an uncertain
message was accepted before making any manual resend decision; never reset the
whole campaign. Investigate Gmail limits or credentials for recurring SMTP
failures. Gmail's sending quotas still apply independently of batching.

To pause scheduled delivery, disable the cron job. The public endpoint can still
be called directly. Keep all delivery records. To resume, restore the schedule; recorded successes are
skipped. Do not restore the old full-list sender mid-campaign.

## Tests

Install the existing backend requirements and have `mongod` on your PATH, then
run from the repository root:

```sh
python3 -m unittest discover -s MyDailyReminder-Backend/tests -v
```

Tests launch a temporary MongoDB instance bound to localhost on a random port,
use isolated test databases, mock all email/content network calls, and clean up
the instance afterward. They override application credentials before importing
configuration. No production database or subscriber is contacted. Missing
`mongod` is reported as a skipped integration suite, not a successful validation.

Coverage includes resumable five-recipient batches, concurrent requests, stale
leases, partial initialization, frozen content/audience, unsubscribe/resubscribe,
retry exhaustion, unknown SMTP outcomes, persistence failure after acceptance,
budget exhaustion and actual injected delays, Toronto daylight saving boundaries,
cutover date protection, unauthenticated endpoint calls, and shared content preparation.
