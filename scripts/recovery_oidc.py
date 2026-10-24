"""恢复验收专用OIDC探针：逻辑issuer不变，网络只连接新建的恢复实例。"""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt


class RestoredIdentityTransport(httpx.BaseTransport):
    def __init__(self, original_base: str, target_port: int):
        self.origin = urlsplit(original_base)
        if (
            (self.origin.scheme, self.origin.hostname, self.origin.port)
            != ("http", "127.0.0.1", 8081)
            or not 1024 <= target_port <= 65535
            or target_port in {8081, 8800, 55432, 8765}
        ):
            raise ValueError("恢复探针仅允许新建实例的本机端口，禁止路由到源服务")
        self.target_port = target_port
        self.http = httpx.HTTPTransport()

    def handle_request(self, request):
        if (request.url.scheme, request.url.host, request.url.port) != (
            self.origin.scheme,
            self.origin.hostname,
            self.origin.port,
        ):
            raise RuntimeError("恢复探针禁止访问恢复身份实例以外的地址")
        outgoing = httpx.Request(
            request.method,
            request.url.copy_with(port=self.target_port),
            headers=request.headers,
            content=request.content,
            extensions=request.extensions,
        )
        return self.http.handle_request(outgoing)

    def close(self):
        self.http.close()


def restored_login(
    http, settings, username, password, *, target_port, browser_executable=None, token_sink=None
):
    verifier = secrets.token_urlsafe(48)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    from playwright.sync_api import sync_playwright

    authorize = (
        settings.issuer
        + "/protocol/openid-connect/auth?"
        + urlencode(
            {
                "client_id": settings.client_id,
                "redirect_uri": settings.redirect_uri,
                "response_type": "code",
                "scope": "openid office",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    origin = urlsplit(settings.issuer)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=browser_executable,
            headless=True,
        )
        context = browser.new_context(service_workers="block")

        def route_request(route):
            parsed = urlsplit(route.request.url)
            if route.request.url.startswith(settings.redirect_uri + "?"):
                route.fulfill(status=200, body="恢复授权已完成")
            elif (parsed.scheme, parsed.hostname, parsed.port) == (
                origin.scheme,
                origin.hostname,
                origin.port,
            ):
                target = httpx.URL(route.request.url).copy_with(port=target_port)
                response = route.fetch(
                    url=str(target),
                    headers={**route.request.headers, "host": origin.netloc},
                    max_redirects=0,
                    timeout=10000,
                )
                route.fulfill(response=response)
            else:
                route.abort()

        context.route("**/*", route_request)
        try:
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            page.goto(authorize)
            page.locator("#username").fill(username)
            page.locator("#password").fill(password)
            page.locator("#kc-login").click()
            page.wait_for_url(settings.redirect_uri + "**", timeout=15000)
            callback = parse_qs(urlsplit(page.url).query)
        finally:
            browser.close()
    if callback.get("state") != [state] or len(callback.get("code", [])) != 1:
        raise RuntimeError("恢复授权回调校验失败")
    response = http.post(
        settings.issuer + "/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.client_id,
            "code": callback["code"][0],
            "code_verifier": verifier,
            "redirect_uri": settings.redirect_uri,
        },
    )
    response.raise_for_status()
    tokens = response.json()
    access = tokens["access_token"]
    jwks = http.get(settings.issuer + "/protocol/openid-connect/certs")
    jwks.raise_for_status()
    keys = jwks.json()["keys"]

    def signing_key(token):
        kid = jwt.get_unverified_header(token)["kid"]
        return jwt.PyJWK.from_dict(next(key for key in keys if key["kid"] == kid)).key

    claims = jwt.decode(
        access,
        signing_key(access),
        algorithms=["RS256"],
        issuer=settings.issuer,
        audience=settings.audience,
        options={"require": ["exp", "iat", "sub", "sid", "azp"]},
    )
    identity = jwt.decode(
        tokens["id_token"],
        signing_key(tokens["id_token"]),
        algorithms=["RS256"],
        issuer=settings.issuer,
        audience=settings.client_id,
    )
    if identity.get("nonce") != nonce or identity["sub"] != claims["sub"]:
        raise RuntimeError("恢复身份令牌校验失败")
    if token_sink is not None:
        token_sink(tokens)
    return claims, access
