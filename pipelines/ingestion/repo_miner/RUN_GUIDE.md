# Running the repo miner

A short guide for teammates who need to run the miner on their machine. It
covers setup, a smoke test, a real run, resuming and handing in the output.
For every flag and column, see [`README.md`](README.md) and
[`ARCHITECTURE.md`](ARCHITECTURE.md).

Run every command from this directory (`pipelines/ingestion/repo_miner/`).

## Try it on two PCs

A full dry run of the real flow with four small repos (`repos_test.txt`), about 10 to 20 minutes per PC. PC A is the
lead; PC B is a volunteer. PC A also mines, so it uses its own code too. Until this branch is merged, both PCs use
the branch: `git fetch && git checkout infra/riffle-data-server && git pull`.

**Both PCs, once**

```bash
cd riffle/pipelines/ingestion/repo_miner
pip install -r requirements.txt
cp .env.example .env              # put your own GITHUB_TOKEN in it (ask python doctor.py to do it for you)
python doctor.py                  # checks and sets up every token (see below); "Join token: not joined" is expected for now
```

**PC A (lead), on the TUI**

1. `python tui.py` (the lead's `.env` also has `RIFFLE_SYNC_URL` and `RIFFLE_ADMIN_TOKEN`).
2. Set the form the way both PCs should run. For this test:
   - Repos file `repos_test.txt`, Only repos tagged `test`, Number of PCs `2`, Max PRs per repo `20`.
   - Untick **LLM flags**, **SZZ label** and **Scanners** (they are slow and need extra setup; a real run keeps them).
3. Press **F3** (Lead tools). Set the days (7), press **Issue codes**. Two rows appear: `PC 1/2` and `PC 2/2`.
4. Select `PC 2/2`, press **Copy selected**, and send it to PC B privately (a message to yourself is fine for a test).
5. With the **PC 1/2** row selected, press **Join this PC as selected** (no pasting; it closes Lead tools and joins), or close Lead tools (Escape), paste the code into the Join box and press **Join** (F2). The left panel says
   "joined as pc1of2-…" and the form now shows PC 1. Press **Start**.

**PC B (volunteer), on the TUI**

1. `python tui.py`.
2. Paste the code into **Join code**, press **Join**. The form fills in by itself: repos file, domain, 2 PCs, "This PC # 2",
   max PRs 20, LLM and SZZ off. You change nothing. Press **Start**.

**What to watch**

- The store page, https://34-72-217-36.sslip.io (sign in with the read token): **Overview** shows both PCs mining, repos
  done and PRs mined climbing; each repo uploads when it finishes. **Admin** shows both tokens, their state and last upload.
- In each TUI: the stats panel shows `Team store: uploading as pc1of2-…`, then `last upload 1m ago · N files sent`.
- When both finish: **Data** shows `mined_dataset` over both PCs' files (two files, rows from four repos), and **Log search**
  finds lines from both PCs.

**Things worth trying**

- Press **Stop** on PC B mid-run. It uploads what it has (the log says so); the page shows it `stopped`. Start again: it
  resumes where it left off.
- On PC B change **Max PRs per repo** to `100` and press Start. It refuses, lists `--max-prs: lead 20, yours 100`, and F2
  with the Join box empty puts the lead's settings back.
- On the **Admin** page press **Rotate** twice. New codes appear and PC B's old token stops working; its next upload says
  uploads are off and why. Re-join it with the new code.

**After the test:** on the Admin page press **Revoke all** for the split. The test files are in the bucket under
`pc1of2-<split>/` and `pc2of2-<split>/`; delete them with
`gcloud storage rm -r gs://riffle-data-miner-mined/pc1of2-<split> gs://riffle-data-miner-mined/pc2of2-<split>`.

## The four tokens, and `python doctor.py`

`python doctor.py` is the one place that checks all of them and offers to save each (answer `y`; pastes are hidden):

| Doctor line | What it is | Where it comes from | What it is for |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | your own GitHub token (`public_repo`) | github.com/settings/tokens | the miner reading PRs; use a different account per PC |
| `Join token` | the write token inside the **join code** (`riffle1.…`) | the lead, privately | uploading your output to the store; it works only for your split |
| `Read token` | `rd_…` | the lead's Admin page → Add reader | signing in to the store's pages. The TUI's **F4** copies it and opens the page |
| `Admin token` | `ad_…` (lead only, shown only when set) | `manage.py add-admin` on the VM | issuing codes and revoking (TUI F3, `admin.py`, the Admin page) |

`python doctor.py --check` only reports and never asks.

## What you need

- Python 3 with `pip`, and `git`.
- A GitHub personal access token with `public_repo` scope. Without one you get
  60 requests an hour and no labels, so the data is useless.
- Free disk for one clone at a time. Clones are deleted after each repo unless
  you pass `--keep-clones`. Ask the lead how much space the largest repos in
  your share need.
- An Anthropic API key, only if the lead wants the LLM flags. The LLM stage is
  on by default and switches itself off with a warning when no key is found.

## 1. Set up

```bash
git clone git@github.com:ru-dr/riffle.git
cd riffle/pipelines/ingestion/repo_miner
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and put your token after `GITHUB_TOKEN=`. The file is gitignored.
Never commit it, and never paste the token in chat.

Then run the environment check:

```bash
python doctor.py --check
```

It checks the OS, Python, git, packages, the token (verified against GitHub),
the optional API tools (oasdiff, buf, graphql-inspector), disk and RAM. Run `python doctor.py` without `--check` to let it install missing
packages. On Debian or Ubuntu with an externally managed Python, it creates
`.venv/` for you.

## 2. Smoke test

```bash
python build_dataset.py --test
```

This mines a few PRs from one small repo (`pallets/flask`) into
`test_output/test_dataset.jsonl`, which you can open in a text editor. If it
finishes and the file has rows, your setup works. Do this before any real run.

## 3. Find your share

The corpus is split across machines. Every machine computes the same split, so
you only need to know your number `K` out of `N` machines. If you have a join code,
`python join.py CODE` tells you your `K/N` and the command, so you can skip to step 4
after the smoke test.

```bash
python build_dataset.py --repos-file repos_web_ts.txt --only-domain web-ts --shard-plan 3
```

This prints which repos each machine mines (replace `3` with the real `N`).
Check that your machine's list looks right.

Use a token from a different GitHub account on each machine. GitHub rate
limits are per account, so two machines on one token slow each other down.

## 4. Run

The command used for the first domain in the README is below. Ask the lead to
confirm the window and `--max-prs` before you start, and use the same flags
on every machine.

```bash
python build_dataset.py \
  --repos-file repos_web_ts.txt --only-domain web-ts \
  --window-from 2023-07-01 --window-to 2026-07-31 --max-prs 8000 \
  --shard K/N
```

Replace `K/N` with your machine, for example `--shard 2/3`.

What to expect:

- The output is named after your share, for example `dataset_pc2of3.parquet`.
- Snapshots and commit features are written next to it as separate tables.
- The run is long. It clones one repo at a time, mines it, appends the rows and
  moves on. A progress log is printed and saved under `logs/`.
- GitHub rate limits make the miner sleep and retry. That is normal.

**Lead:** in the TUI press F3 (Lead tools). Set the form the way every PC should run, set "Number of PCs", press
Issue codes, and copy each code to its owner. The same screen lists who has uploaded and revokes a PC.

Prefer a menu? `python tui.py` runs the same thing with a screen for the
settings, including "Number of PCs" and "This PC #". With a join code the TUI is the easiest
way: paste the code into the "Join code" box and press **Join** (or F2). It checks the code,
saves your token, and fills the whole form with the lead's settings, including which PC you
are. Press **Start**. If you change a setting that shapes the data, Start stops and lists
what differs (F2 with the box empty puts the lead's settings back). The panel shows
"Team store: last upload 2m ago" while it runs, and Stop uploads what you have before it
exits, so wait for the "[sync]" line before closing the window (ctrl+q waits for it too).

## 5. Stop and resume

Press ctrl+c any time. Finished PRs are saved in `<out>.partial.jsonl` and
finished repos are in the output file.

To resume, run the exact same command again. The miner skips finished repos
and continues from the PRs it had selected. Do not delete `ci_cache/`, it is
what makes resuming fast. Add `--remine-repo OWNER/NAME` only if the lead
asks you to redo one repo from scratch.

## 6. Check your run

```bash
python build_dataset.py --repos-file repos_web_ts.txt --only-domain web-ts --shard K/N --audit
python trace_report.py logs/run-*.trace.jsonl
```

`--audit` flags repos that were mined while blame data was failing. The trace
report shows stage times, slow PRs and stalls. If `--audit` flags a repo, tell
the lead before re-mining it.

## 7. Hand in the output

When every repo in your share is done, a file named `dataset_pcKofN.shard.json`
is written next to your dataset. Send the lead these, with the same miner
version and flags you ran with:

- `dataset_pcKofN.parquet`
- its snapshots and commit features tables
- `dataset_pcKofN.shard.json`

The lead merges them with `python merge_shards.py --out dataset.parquet`. The
merge refuses if a machine is missing, the split or miner version differs, or a
repo appears twice.

Nothing to send by hand. The lead sends you a **join code** (it starts with
`riffle1.`) when they plan the split. It is a secret: it lets anyone upload as your PC,
so do not paste it in chat, and delete the message after you use it.

```bash
python join.py riffle1.PASTE-THE-WHOLE-CODE
```

That checks the code with the store, saves the store address and your token in `.env`,
and prints the exact command to run, including `--shard K/N`. In the TUI the form fills in and **locks**: you cannot change the lead's settings on your PC (workers, clone folder and resume switches stay yours). Run that command and no
other, because every PC must use the same flags. `python doctor.py` shows a "Team store"
line that says whether it is working, and `python join.py --check` tests it alone.

From then on the miner sends your logs, health files, dataset and shard manifest after
each repo, when you press ctrl+c and at the end. It never stops a run if the store is
down or the token stops working; a line starting `[sync]` says what happened, and
`[sync] OFF` says why and how to fix it. Your token only works for your split, so if the
miner says it is for another split, your flags differ from the lead's: use the command
join.py printed. Your `ci_cache/` stays on your PC and is not sent. Keep it so a rerun
resumes.

Run output is not committed. `ci_cache/`, `snapshots_raw/`, `health/`,
`logs/`, `test_output/` and the `*.parquet` and `*.jsonl` files are gitignored.
Share them through the team bucket or DVC, as the lead says.

## When something goes wrong

| Symptom | What to do |
| --- | --- |
| Run says rate limited and waits | Normal. Leave it. Check you are on your own token |
| `GITHUB_TOKEN` rejected in `doctor.py` | The `.env` still has the placeholder, or the token lacks `public_repo` |
| LLM stage switched off with a warning | No `LLM_API_KEY` found. Fine if the lead wants no LLM. Otherwise add the key and resume |
| Clone fails or times out | The miner retries each repo up to 3 times. If it still fails, rerun the same command |
| Disk full | Free space and rerun the same command. Do not pass `--keep-clones` |
| Merge refuses your shard | Your miner version or flags differ from the other machines. Pull `main`, check `version.py`, and ask the lead |
| A repo looks wrong in `--audit` | Tell the lead. Do not re-mine on your own |

## Rules to keep

- Use the same flags and the same miner version on every machine.
- The miner stays generic. Do not edit it to special-case a repo. Repo lists
  and domain tags live in `repos*.txt`.
- An unobserved label stays null in the output. Do not fill it in by hand.
- Questions about the code or columns: [`ARCHITECTURE.md`](ARCHITECTURE.md).
