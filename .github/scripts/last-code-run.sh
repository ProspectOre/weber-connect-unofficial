#!/usr/bin/env bash
# Report the last code run of a required check for this PR head.
#
# Usage: last-code-run.sh CHECK_NAME   (in a pull_request edit run)
# A title or body edit changes no code, so its run checks attribution and then
# passes or fails with the newest earlier run of CHECK_NAME on the same head,
# waiting while that run is in progress. Earlier means an older check suite, so
# two edit runs never wait on each other; cancelled and skipped runs are not
# results. Needs GH_TOKEN with checks:read and actions:read, and HEAD_SHA.
set -euo pipefail
check=$1
read -r own workflow < <(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
  --jq '"\(.check_suite_id) \(.workflow_id)"')
for _ in $(seq 110); do
  prior=$(gh api --method GET "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs" \
    -f check_name="$check" -f filter=all -f per_page=100 --jq "
    [.check_runs[] | select(.app.slug == \"github-actions\" and .check_suite.id < $own
      and .conclusion != \"cancelled\" and .conclusion != \"skipped\")]
    | max_by(.id) | select(.) | \"\(.status) \(.conclusion) \(.html_url)\"")
  if [ -z "$prior" ]; then
    # The aggregate check is created only after its prerequisite jobs finish.
    # Wait only while an older workflow can still create it. A missing,
    # cancelled or approval-held code run must not consume the whole deadline.
    running=$(gh api --method GET "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow/runs" \
      -f head_sha="$HEAD_SHA" -f per_page=100 --jq "
      [.workflow_runs[] | select(.check_suite_id < $own
        and (.status == \"queued\" or .status == \"in_progress\"))] | length")
    if [ "$running" -eq 0 ]; then
      echo "No active code run of '$check' for this head; approve, push or reopen the PR to run CI."
      exit 1
    fi
    sleep 30
    continue
  fi
  read -r status conclusion url <<<"$prior"
  if [ "$status" = completed ]; then
    echo "Last code run of '$check': $conclusion ($url)"
    [ "$conclusion" = success ]
    exit
  fi
  sleep 30
done
echo "The last code run of '$check' did not appear or finish in time."
exit 1
