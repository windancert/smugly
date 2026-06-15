from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from smugly import config
from smugly.smugmug import SmugMugClient

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="smugly/templates")


def _make_client() -> SmugMugClient | None:
    s = config.settings
    if not s.smugmug_api_key or not s.smugmug_api_secret:
        return None
    tokens = config.load_tokens()
    return SmugMugClient(
        s.smugmug_api_key, s.smugmug_api_secret,
        tokens.get("oauth_token", ""),
        tokens.get("oauth_token_secret", ""),
    )


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = config.load_app_config()
    tokens = config.load_tokens()
    authed = bool(tokens.get("oauth_token"))
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "db_name": cfg.get("db_name", ".sync_state.db"),
        "authed": authed,
        "api_key": config.settings.smugmug_api_key,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/save", response_class=HTMLResponse)
async def save_settings(db_name: str = Form(...)):
    cfg = config.load_app_config()
    cfg["db_name"] = db_name.strip() or ".sync_state.db"
    config.save_app_config(cfg)
    return RedirectResponse("/settings?success=1", status_code=303)


@router.get("/oauth/start")
async def oauth_start(request: Request):
    client = _make_client()
    if not client:
        return RedirectResponse("/settings?error=no_keys", status_code=303)
    callback = str(request.url_for("oauth_callback"))
    try:
        token = client.get_request_token(callback)
    except Exception as e:
        return RedirectResponse(f"/settings?error={str(e)[:80]}", status_code=303)
    config.save_oauth_temp({
        "oauth_token": token["oauth_token"],
        "oauth_token_secret": token["oauth_token_secret"],
    })
    return RedirectResponse(client.get_authorize_url(token["oauth_token"]))


@router.get("/oauth/callback", name="oauth_callback")
async def oauth_callback(oauth_token: str = "", oauth_verifier: str = ""):
    temp = config.load_oauth_temp()
    if not temp or temp.get("oauth_token") != oauth_token:
        return RedirectResponse("/settings?error=oauth_mismatch", status_code=303)
    client = _make_client()
    try:
        access = client.get_access_token(
            oauth_token, temp["oauth_token_secret"], oauth_verifier
        )
        config.save_tokens({
            "oauth_token": access["oauth_token"],
            "oauth_token_secret": access["oauth_token_secret"],
        })
        config.clear_oauth_temp()
    except Exception as e:
        return RedirectResponse(f"/settings?error={str(e)[:80]}", status_code=303)
    return RedirectResponse("/settings?success=authorized", status_code=303)


@router.post("/oauth/revoke")
async def oauth_revoke():
    config.save_tokens({})
    return RedirectResponse("/settings?success=revoked", status_code=303)
