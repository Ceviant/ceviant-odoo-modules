def migrate(cr, version):
    """
    Migration script to add branch_id column to custom_journal_entry table.
    """
    cr.execute("""
        ALTER TABLE custom_journal_entry 
        ADD COLUMN IF NOT EXISTS branch_id VARCHAR;
    """)
    cr.commit()
