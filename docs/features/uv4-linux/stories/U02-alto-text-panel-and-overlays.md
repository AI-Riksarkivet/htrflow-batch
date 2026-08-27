---
type: Product Backlog Item
id: 2901
parent: 2801
title: Show the transcription next to the page, with lines outlined on the image
---

# U02 · Show the transcription next to the page, with lines outlined on the image

**Story.** As a reader, I want to see the transcribed text beside the page
image, with each text line outlined on the image and highlighted as I move
through the text, so that I can check the transcription against the
original line by line.

## Why it matters

The fork already contained this "text panel" — but it was switched off by a
disabled configuration fetch, and its line outlines were drawn in the wrong
place whenever the batch system had transcribed a downscaled copy of the
page (which it always does, to save GPU memory). Without this story the
viewer showed a page image and nothing else.

## What this delivers

A small, documented patch to the fork applied at build time:

- The viewer's configuration is fetched again, so the text panel is
  enabled.
- Line outlines are scaled from the transcribed image's size to the
  displayed image's size, so they land on the right lines.
- Outlines are always visible (not only on hover), and survive the viewer
  redrawing the page (previously they vanished after zooming).

## Done when

- [ ] Opening a batch-transcribed volume shows the text panel with the
      page's ALTO text.
- [ ] Every line outline sits on its line at any zoom level, on a page
      that was transcribed at a capped width.
- [ ] Clicking a line in the text highlights it on the image.
