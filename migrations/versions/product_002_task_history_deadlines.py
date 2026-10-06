"""Add explicit task deadlines and evidence-backed occurrence history."""
from datetime import datetime, timezone
from uuid import uuid4
from alembic import op
import sqlalchemy as sa

revision = 'product_002'
down_revision = 'product_001'
branch_labels = None
depends_on = None


def upgrade():
    # Existing databases are upgraded; the original product revision stays intact.
    with op.batch_alter_table('tracker_sync') as batch:
        batch.add_column(sa.Column('due_date', sa.Date(), nullable=True))
        batch.add_column(sa.Column('deadline_updated_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('deadline_updated_by', sa.String(64), nullable=True))
        batch.create_unique_constraint('uq_task_scope', ['id','platform','workspace_id','channel_id'])
    op.create_table('task_occurrences',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('task_id',sa.String(36),nullable=False),
        sa.Column('digest_id',sa.String(36),nullable=False),
        sa.Column('entry_id',sa.String(36),nullable=False),
        sa.Column('entry_platform',sa.String(20),nullable=False),
        sa.Column('platform',sa.String(20),nullable=False),
        sa.Column('workspace_id',sa.String(64),nullable=False),
        sa.Column('channel_id',sa.String(64),nullable=False),
        sa.Column('quote',sa.Text(),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('task_id','entry_id',name='uq_task_occurrence_source'),
        sa.ForeignKeyConstraint(['task_id','platform','workspace_id','channel_id'],
            ['tracker_sync.id','tracker_sync.platform','tracker_sync.workspace_id','tracker_sync.channel_id'],
            name='fk_task_occurrences_task_id_tracker_sync',ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['digest_id','platform','workspace_id','channel_id'],
            ['daily_digests.id','daily_digests.platform','daily_digests.workspace_id','daily_digests.channel_id'],
            name='fk_task_occurrences_digest_id_daily_digests',ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id','entry_platform','workspace_id','channel_id'],
            ['standup_entries.id','standup_entries.platform','standup_entries.workspace_id','standup_entries.channel_id'],
            name='fk_task_occurrences_entry_id_standup_entries',ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['digest_id','entry_id'],['digest_sources.digest_id','digest_sources.entry_id'],
            name='fk_task_occurrences_digest_id_digest_sources',ondelete='CASCADE'),
        sa.CheckConstraint('length(quote) BETWEEN 1 AND 4000',name=op.f('ck_task_occurrences_quote_length')))
    op.create_index('ix_task_occurrence_history','task_occurrences',['task_id','recorded_at'])
    if op.get_context().as_sql:
        # Offline SQL creates schema only. Run the normal online migration for backfill.
        return
    conn=op.get_bind()
    metadata=sa.MetaData()
    tasks=sa.Table('tracker_sync',metadata,autoload_with=conn)
    entries=sa.Table('standup_entries',metadata,autoload_with=conn)
    membership=sa.Table('digest_sources',metadata,autoload_with=conn)
    occurrences=sa.Table('task_occurrences',metadata,autoload_with=conn)
    for task in conn.execute(sa.select(tasks)).mappings():
        # Only evidence still present in the old payload can be reconstructed.
        # Do not manufacture lost past repeats or assign guessed deadlines.
        payload=task['payload']
        if not isinstance(payload,dict):
            continue
        seen=set()
        for evidence in payload.get('evidence',[]):
            if not isinstance(evidence,dict):
                continue
            identity=evidence.get('entry_id');quote=evidence.get('quote')
            if identity in seen or not isinstance(quote,str) or not 1 <= len(quote) <= 4000:
                continue
            entry=conn.execute(sa.select(entries).where(entries.c.id==identity,
                entries.c.workspace_id==task['workspace_id'],entries.c.channel_id==task['channel_id'])).mappings().first()
            member=conn.execute(sa.select(membership.c.entry_id).where(
                membership.c.digest_id==task['digest_id'],membership.c.entry_id==identity)).first()
            if entry is None or member is None or quote not in entry['raw_text']:
                continue
            seen.add(identity)
            conn.execute(occurrences.insert().values(id=str(uuid4()),task_id=task['id'],digest_id=task['digest_id'],
                entry_id=identity,entry_platform=entry['platform'],platform=task['platform'],
                workspace_id=task['workspace_id'],channel_id=task['channel_id'],quote=quote,
                recorded_at=datetime.now(timezone.utc)))


def downgrade():
    op.drop_index('ix_task_occurrence_history',table_name='task_occurrences')
    op.drop_table('task_occurrences')
    with op.batch_alter_table('tracker_sync') as batch:
        batch.drop_constraint('uq_task_scope',type_='unique')
        batch.drop_column('deadline_updated_by')
        batch.drop_column('deadline_updated_at')
        batch.drop_column('due_date')
