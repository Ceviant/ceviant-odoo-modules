from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CashFlowWizard(models.TransientModel):
    _name = 'ceviant.cash.flow.wizard'
    _description = 'Statement of Cash Flows'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(string='Start Date', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(month=1, day=1))
    date_to = fields.Date(string='End Date', required=True, default=fields.Date.context_today)
    target_move = fields.Selection([('posted', 'All Posted Entries'), ('all', 'All Entries (incl. draft)')],
                                   string='Target Moves', required=True, default='posted')
    show_accounts = fields.Boolean(string='Show Account Details',
                                   help='List the accounts behind every line of the statement.')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from > wizard.date_to:
                raise UserError(_('The start date must be on or before the end date.'))

    def _statement(self):
        self.ensure_one()
        return self.env['ceviant.cash.flow.statement'].compute(
            self.company_id, self.date_from, self.date_to, include_draft=self.target_move == 'all')

    def action_view(self):
        return self.env.ref('ceviant_cash_flow.action_report_cash_flow_html').report_action(self)

    def action_print_pdf(self):
        return self.env.ref('ceviant_cash_flow.action_report_cash_flow_pdf').report_action(self)

    def action_export_xlsx(self):
        return self.env.ref('ceviant_cash_flow.action_report_cash_flow_xlsx').report_action(self)
