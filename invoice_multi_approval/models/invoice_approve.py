from odoo import fields, models, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class InvoiceApprove(models.Model):
    _inherit = 'account.move'

    state = fields.Selection(
        selection_add=[
            ('finance_approval', 'Finance Approval'),
            ('md_approval', 'Managing Director Approval'),
            ('line_manager', 'Line Manager Approval'),
            ('internal_control', 'Internal Control Check'),
            ('management', 'Management Approval'),
            ('treasury', 'Treasury Confirmation'),
            ('rejected', 'Rejected'),
        ],
        ondelete={
            'finance_approval': 'set default',
            'md_approval': 'set default',
            'line_manager': 'set default',
            'internal_control': 'set default',
            'management': 'set default',
            'treasury': 'set default',
            'rejected': 'set default',
        }
    )

    _STAGE_GROUPS = {
        'finance_approval': 'invoice_multi_approval.group_finance_approver',
        'md_approval': 'invoice_multi_approval.group_managing_director',
        'line_manager': 'invoice_multi_approval.group_journal_line_manager',
        'internal_control': 'invoice_multi_approval.group_journal_internal_control',
        'management': 'invoice_multi_approval.group_journal_management',
        'treasury': 'invoice_multi_approval.group_journal_treasury',
    }

    def _get_approval_group(self, stage):
        group_xmlid = self._STAGE_GROUPS.get(stage)
        if not group_xmlid:
            _logger.warning("No approval group is configured for stage '%s'.", stage)
            return None
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not group:
            _logger.warning("Approval group for stage '%s' not found.", stage)
        return group

    def _ensure_stage_group(self, stage):
        group = self._get_approval_group(stage)
        if not group:
            raise UserError(_("No approval group is configured for the %s stage.") % dict(self._fields['state'].selection).get(stage, stage))
        if group not in self.env.user.groups_id:
            raise UserError(_("You don't have permission to approve at the %s stage.") % dict(self._fields['state'].selection).get(stage, stage))
        return group

    def action_submit_for_approval(self):
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                raise UserError(_("Only invoices and bills can be submitted through this approval workflow."))
            if move.state != 'draft':
                raise UserError(_("Only draft invoices can be submitted for approval."))
            if move.message_attachment_count < 1:
                raise UserError(_("Attach at least one supporting document before submitting the invoice."))
            move.write({'state': 'line_manager'})

    def action_invoice_approve(self):
        """Move invoices and journal entries through their approval stages."""
        for move in self:
            if move.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                if move.state == 'line_manager':
                    move._ensure_stage_group('line_manager')
                    move.write({'state': 'internal_control'})
                elif move.state == 'internal_control':
                    move._ensure_stage_group('internal_control')
                    move.write({'state': 'management'})
                elif move.state == 'management':
                    move._ensure_stage_group('management')
                    move.write({'state': 'treasury'})
                elif move.state == 'treasury':
                    move._ensure_stage_group('treasury')
                    move.action_post()
                else:
                    raise UserError(_("Approval is not allowed in the current invoice state."))
                continue

            if move.state == 'draft':
                move._ensure_stage_group('finance_approval')
                move.write({'state': 'finance_approval'})
            elif move.state == 'finance_approval':
                move._ensure_stage_group('finance_approval')
                move.write({'state': 'md_approval'})
            elif move.state == 'md_approval':
                move._ensure_stage_group('md_approval')
                move.action_post()
            else:
                raise UserError(_("The journal entry is already in a final state (Posted or Rejected)."))

    def action_post(self):
        """Allow posting only from the last approval stage for protected moves."""
        for move in self:
            _logger.info("Attempting to post move %s with state: %s", move.name, move.state)
            if move.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund') and move.state != 'treasury':
                raise UserError(
                    _("You can only post invoices after Treasury confirmation. Current state: %s") % move.state
                )
            if move.move_type == 'entry' and move.state not in ['draft', 'md_approval']:
                raise UserError(
                    _("You can only post journal entries that are in Draft or Managing Director Approval state. Current state: %s") % move.state
                )
        return super().action_post()

    def action_refuse(self):
        """Refuse the move and send it back to draft state."""
        for move in self:
            if move.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
                allowed_states = ['line_manager', 'internal_control', 'management', 'treasury']
            else:
                allowed_states = ['finance_approval', 'md_approval']
            if move.state not in allowed_states:
                raise UserError(_("You can only refuse the move at the approval stages."))
            move.write({'state': 'draft'})
            _logger.info("Move %s has been refused and sent back to draft.", move.name)

