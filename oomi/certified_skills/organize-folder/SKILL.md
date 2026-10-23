---
name: organize-folder
description: Tidy a messy folder such as Downloads or Desktop into subfolders by kind or date, moving files and never deleting any.
status: certified
---

## When to use
The user wants a folder cleaned up or sorted. Moving files only; deleting is a separate decision the user makes.

## Steps
1. Look first: list_dir on the folder, then a count by extension:
   `find "<folder>" -maxdepth 1 -type f | sed -E 's/.*\.([^./]+)$/\1/;t;s/.*/(none)/' | tr 'A-Z' 'a-z' | sort | uniq -c | sort -rn`
2. Propose a plan before touching anything: the subfolders (for example Documents, Images, Videos, Audio,
   Archives, Installers, Code, Other, or by year-month if the user prefers dates) and how many files go in each.
   Ask the user to confirm or adjust it.
3. Move with `mv -n` (never overwrite), one category at a time, e.g.
   `mkdir -p "<folder>/Images" && find "<folder>" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.png' -o -iname '*.heic' \) -exec mv -n {} "<folder>/Images/" \;`
   For dates use the modified time: `stat -f '%Sm' -t '%Y-%m' "<file>"`.
4. Check that nothing was lost: count the files before and after with
   `find "<folder>" -type f | wc -l`; the numbers must match.
5. Report what moved where and anything left in place (name clashes that `mv -n` skipped, hidden files).

## Pitfalls
- Leave existing subfolders, app bundles (`*.app`) and hidden files (`.DS_Store`, `.localized`) where they are.
- Files still downloading (`*.crdownload`, `*.download`, `*.part`) stay put.
- Don't rename files unless asked; people search by the names they know.
