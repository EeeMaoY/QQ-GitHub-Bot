"""
@Author         : yanyongyu
@Date           : 2022-09-05 09:50:07
@LastEditors    : yanyongyu
@LastEditTime   : 2024-03-05 14:55:11
@Description    : User model
@GitHub         : https://github.com/yanyongyu
"""

__author__ = "yanyongyu"

from datetime import datetime, timedelta, UTC
from typing import Self, cast

from pydantic import TypeAdapter
from sqlalchemy import String, select, update, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from nonebot_plugin_orm import Model, get_session
from sqlalchemy.dialects.postgresql import JSONB, insert

from src.providers.platform import UserInfo

ACCESS_TOKEN_REFRESH_AHEAD: timedelta = timedelta(minutes=5)
"""access_token 剩余有效期低于该阈值时提前刷新。

留出窗口是为了给命令执行本身留出余量：刷新只需要一次网络往返，
但命令跑起来的耗时不可控，贴着过期时间刷新容易在命令执行到一半时令牌失效。
"""


def utcnow() -> datetime:
    """当前 UTC 时间（带时区，与 DateTime(timezone=True) 的取值对齐）"""
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """兜底：万一取回不带时区的 datetime，按 UTC 解释"""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class User(Model):
    """User model"""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[dict] = mapped_column(JSONB, unique=True, index=True, nullable=False)
    access_token: Mapped[str | None] = mapped_column(String(), nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(), nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_user_info(self) -> UserInfo:
        """Convert to user info"""
        return TypeAdapter(UserInfo).validate_python(self.user)

    @classmethod
    async def from_info(cls, info: UserInfo) -> Self | None:
        """Get user model by user info"""
        sql = select(cls).where(cls.user == info.model_dump())
        async with get_session() as session:
            result = await session.execute(sql)
            return result.scalar_one_or_none()

    @classmethod
    async def get_by_id(cls, user_id: int) -> Self | None:
        """Get user model by primary key.

        Used to re-read the token pair after a refresh, so that a concurrent
        refresh performed by another worker can be picked up.
        """
        sql = select(cls).where(cls.id == user_id)
        async with get_session() as session:
            result = await session.execute(sql)
            return result.scalar_one_or_none()

    def access_token_needs_refresh(self) -> bool:
        """Whether the access token is about to expire and can be refreshed.

        Returns False when there is no expiry info (old rows, or the GitHub App
        does not issue expiring user tokens), keeping the historical behaviour of
        using the token until a 401 tells the user to run /auth.
        """
        if not self.access_token or not self.refresh_token:
            return False

        expires_at = ensure_utc(self.access_token_expires_at)
        if expires_at is None:
            return False

        refresh_expires_at = ensure_utc(self.refresh_token_expires_at)
        if refresh_expires_at is not None and utcnow() >= refresh_expires_at:
            return False

        return utcnow() >= expires_at - ACCESS_TOKEN_REFRESH_AHEAD

    def adopt_tokens_from(self, other: Self) -> None:
        """Copy the token fields of another record into this in-memory object."""
        self.access_token = other.access_token
        self.refresh_token = other.refresh_token
        self.access_token_expires_at = other.access_token_expires_at
        self.refresh_token_expires_at = other.refresh_token_expires_at

    @classmethod
    async def create_or_update_by_info(
        cls,
        info: UserInfo | Self,
        access_token: str | None,
        refresh_token: str | None = None,
        access_token_expires_at: datetime | None = None,
        refresh_token_expires_at: datetime | None = None,
    ) -> Self:
        """Create or update user model by user info"""
        if isinstance(info, cls):
            info = info.to_user_info()

        info = cast(UserInfo, info)

        insert_sql = insert(cls).values(
            user=info.model_dump(),
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=refresh_token_expires_at,
        )
        update_sql = insert_sql.on_conflict_do_update(
            index_elements=[cls.user],
            set_={
                "access_token": insert_sql.excluded.access_token,
                "refresh_token": insert_sql.excluded.refresh_token,
                "access_token_expires_at": (
                    insert_sql.excluded.access_token_expires_at
                ),
                "refresh_token_expires_at": (
                    insert_sql.excluded.refresh_token_expires_at
                ),
            },
        ).returning(cls)

        async with get_session() as session:
            result = await session.execute(update_sql)
            user = result.scalar_one()
            await session.commit()
            await session.refresh(user)
            return user

    @classmethod
    async def update_tokens(
        cls,
        user_id: int,
        *,
        access_token: str,
        refresh_token: str | None,
        access_token_expires_at: datetime | None,
        refresh_token_expires_at: datetime | None,
        expected_refresh_token: str,
    ) -> bool:
        """Store a freshly refreshed token pair, conditionally.

        A refresh token is single-use, so whenever two requests race only one of
        them can win. The `WHERE refresh_token = expected` guard makes the loser
        a no-op instead of clobbering the winner's pair; the caller then re-reads
        the row and adopts it. This also guards across processes, which an
        in-process asyncio lock cannot do under gunicorn's multiple workers.
        """
        sql = (
            update(cls)
            .where(cls.id == user_id, cls.refresh_token == expected_refresh_token)
            .values(
                access_token=access_token,
                refresh_token=refresh_token,
                access_token_expires_at=access_token_expires_at,
                refresh_token_expires_at=refresh_token_expires_at,
            )
        )
        async with get_session() as session:
            result = await session.execute(sql)
            await session.commit()
            return bool(result.rowcount)

    async def clear_refresh_token(self) -> None:
        """Give up on refreshing, but keep the access token around.

        Clearing the access token too would degrade the next command's message
        from "授权已过期，请 /auth 刷新" to "你还没有绑定 GitHub 帐号", which
        points the user in the wrong direction.
        """
        sql = (
            update(type(self))
            .where(type(self).id == self.id)
            .values(
                refresh_token=None,
                refresh_token_expires_at=None,
                access_token_expires_at=None,
            )
        )
        async with get_session() as session:
            await session.execute(sql)
            await session.commit()

        self.refresh_token = None
        self.refresh_token_expires_at = None
        self.access_token_expires_at = None

    async def unauth(self) -> None:
        """UnAuth user"""
        sql = (
            update(type(self))
            .where(type(self).id == self.id)
            .values(
                access_token=None,
                refresh_token=None,
                access_token_expires_at=None,
                refresh_token_expires_at=None,
            )
        )
        async with get_session() as session:
            await session.execute(sql)
            await session.commit()

        self.access_token = None
        self.refresh_token = None
        self.access_token_expires_at = None
        self.refresh_token_expires_at = None

    @classmethod
    async def unauth_by_info(cls, info: UserInfo) -> bool:
        """UnAuth user by user info"""
        sql = (
            update(cls)
            .where(cls.user == info.model_dump())
            .values(
                access_token=None,
                refresh_token=None,
                access_token_expires_at=None,
                refresh_token_expires_at=None,
            )
        )
        async with get_session() as session:
            result = await session.execute(sql)
            return bool(result.rowcount)
