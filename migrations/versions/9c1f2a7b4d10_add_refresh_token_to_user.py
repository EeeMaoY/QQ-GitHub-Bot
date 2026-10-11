"""add refresh token to user

修订 ID: 9c1f2a7b4d10
父修订: 14ab5b50ede5
创建时间: 2026-10-11 00:00:00.000000

GitHub App 的 user-to-server token 8 小时过期，同时会下发一个一次性使用的
refresh_token（6 个月有效）。此前只存了 access_token，过期即失效，用户只能
重新 /auth。这里补上 refresh_token 与两个绝对过期时间，全部可空，老数据不受影响
（取值为 NULL 时行为与改动前完全一致）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c1f2a7b4d10"
down_revision: str | Sequence[str] | None = "14ab5b50ede5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("refresh_token", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "access_token_expires_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True
            )
        )


def downgrade(name: str = "") -> None:
    if name:
        return

    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("refresh_token_expires_at")
        batch_op.drop_column("access_token_expires_at")
        batch_op.drop_column("refresh_token")
