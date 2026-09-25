# Quickstart

Transcribe one page of a sample volume on your own machine and open it in
the viewer. No cluster and no GPU: Docker Compose runs a local S3 server,
the wrapper on the CPU and the web front. It takes about five minutes, most
of it downloading.

## You need

- `git`, `make`, and a recent Docker with Compose (`docker compose version`
  answers).
- About 8 GB of free disk for the images.
- Internet access to Docker Hub, GitHub and Hugging Face: the images, the
  sample pages and the models come from there.
- Two free ports, 8080 and 19000. Step 2 shows how to use others.

## 1. Get the code

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch
cd htrflow-batch
```

## 2. Start the stack

```bash
make compose-up
```

The first run pulls the images, several GB. It ends with:

```
 Container htrflow-batch-smoke-wrapper-1 Started
```

If it stops with `address already in use`, another program has one of the
ports. Pick two free ones and use them in every URL below:

```bash
make compose-down
make compose-up HTR_COMPOSE_WEB_PORT=8090 HTR_COMPOSE_S3_PORT=19010
```

## 3. Wait for the page

The wrapper downloads the models, transcribes one page on the CPU,
uploads the result and exits. That takes one to three minutes. Follow it:

```bash
docker compose -f .docker/docker-compose.yml logs -f wrapper
```

It returns by itself when the wrapper exits, and ends with:

```
wrapper-1  | … INFO [mock-vol] COMPLETE 1 pages (1 processed, 0 failed) in …s, viewer: http://localhost:19000/htr-results/demo-v1/mock-vol/iiif.json
wrapper-1 exited with code 0
```

## 4. Open the result

Open this in a browser:

```
http://localhost:8080/uv.html#?manifest=http://localhost:19000/htr-results/demo-v1/mock-vol/iiif.json
```

You should see the page with every text line outlined, and its
transcription in the **Text** panel.

`http://localhost:8080/` is the campaign browser. Here it only says it
cannot reach the campaign service: it lists the campaigns on a cluster, and
this stack has none.

## 5. Clean up

```bash
make compose-down
```

This removes the containers and the S3 data. The images stay, for a quicker
second run.

## What ran

| Service | What it does |
|---|---|
| `rustfs` | The S3 server, on port 19000. |
| `fixtures-init` | Creates the buckets, uploads four sample pages and a IIIF manifest for volume `mock-vol`, and makes the results readable from the browser. |
| `wrapper` | The published wrapper image, running pipeline `demo-v1` on `mock-vol`, one page only (`MAX_PAGES`). On a cluster the same image runs one pod per volume, on a GPU. |
| `web` | The web front on port 8080: the viewer, the run viewer and the campaign browser. |

The S3 credentials are throwaway values from `.env.example`. Never reuse
them anywhere else.

## Next

- [Deploy](deploy.md) the platform on a cluster with GPU nodes.
- [Run a campaign](campaigns.md) on your own volumes.
- No cluster yet, but one GPU node? [Dev cluster](../development/dev-cluster.md)
  sets up a disposable one-node cluster with everything included.
