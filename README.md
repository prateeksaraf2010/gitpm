# GitPM — Jira, natively in GitHub

A Pull Request **is** the ticket. No database, no second tool.

| Jira | GitPM |
|---|---|
| Create ticket | Open a draft PR (`.gitpm/items/<slug>.md` gives it a diff) labelled `status:backlog` |
| In progress | Draft PR |
| In review | PR marked ready for review |
| Done | PR merged |
| Won't do | PR closed |
| Priority / type / points / epic / blocked | Labels `priority:P1` `type:bug` `pts:5` `epic:auth` `status:blocked` |
| Sprint | Milestone |

## Run
    export GITHUB_TOKEN=...          # repo scope (PRs, labels, contents)
    export GITPM_REPOS=org/a,org/b
    export ANTHROPIC_API_KEY=...     # optional; enables the AI features
    python3 -m gitpm                 # http://localhost:8787
Without GITHUB_TOKEN it starts in DEMO mode with sample data. Python 3.9+, no dependencies.

## AI features
- **Agent chat**: natural language → tool calls (create/update/transition/comment). "open a P1 bug for login timeouts and assign maya".
- **Auto-triage**: fills missing priority/type/points (button, or `python -m gitpm triage`).
- **Briefs**: standup, sprint health, risks (`python -m gitpm standup|sprint|risks`).
- **GitHub Action**: `.github/workflows/gitpm.yml` triages every new PR and posts a weekday standup (edit `YOUR_ORG/gitpm`).

Dragging a card to Done merges the PR (squash); to Cancelled closes it — both ask for confirmation.
Server binds to 127.0.0.1 and has no auth; don't expose it without adding one.
