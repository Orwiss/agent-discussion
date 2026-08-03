# MAS experiment deployment and operations

## Live service

- Preferred discussion URL after DNS activation: <https://ideagents.orwiss.xyz/>
- Preferred survey URL after DNS activation: <https://ideagents.orwiss.xyz/survey/>
- Direct Cloud Run fallback: <https://mas-ideation-nco437omja-du.a.run.app/>
- Vercel proxy project: `interactive-orwiss/ideagents` (Hobby)
- Google Cloud project: `mas-503315`
- Cloud Run service/region: `mas-ideation` / `asia-northeast3`
- Supabase project: `agent-web-survey`

Every page except the health check and login page requires Google login. Google
OAuth test users and the backend allowlist both contain only the approved
researcher accounts. Browser clients cannot read or write Supabase directly.

Gabia DNS must contain this record before the preferred URLs activate:

```text
Type: A
Host: ideagents
Value: 76.76.21.21
```

Do not change the domain's nameservers. Remove any pre-existing `ideagents`
record before adding the A record, then wait for DNS and TLS issuance.

## Running a participant

1. A researcher signs in with an approved Google account.
2. Enter the participant ID and run the discussion session.
3. Use the same participant ID for the discussion and survey.
4. Lowercase IDs such as `p21` and surrounding spaces are automatically saved
   as `P21`.
5. Pilot/retry sessions can reuse a participant ID; every run has a separate
   session ID and remains distinguishable in the analysis views.

To add or revoke a researcher, update both Google Auth Platform's test-user
list and the `allowed-researcher-emails` Secret Manager secret, then deploy a
new Cloud Run revision.

## Data and analysis

Use the Supabase SQL editor. The most convenient exports are:

```sql
select *
from public.analysis_session_summary
order by participant_id, started_at;
```

```sql
select *
from public.analysis_transcript
order by participant_id, session_id, message_index;
```

```sql
select *
from public.analysis_llm_usage_by_session
order by session_id;
```

`analysis_session_summary` has one row per session and joins the submitted
idea, message counts, duration, survey rounds, and input/output/cached/reasoning
token totals. Raw data remains available in `experiment_events`,
`experiment_messages`, `experiment_ideas`, `llm_calls`, and `submissions`.

## Capacity and cost controls

- Cloud Run minimum instances: `0`
- Cloud Run maximum instances: `1`
- App-level active session limit: `3`
- Container concurrency: `20`
- Transport: 20-second HTTP long polling; no WebSocket
- Custom-domain proxy: Vercel Hobby external rewrite
- LLM credentials: Google Secret Manager
- Study database: Supabase Free

Keep the experiment tab open while a session is running. Continuous polling
keeps an active Cloud Run request, so the session is not subject to an
application-level 15-minute inactivity cutoff. Session authorization is also
stored in the `experiment_sessions.counts` JSON as a one-way token hash. If a
Cloud Run instance is replaced, the live agent conversation cannot resume, but
the browser stops polling and the final idea can still be submitted safely to
Supabase. Avoid deployments while a participant is actively discussing so the
conversation itself is not interrupted.

Cloud Run, Cloud Build, Artifact Registry, and Supabase are configured to stay
within their free allowances for the planned 30-participant study. The two
temporary Cloud Build source archives were removed after deployment; a future
deployment will recreate and can again remove them.

## Redeploy

From the `agent-discussion` repository, load the Supabase URL and run:

```powershell
.\scripts\deploy_cloud_run.ps1 `
  -ProjectId mas-503315 `
  -Region asia-northeast3 `
  -ServiceName mas-ideation `
  -SupabaseUrl "https://qurcykghmpnhozubgpnd.supabase.co" `
  -GoogleOAuthClientId "<web OAuth client ID>"
```

The script rebuilds the sibling `agent-web-survey` project, embeds its `dist`
folder, deploys one Cloud Run revision, and keeps all runtime secrets out of
the source archive.
