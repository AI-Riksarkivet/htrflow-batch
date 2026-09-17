# Presentations

A five-part series for people who know htrflow and want to see what runs
it at archive scale. Each part opens in the browser as a presentation (arrow
keys move, `F` goes full screen) or downloads as a PDF.

| Part | What it covers | Open |
|---|---|---|
| **1. From one folder to the archive** | What changes between one volume on your machine and ten thousand in the archive: a pod per volume, a Job per campaign, the window and the queue, priority, the bucket, git as the interface, the whole picture and the status page | [slides](slides/part-1-from-one-folder-to-the-archive.html) · [PDF](slides/part-1-from-one-folder-to-the-archive.pdf) |
| **2. Your interface is git** | The campaigns repository, the pipeline and campaign files, validating locally, the pull request, apply, proving a recipe on six images, pausing, cancelling and re-running | [slides](slides/part-2-your-interface-is-git.html) · [PDF](slides/part-2-your-interface-is-git.pdf) |
| **3. Inside one run** | The container and the pod, what the pod may do and reach, the streaming loop, the bucket's contract, resume, verify, failed versus missing, exit codes | [slides](slides/part-3-inside-one-run.html) · [PDF](slides/part-3-inside-one-run.pdf) |
| **4. What the queue can do** | Kueue's concepts as capabilities — quotas, cohorts, preemption, fair sharing, flavors, admission checks — and dynamic resource allocation for GPUs | [slides](slides/part-4-what-the-queue-can-do.html) · [PDF](slides/part-4-what-the-queue-can-do.pdf) |
| **5. Models and signatures** | Why weights are the risky part, the warm-up and the model cache, the two transformers lines, bringing a new model, what a signature proves, and three ways a model could become a signed artifact | [slides](slides/part-5-models-and-signatures.html) · [PDF](slides/part-5-models-and-signatures.pdf) |

Each deck points at the pages on this site that
carry the same material in writing. The decks are built from Markdown in
the repository's `docs/slides` with Marp, on every change to the main
branch.
