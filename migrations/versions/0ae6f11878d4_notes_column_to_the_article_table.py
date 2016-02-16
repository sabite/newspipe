"""notes column to the article table

Revision ID: 0ae6f11878d4
Revises: 661199d8768a
Create Date: 2016-02-16 22:13:01.100424

"""

# revision identifiers, used by Alembic.
revision = '0ae6f11878d4'
down_revision = '661199d8768a'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('article', sa.Column('notes', sa.String(), nullable=True))


def downgrade():
    op.drop_column('article', 'notes')
