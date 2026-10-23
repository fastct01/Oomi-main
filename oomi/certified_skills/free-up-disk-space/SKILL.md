---
name: free-up-disk-space
description: Find what is using disk space on the Mac and suggest what could go, deleting nothing without the user's explicit approval.
status: certified
---

## When to use
The disk is full or nearly full, or the user wants to know where their space went.

## Steps
1. Overall picture: `df -h /` (free space) and `du -sh ~/* 2>/dev/null | sort -rh | head -15`.
2. Drill into the biggest folders one level at a time:
   `du -sh "<folder>"/* 2>/dev/null | sort -rh | head -15`.
3. Find single large files: `find ~ -xdev -type f -size +500M 2>/dev/null -exec ls -lh {} \; | awk '{print $5, $9}' | sort -rh | head -20`.
4. Check the usual suspects and their sizes: `~/Downloads`, `~/Library/Caches`, `~/Library/Developer/Xcode/DerivedData`,
   `~/Library/Containers/com.docker.docker`, old iOS backups in `~/Library/Application Support/MobileSync/Backup`,
   `node_modules` folders (`find ~ -name node_modules -type d -prune 2>/dev/null | head`), the Trash (`du -sh ~/.Trash`).
5. Present a short table: what it is, size, whether it's safe to remove (caches and build output regenerate;
   documents, photos and backups don't), and how to remove it.
6. Delete only what the user picks, one item at a time, and prefer moving to the Trash
   (`osascript -e 'tell application "Finder" to delete POSIX file "<path>"'`) over `rm`, so it can be undone.
   Show `df -h /` again at the end.

## Pitfalls
- Never touch system folders (`/System`, `/Library`, `/usr`), the Photos or Mail libraries, or anything in iCloud Drive.
- `du` on protected folders prints permission errors; that's expected, ignore them.
- Some apps need to be quit before their caches are cleared.
