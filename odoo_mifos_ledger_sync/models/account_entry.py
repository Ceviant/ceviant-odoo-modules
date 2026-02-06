from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger('custom_account_entry')

class AccountEntry(models.Model):
    _name = 'custom.account.entry'
    _description = 'Custom Account Entry'

    account_id = fields.Char(string='Account ID', required=True)
    account_name = fields.Char(string='Account Name', required=True)
    account_type = fields.Char(string='Account Type', required=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)

    account_code = fields.Char(string='Account Code', required=True, unique=True)

    @api.constrains('currency_id')
    def _check_currency(self):
        if not self.currency_id:
            raise ValidationError(_("Currency must be provided."))

    @api.model_create_multi
    def create(self, vals_list):
        _logger.info(f"Creating Account Entries (batch size: {len(vals_list)})")
        return super(AccountEntry, self).create(vals_list)
