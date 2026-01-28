{
    'name': 'Ledger Sync',
    'version': '2.0.0',
    'category': 'Accounting',
    'summary': 'For handling ledger transactions',
    'description': 'This module provides a custom API endpoint for handling ledger transactions.',
    'author': 'Turog',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'views/custom_journal_entry_views.xml',
        'data/cron_jobs.xml',
    ],
    'installable': True,
    'application': False,
}
