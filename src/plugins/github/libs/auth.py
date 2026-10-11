"""
@Author         : yanyongyu
@Date           : 2021-03-09 16:30:16
@LastEditors    : yanyongyu
@LastEditTime   : 2024-05-16 18:03:01
@Description    : OAuth lib
@GitHub         : https://github.com/yanyongyu
"""

__author__ = "yanyongyu"

import urllib.parse
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from nonebot import logger
from nonebot.adapters.github import ActionFailed

from src.plugins.github import config
from src.plugins.github.models import User
from src.plugins.github.models.user import utcnow
from src.providers.platform import UserInfo
from src.plugins.github.utils import get_github, get_github_bot
from src.plugins.github.cache import get_state, create_state, delete_state

OAUTH_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"


@dataclass
class OAuthToken:
    """GitHub OAuth 令牌对。

    GitHub App 开启 expiring user tokens 后，access_token 8 小时过期，
    `/login/oauth/access_token` 会一并返回 refresh_token（6 个月有效）。没开启
    时 `refresh_token` 为 None，此时退化成改动前的行为：令牌一直用到失效为止。
    """

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    refresh_token_expires_in: int | None = None

    # 换算成绝对时间的基准点。它必须和「拿到响应的那一刻」绑定，所以在这里
    # 取一次并复用，否则 access_token_expires_at 的两次读取会漂移。
    fetched_at: datetime = field(default_factory=utcnow)

    @staticmethod
    def _expires_at(base: datetime, seconds: int | None) -> datetime | None:
        if seconds is None or seconds <= 0:
            return None
        return base + timedelta(seconds=seconds)

    @property
    def access_token_expires_at(self) -> datetime | None:
        return self._expires_at(self.fetched_at, self.expires_in)

    @property
    def refresh_token_expires_at(self) -> datetime | None:
        return self._expires_at(self.fetched_at, self.refresh_token_expires_in)


def _as_int(value: object) -> int | None:
    """GitHub 有时把数字写成字符串，宽松转换一下"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_token_payload(payload: dict[str, Any], *, context: str) -> OAuthToken | None:
    """把令牌接口的响应解析成 OAuthToken。

    注意：这个接口出错时 HTTP 状态码依然是 200，错误信息放在 JSON 的
    `error` / `error_description` 里，所以判断成败不能只看状态码。
    """
    if error := payload.get("error"):
        logger.warning(
            f"Failed to {context}: {error} - {payload.get('error_description')}"
        )
        return None

    access_token = payload.get("access_token")
    if not access_token:
        logger.warning(f"Failed to {context}: no access_token in response")
        return None

    return OAuthToken(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=_as_int(payload.get("expires_in")),
        refresh_token_expires_in=_as_int(payload.get("refresh_token_expires_in")),
    )


async def create_auth_link(info: UserInfo) -> str:
    """Create oauth link"""
    query = {
        "client_id": config.github_app.client_id,
        "state": await create_state(info),
    }
    return f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(query)}"


async def consume_state(state_id: str) -> UserInfo | None:
    """Get oauth state data"""
    if user := await get_state(state_id):
        await delete_state(state_id)
        return user


async def create_auth_user(info: UserInfo, token: OAuthToken) -> User:
    """Create oauth user model"""
    return await User.create_or_update_by_info(
        info,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        access_token_expires_at=token.access_token_expires_at,
        refresh_token_expires_at=token.refresh_token_expires_at,
    )


async def get_token_by_code(code: str) -> OAuthToken | None:
    """Get oauth token by oauth code"""
    github = get_github()
    data = {
        "client_id": config.github_app.client_id,
        "client_secret": config.github_app.client_secret,
        "code": code,
    }
    headers = {"Accept": "application/json"}
    response = await github.arequest(
        "POST",
        OAUTH_ACCESS_TOKEN_URL,
        json=data,
        headers=headers,
    )  # type: ignore
    return _parse_token_payload(response.json(), context="get oauth token by code")


async def refresh_access_token(refresh_token: str) -> OAuthToken | None:
    """Exchange a refresh token for a new token pair.

    refresh_token 是一次性的：换成功后旧的一对（access + refresh）同时作废，
    返回的新 refresh_token 必须立刻落库，否则这次刷新等于把用户的授权烧掉了。
    """
    github = get_github()
    data = {
        "client_id": config.github_app.client_id,
        "client_secret": config.github_app.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    headers = {"Accept": "application/json"}
    response = await github.arequest(
        "POST",
        OAUTH_ACCESS_TOKEN_URL,
        json=data,
        headers=headers,
    )  # type: ignore
    return _parse_token_payload(response.json(), context="refresh github access token")


async def revoke_user_token(access_token: str) -> None:
    """Revoke the user's GitHub authorization.

    优先删整个 grant（`DELETE /applications/{client_id}/grant`）：refresh_token
    挂在同一个授权下，只删 access_token 会留下一个 6 个月有效、还能继续换令牌的
    refresh_token，与「已撤销」的语义不符。该端点不可用时退回删单个 token，行为
    与改动前一致。两者对「本来就不存在」都返回 404，视为已经撤销干净。
    """
    bot = get_github_bot()
    async with bot.as_oauth_app():
        try:
            await bot.rest.apps.async_delete_authorization(
                client_id=config.github_app.client_id, access_token=access_token
            )
            return
        except ActionFailed as e:
            if e.response.status_code != 404:
                raise

        try:
            await bot.rest.apps.async_delete_token(
                client_id=config.github_app.client_id, access_token=access_token
            )
        except ActionFailed as e:
            if e.response.status_code != 404:
                raise


async def refresh_user_token(user: User) -> bool:
    """Make sure the user's access token is usable, refreshing it if needed.

    Returns True when the in-memory `user` now holds a usable access token,
    False when the refresh token is gone for good and the user has to /auth again.

    并发安全靠数据库里的条件更新（`WHERE refresh_token = 旧值`），而不是进程内
    asyncio 锁 —— 这个机器人跑在 gunicorn 多 worker 下，进程内锁跨不了进程，
    给不了任何保证。
    """
    refresh_token = user.refresh_token
    if not refresh_token:
        return False

    token = await refresh_access_token(refresh_token)

    if token is None:
        # 换不到。有两种可能，必须区分开：refresh_token 真的失效了，或者并发下
        # 被别的请求抢先消费掉了。后者的话库里已经是一对新令牌，直接采用就好，
        # 绝不能当成失效去清空 —— 那会把刚拿到的新令牌一起丢掉。
        fresh = await User.get_by_id(user.id)
        if fresh and fresh.refresh_token and fresh.refresh_token != refresh_token:
            user.adopt_tokens_from(fresh)
            logger.info("GitHub token refreshed concurrently, reusing the new one")
            return bool(user.access_token)

        logger.warning("GitHub refresh token is no longer valid, user needs to /auth")
        await user.clear_refresh_token()
        return False

    updated = await User.update_tokens(
        user.id,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        access_token_expires_at=token.access_token_expires_at,
        refresh_token_expires_at=token.refresh_token_expires_at,
        expected_refresh_token=refresh_token,
    )

    if updated:
        user.access_token = token.access_token
        user.refresh_token = token.refresh_token
        user.access_token_expires_at = token.access_token_expires_at
        user.refresh_token_expires_at = token.refresh_token_expires_at
        logger.info("GitHub access token refreshed")
        return True

    # 条件更新落空：另一个进程已经刷过了，采用它写入的那一对
    fresh = await User.get_by_id(user.id)
    if fresh:
        user.adopt_tokens_from(fresh)
    return bool(user.access_token)
