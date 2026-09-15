"""add app allow anonymous

Revision ID: c4d81f0b7a92
Revises: 87cc955248fd
Create Date: 2026-09-14 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d81f0b7a92"
down_revision = "87cc955248fd"
branch_labels = None
depends_on = None


def upgrade():
    # Per-app anonymous flag, second level of the access policy:
    #
    # - access_policy='allow_all'       + allow_anonymous=true  -> any visitor
    #   chats immediately under a randomly generated session_id (historical
    #   behaviour, so `true` is the backward-compatible default).
    # - access_policy='allow_all'       + allow_anonymous=false -> the visitor
    #   must sign in through /oa-login first.
    # - access_policy='deny_all_explicit'                       -> sign in AND
    #   hold an app_access_permissions row; allow_anonymous is inert.
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allow_anonymous",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )


def downgrade():
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.drop_column("allow_anonymous")
