---
name: convert-images
description: Convert, resize or compress images such as HEIC, PNG, JPEG and TIFF with the Mac's built-in sips tool, keeping the originals.
status: certified
---

## When to use
The user wants photos in another format (HEIC to JPEG is the common one), smaller, or a set size.

## Steps
1. See what you have: `sips -g pixelWidth -g pixelHeight -g format "<file>"` and `ls -lh`.
2. Write results to a new folder so the originals stay untouched: `mkdir -p "<folder>/converted"`.
3. Pick the command:
   - Convert: `sips -s format jpeg "<in>" --out "<folder>/converted/<name>.jpg"` (formats: jpeg, png, heic, tiff, gif, bmp, pdf).
   - Compress JPEG: add `-s formatOptions 75` (quality 0-100; 70-80 looks the same to most people).
   - Resize so the longest side is N pixels: `sips -Z 1600 "<in>" --out "<out>"`.
   - Exact size (may distort): `sips -z <height> <width> "<in>" --out "<out>"`.
   For many files, loop: `for f in "<folder>"/*.HEIC; do sips -s format jpeg "$f" --out "<folder>/converted/$(basename "${f%.*}").jpg"; done`
4. Check the results: the count of output files matches the inputs, and spot-check one with `sips -g pixelWidth -g format`.
5. Report the output folder and the size before and after (`du -sh` on both).

## Pitfalls
- Don't overwrite originals unless the user asks; a lossy conversion can't be undone.
- sips can't make WebP or AVIF files. Say so, and suggest a tool the user could install instead.
- For a video or audio file, sips is the wrong tool; tell the user rather than trying anyway.
