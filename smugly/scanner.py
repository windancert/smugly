import hashlib
import os
from pathlib import Path
from typing import Optional

PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".heic"})
EXCLUDED_DIRS = frozenset({"_deleted"})
# SmugMug allows 5 folder levels below root + 1 gallery = 6 path components max
MAX_DEPTH = 6


def is_photo(name: str) -> bool:
    return Path(name).suffix.lower() in PHOTO_EXTENSIONS


def compute_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Scanner:
    def __init__(self, db_path: Path, photo_dir: Path, smugmug_client=None):
        self.db_path = db_path
        self.photo_dir = photo_dir
        self.client = smugmug_client

    def _conn(self):
        from smugly.db import get_conn
        return get_conn(self.db_path)

    def _excluded(self, name: str) -> bool:
        return name in EXCLUDED_DIRS or name == self.db_path.name

    def scan(self, subpath: str = "") -> dict:
        conn = self._conn()
        conn.execute(
            "UPDATE scan_state SET is_scanning=1, scan_root=? WHERE id=1",
            (subpath or None,),
        )
        conn.commit()
        try:
            local_tree = self._walk_local(subpath)
            smugmug_albums = self._refresh_smugmug_cache(subpath) if self.client else {}
            summary = self._diff(local_tree, smugmug_albums, subpath)
            conn.execute(
                "UPDATE scan_state SET is_scanning=0, last_scan_at=datetime('now') WHERE id=1"
            )
            conn.commit()
            return summary
        except Exception:
            conn.execute("UPDATE scan_state SET is_scanning=0 WHERE id=1")
            conn.commit()
            raise

    # ── local walk ───────────────────────────────────────────────────────────

    def _walk_local(self, subpath: str) -> dict:
        """Returns {rel_path: {photos, subdirs, error, depth, abs_path}}."""
        scan_root = self.photo_dir / subpath if subpath else self.photo_dir
        result: dict = {}

        for dirpath, dirnames, filenames in os.walk(scan_root):
            abs_dir = Path(dirpath)
            rel = abs_dir.relative_to(self.photo_dir)
            rel_key = rel.as_posix()
            if rel_key == ".":
                rel_key = ""

            dirnames[:] = [d for d in sorted(dirnames) if not self._excluded(d)]

            photos = sorted(f for f in filenames if is_photo(f))
            subdirs = list(dirnames)

            error: Optional[str] = None
            if photos and subdirs:
                error = "mixed"
            elif not photos and not subdirs and rel_key:
                error = "empty"

            depth = len(rel_key.split("/")) if rel_key else 0
            if not error and depth > MAX_DEPTH:
                error = "too_deep"

            result[rel_key] = {
                "photos": photos,
                "subdirs": subdirs,
                "error": error,
                "depth": depth,
                "abs_path": abs_dir,
            }

        return result

    # ── SmugMug cache ────────────────────────────────────────────────────────

    def _refresh_smugmug_cache(self, subpath: str) -> dict:
        conn = self._conn()
        root_node_id = self.client.get_root_node_id()
        all_nodes = self.client.walk_node_tree(root_node_id)

        if subpath:
            conn.execute("DELETE FROM albums WHERE local_path LIKE ?", (f"{subpath}%",))
        else:
            conn.execute("DELETE FROM albums")

        albums: dict = {}
        for node in all_nodes:
            path = node["path"]
            if subpath and not (path == subpath or path.startswith(subpath + "/")):
                continue
            conn.execute(
                """INSERT OR REPLACE INTO albums (local_path, smugmug_node_id, smugmug_album_id, node_type)
                   VALUES (?, ?, ?, ?)""",
                (path, node["node_id"], node.get("album_key"), node["node_type"]),
            )
            albums[path] = node

        conn.commit()
        return albums

    # ── diff ─────────────────────────────────────────────────────────────────

    def _diff(self, local_tree: dict, smugmug_albums: dict, subpath: str) -> dict:
        conn = self._conn()
        summary = {
            "errors": 0, "in_sync": 0, "upload": 0,
            "download": 0, "conflict": 0, "rename": 0,
        }

        existing: dict = {}
        for row in conn.execute("SELECT * FROM files"):
            existing[row["local_path"]] = dict(row)

        # hash → [paths] for rename detection
        hash_to_paths: dict = {}
        for path, info in existing.items():
            if info["local_hash"]:
                hash_to_paths.setdefault(info["local_hash"], []).append(path)

        seen: set = set()

        for rel_dir, info in local_tree.items():
            if info["error"]:
                summary["errors"] += 1
                marker = (rel_dir + "/") if rel_dir else "__root__"
                conn.execute(
                    "INSERT OR REPLACE INTO files (local_path, status) VALUES (?, 'structure_error')",
                    (marker,),
                )
                continue

            if not info["photos"]:
                continue  # pure-folder dir

            smugmug_album = smugmug_albums.get(rel_dir)
            album_key = smugmug_album["album_key"] if smugmug_album else None

            remote_by_name, remote_by_hash = self._fetch_remote_images(album_key)
            abs_dir: Path = info["abs_path"]
            processed_remote: set = set()

            for photo_name in info["photos"]:
                local_rel = f"{rel_dir}/{photo_name}".lstrip("/")
                seen.add(local_rel)

                abs_path = abs_dir / photo_name
                local_mtime = abs_path.stat().st_mtime

                # use cached mtime to skip rehashing unchanged files
                prev = existing.get(local_rel)
                if prev and prev["local_mtime"] == local_mtime and prev["local_hash"]:
                    local_hash = prev["local_hash"]
                else:
                    local_hash = compute_md5(abs_path)

                remote_img = remote_by_name.get(photo_name)
                if remote_img is None:
                    remote_img = remote_by_hash.get(local_hash)
                remote_hash = (
                    remote_img.get("ArchivedMD5") or remote_img.get("MD5Sum", "")
                ) if remote_img else None
                if remote_img:
                    processed_remote.add(remote_img.get("FileName", ""))

                if prev is None:
                    self._handle_new(
                        conn, local_rel, local_hash, local_mtime,
                        remote_img, remote_hash, album_key,
                        hash_to_paths, summary,
                    )
                else:
                    self._handle_known(
                        conn, local_rel, local_hash, local_mtime,
                        remote_img, remote_hash, prev, summary,
                    )

            # remote-only images in this album
            for fname, img in remote_by_name.items():
                if fname in processed_remote:
                    continue
                remote_rel = f"{rel_dir}/{fname}".lstrip("/")
                if remote_rel in existing:
                    continue
                r_hash = img.get("ArchivedMD5") or img.get("MD5Sum", "")
                conn.execute(
                    """INSERT OR REPLACE INTO files
                       (local_path, smugmug_image_id, smugmug_album_id, smugmug_hash, status)
                       VALUES (?, ?, ?, ?, 'remote_only')""",
                    (remote_rel, img.get("ImageKey"), album_key, r_hash),
                )
                self._enqueue(conn, "download", remote_rel, img.get("ImageKey"), album_key)
                summary["download"] += 1

        # previously tracked files not found this scan → potential local deletion
        for path, info in existing.items():
            if path in seen or path.endswith("/") or path == "__root__":
                continue
            if subpath and not path.startswith(subpath):
                continue
            if info["status"] in ("in_sync", "local_only"):
                if info["smugmug_image_id"]:
                    self._enqueue(conn, "quarantine", path, info["smugmug_image_id"], info.get("smugmug_album_id"))
                conn.execute("UPDATE files SET status='quarantined' WHERE local_path=?", (path,))

        conn.commit()
        return summary

    def _fetch_remote_images(self, album_key: Optional[str]) -> tuple:
        by_name: dict = {}
        by_hash: dict = {}
        if not album_key or not self.client:
            return by_name, by_hash
        try:
            for img in self.client.get_album_images(album_key):
                fname = img.get("FileName", "")
                md5 = img.get("ArchivedMD5") or img.get("MD5Sum", "")
                if fname:
                    by_name[fname] = img
                if md5:
                    by_hash[md5] = img
        except Exception:
            pass
        return by_name, by_hash

    def _handle_new(self, conn, local_rel, local_hash, local_mtime,
                    remote_img, remote_hash, album_key, hash_to_paths, summary):
        if remote_img and local_hash == remote_hash:
            # bootstrap match
            conn.execute(
                """INSERT OR REPLACE INTO files
                   (local_path, local_hash, local_mtime, smugmug_image_id, smugmug_album_id,
                    smugmug_hash, status, last_synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'in_sync', datetime('now'))""",
                (local_rel, local_hash, local_mtime,
                 remote_img.get("ImageKey"), album_key, remote_hash),
            )
            summary["in_sync"] += 1
        elif remote_img:
            # present on both sides, different content → conflict
            conn.execute(
                """INSERT OR REPLACE INTO files
                   (local_path, local_hash, local_mtime, smugmug_image_id, smugmug_album_id,
                    smugmug_hash, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'conflict')""",
                (local_rel, local_hash, local_mtime,
                 remote_img.get("ImageKey"), album_key, remote_hash),
            )
            self._enqueue(conn, "flag_conflict", local_rel, remote_img.get("ImageKey"), album_key)
            summary["conflict"] += 1
        elif local_hash in hash_to_paths:
            # same hash known at a different path → rename
            old_path = hash_to_paths[local_hash][0]
            conn.execute(
                "UPDATE files SET local_path=?, local_mtime=?, status='in_sync' WHERE local_path=?",
                (local_rel, local_mtime, old_path),
            )
            self._enqueue(conn, "rename_remote", local_rel, None, album_key)
            summary["rename"] += 1
        else:
            conn.execute(
                """INSERT OR REPLACE INTO files (local_path, local_hash, local_mtime, smugmug_album_id, status)
                   VALUES (?, ?, ?, ?, 'local_only')""",
                (local_rel, local_hash, local_mtime, album_key),
            )
            self._enqueue(conn, "upload", local_rel, None, album_key)
            summary["upload"] += 1

    def _handle_known(self, conn, local_rel, local_hash, local_mtime,
                      remote_img, remote_hash, prev, summary):
        prev_local_hash = prev["local_hash"]
        prev_remote_hash = prev["smugmug_hash"]
        local_changed = local_hash != prev_local_hash
        remote_changed = bool(remote_img) and remote_hash != prev_remote_hash

        if local_changed and remote_changed:
            conn.execute(
                "UPDATE files SET local_hash=?, local_mtime=?, smugmug_hash=?, status='conflict' WHERE local_path=?",
                (local_hash, local_mtime, remote_hash, local_rel),
            )
            self._enqueue(conn, "flag_conflict", local_rel,
                          remote_img.get("ImageKey") if remote_img else None,
                          prev.get("smugmug_album_id"))
            summary["conflict"] += 1
        elif local_changed:
            conn.execute(
                "UPDATE files SET local_hash=?, local_mtime=?, status='local_only' WHERE local_path=?",
                (local_hash, local_mtime, local_rel),
            )
            self._enqueue(conn, "upload", local_rel, prev.get("smugmug_image_id"), prev.get("smugmug_album_id"))
            summary["upload"] += 1
        elif remote_changed:
            conn.execute(
                "UPDATE files SET smugmug_hash=?, status='remote_only' WHERE local_path=?",
                (remote_hash, local_rel),
            )
            self._enqueue(conn, "download", local_rel,
                          remote_img.get("ImageKey") if remote_img else None,
                          prev.get("smugmug_album_id"))
            summary["download"] += 1
        else:
            conn.execute(
                "UPDATE files SET local_hash=?, local_mtime=?, status='in_sync' WHERE local_path=?",
                (local_hash, local_mtime, local_rel),
            )
            summary["in_sync"] += 1

    def _enqueue(self, conn, action: str, local_path: str,
                 image_id: Optional[str], album_id: Optional[str]) -> None:
        exists = conn.execute(
            "SELECT id FROM queue WHERE action=? AND local_path=? AND status='pending'",
            (action, local_path),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO queue (action, local_path, smugmug_image_id, smugmug_album_id)
                   VALUES (?, ?, ?, ?)""",
                (action, local_path, image_id, album_id),
            )
