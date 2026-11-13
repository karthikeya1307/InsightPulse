# Prompt history

The FAQ and Submission Guidelines require **all prompts submitted to every AI tool**. This folder
contains every prompt sent to the AI coding agent that built this repository. Each one is copied
verbatim from the session record.

| # | File | Sent (UTC) | Tool | Summary |
|---|---|---|---|---|
| 1 | [01_initial_build_prompt.md](01_initial_build_prompt.md) | 2026-11-11 17:12 | Claude (Claude Code agent in a Claude project) | The full build specification (22 sections) plus the official problem statement, with `Submission_Guidelines.pdf` and `FAQ.pdf` attached |
| 2 | [02_unify_requirements.md](02_unify_requirements.md) | 2026-11-11 17:14 | same session | Treat the specification, problem statement and PDFs as one problem, and make sure the solution covers all of them |
| 3 | [03_repository_details.md](03_repository_details.md) | 2026-11-12 01:47 | same session | GitHub's empty-repo quick-start for the target repository, plus the e-mail to use for commits |
| 4 | [04_push_output.md](04_push_output.md) | 2026-11-12 02:05 | same session | Terminal output of pushing the bundle to GitHub |
| 5 | [05_run_and_deploy.md](05_run_and_deploy.md) | 2026-11-12 02:07 | same session | Asked for a guide to run and deploy the app |
| 6 | [06_docker_wsl_error.md](06_docker_wsl_error.md) | 2026-11-12 02:35 | same session | Docker Desktop WSL error |
| 7 | [07_wsl_install_output.md](07_wsl_install_output.md) | 2026-11-12 02:47 | same session | Output of `wsl --install` |
| 8 | [08_datasets_and_upload.md](08_datasets_and_upload.md) | 2026-11-12 03:30 | same session | Which datasets are used, and how a user can upload their own data (then chose to add CSV upload) |
| 9 | [09_rebuild_question.md](09_rebuild_question.md) | 2026-11-12 03:46 | same session | Whether to rebuild the Docker image after the update |
| 10 | [10_explain_solution.md](10_explain_solution.md) | 2026-11-12 03:53 | same session | Explain the problem statement and how the app solves it |

## Notes on completeness

* AI responses are optional under the FAQ, so they are not reproduced here. The commit history
  (one commit per phase) shows what the agent produced and when.
* The project chat assistant forwarded prompt 2 to the build agent without changing its meaning.
  The forwarding note is not a prompt the candidate wrote, so it is not listed.
* If any other AI tool was used outside this session, for example to draft the specification in
  prompt 1, its prompts must be added here as `11_...md` and onwards before submission.
  See `AI_USAGE.md`.
