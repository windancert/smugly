from requests_oauthlib import OAuth1Session

BASE = "https://api.smugmug.com/api/v2"
REQUEST_TOKEN_URL = "https://api.smugmug.com/services/oauth/1.0a/getRequestToken"
AUTHORIZE_URL = "https://api.smugmug.com/services/oauth/1.0a/authorize"
ACCESS_TOKEN_URL = "https://api.smugmug.com/services/oauth/1.0a/getAccessToken"


class SmugMugClient:
    def __init__(self, api_key: str, api_secret: str, access_token: str = "", access_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_secret = access_secret

    def _session(self) -> OAuth1Session:
        return OAuth1Session(
            self.api_key,
            client_secret=self.api_secret,
            resource_owner_key=self.access_token,
            resource_owner_secret=self.access_secret,
        )

    # ── OAuth flow ──────────────────────────────────────────────────────────

    def get_request_token(self, callback_url: str) -> dict:
        s = OAuth1Session(self.api_key, client_secret=self.api_secret, callback_uri=callback_url)
        return s.fetch_request_token(REQUEST_TOKEN_URL)

    def get_authorize_url(self, request_token: str) -> str:
        return f"{AUTHORIZE_URL}?oauth_token={request_token}&Access=Full&Permissions=Modify"

    def get_access_token(self, request_token: str, request_secret: str, verifier: str) -> dict:
        s = OAuth1Session(
            self.api_key,
            client_secret=self.api_secret,
            resource_owner_key=request_token,
            resource_owner_secret=request_secret,
            verifier=verifier,
        )
        return s.fetch_access_token(ACCESS_TOKEN_URL)

    # ── API reads ────────────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> dict:
        params.setdefault("_accept", "application/json")
        r = self._session().get(f"{BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def get_auth_user(self) -> dict:
        return self._get("!authuser")["Response"]["User"]

    def get_root_node_id(self) -> str:
        user = self.get_auth_user()
        return user["Uris"]["Node"]["Uri"].split("/")[-1]

    def _get_node_children(self, node_id: str) -> list:
        results, start = [], 1
        while True:
            data = self._get(f"/node/{node_id}!children", start=start, count=100)
            batch = data["Response"].get("Node", [])
            results.extend(batch)
            pages = data["Response"].get("Pages", {})
            if not batch or start + len(batch) - 1 >= pages.get("Total", 0):
                break
            start += len(batch)
        return results

    def get_album_images(self, album_key: str) -> list:
        results, start = [], 1
        while True:
            data = self._get(
                f"/album/{album_key}!images",
                start=start, count=100,
                _filter="FileName,MD5Sum,ImageKey,ArchivedMD5",
            )
            batch = data["Response"].get("AlbumImage", [])
            results.extend(batch)
            pages = data["Response"].get("Pages", {})
            if not batch or start + len(batch) - 1 >= pages.get("Total", 0):
                break
            start += len(batch)
        return results

    def walk_node_tree(self, node_id: str, path: str = "") -> list:
        """Return flat list of {path, node_id, album_key, node_type, name}."""
        results = []
        for child in self._get_node_children(node_id):
            name = child.get("Name", "")
            child_path = f"{path}/{name}".lstrip("/")
            child_type = child.get("Type", "")
            child_node_id = child.get("NodeID", "")

            if child_type == "Album":
                album_uri = child.get("Uris", {}).get("Album", {}).get("Uri", "")
                album_key = album_uri.split("/")[-1] if album_uri else ""
                results.append({
                    "path": child_path,
                    "node_id": child_node_id,
                    "album_key": album_key,
                    "node_type": "gallery",
                    "name": name,
                })
            elif child_type == "Folder":
                results.append({
                    "path": child_path,
                    "node_id": child_node_id,
                    "album_key": None,
                    "node_type": "folder",
                    "name": name,
                })
                results.extend(self.walk_node_tree(child_node_id, child_path))
        return results
