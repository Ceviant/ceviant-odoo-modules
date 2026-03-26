from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class ExpenseReport(models.Model):
    _inherit = "hr.expense.sheet"

    def _selection_state(self):
        """Override state selection to relabel existing states."""
        return [
            ('draft', 'To Report'),
            ('submit', 'Line Manager Approval'),
            ('approve', 'Internal Control Approval'),
            ('manager_director', 'Manager Director Approval'),
            ('post', 'Post'),
            ('done', 'Done'),
            ('cancel', 'Cancelled')
        ]

    state = fields.Selection(
        selection=_selection_state,
        string="Status",
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
        default='draft'
    )

    approval_groups = {
        'submit': 'Line Manager',
        'approve': 'Internal Control',
        'manager_director': 'Manager Director',
    }

    def _get_approval_group(self, group_name):
        """Retrieve the group object by name."""
        group = self.env['res.groups'].search([('name', '=', group_name)], limit=1)
        if not group:
            _logger.warning(f"Approval group '{group_name}' not found.")
        return group

    def action_submit_sheet(self):
        """Submit the expense report."""
        for report in self:
            if report.state != 'draft':
                raise UserError(_("Only reports in draft can be submitted."))
            report.write({'state': 'submit'})
            _logger.info(f"Report {report.name} submitted to Line Manager.")

    def action_approve_expense_sheets(self):
        """Approve the expense report based on the current stage and user group."""
        for report in self:
            user = self.env.user
            line_manager_group = self._get_approval_group('Line Manager')
            internal_control_group = self._get_approval_group('Internal Control')
            manager_director_group = self._get_approval_group('Manager Director')

            if report.state == 'submit':
                if line_manager_group not in user.groups_id:
                    raise UserError(_("You don't have permission to approve at the Line Manager stage."))
                report.write({'state': 'approve'})
            elif report.state == 'approve':
                if internal_control_group not in user.groups_id:
                    raise UserError(_("You don't have permission to approve at the Internal Control stage."))
                report.write({'state': 'manager_director'})
            elif report.state == 'manager_director':
                if manager_director_group not in user.groups_id:
                    raise UserError(_("You don't have permission to approve at the Manager Director stage."))
                report.write({'state': 'post'})
            else:
                raise UserError(_("Approval is not allowed in the current state."))

    def action_sheet_move_create(self):
        """Create account move for the expense report."""
        for report in self:
            user = self.env.user
            manager_director_group = self._get_approval_group('Manager Director')

            if report.state == 'manager_director':
                if manager_director_group not in user.groups_id:
                    raise UserError(_("You don't have permission to create account move at the Manager Director stage."))
            super(ExpenseReport, self).action_sheet_move_create()


    def action_reset_draft(self):
        """Reset the report to draft state."""
        for report in self:
            if report.state in ['cancel', 'done']:
                report.write({'state': 'draft'})
                _logger.info(f"Report {report.name} reset to draft.")
            else:
                raise UserError(_("Only 'done' or 'cancel' reports can be reset to draft."))