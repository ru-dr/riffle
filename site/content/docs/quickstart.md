> **Planned.** The commands below are the intended local workflow. They land
> with the first runnable services.

## Prerequisites

| Tool | Version |
| --- | --- |
| Docker | with Compose |
| Go | 1.22 or later |
| Python | 3.11 or later |
| Node | 20 or later |
| GitHub CLI | `gh`, for webhook forwarding |

## Run the stack

```bash
git clone git@github.com:ru-dr/riffle.git && cd riffle
cp .env.example .env
make bootstrap && make up && make seed
```

`make up` brings up Postgres, Redis, a Pub/Sub emulator and all four services.
`make seed` loads a fixture tenant so the dashboard has something to show.

## Everyday commands

```bash
make test                   # all services
make lint
make logs SERVICE=scorer    # follow one service
make down
```

## Send a pull request without GitHub

Replay a recorded webhook, or forward live events from a test repository:

```bash
make replay FIXTURE=fixtures/pr_opened.json

gh webhook forward --repo=<org>/<test-repo> --events=pull_request \
  --url=http://localhost:8080/webhook
```

## Train locally

Training runs on CPU and finishes in minutes on the fixture tenant:

```bash
make train    TENANT=fixture
make evaluate TENANT=fixture
make promote  TENANT=fixture VERSION=<version>
```
