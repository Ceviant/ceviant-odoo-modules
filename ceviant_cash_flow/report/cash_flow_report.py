from odoo import api, models


class CashFlowReport(models.AbstractModel):
    _name = 'report.ceviant_cash_flow.report_cash_flow'
    _description = 'Statement of Cash Flows report'

    @api.model
    def _get_report_values(self, docids, data=None):
        wizards = self.env['ceviant.cash.flow.wizard'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'ceviant.cash.flow.wizard',
            'docs': wizards,
            'statements': {wizard.id: wizard._statement() for wizard in wizards},
        }


class CashFlowReportXlsx(models.AbstractModel):
    _name = 'report.ceviant_cash_flow.report_cash_flow_xlsx'
    _inherit = 'report.report_xlsx.abstract'
    _description = 'Statement of Cash Flows XLSX'

    def generate_xlsx_report(self, workbook, data, wizards):
        title = workbook.add_format({'bold': True, 'font_size': 14})
        bold = workbook.add_format({'bold': True})
        money = workbook.add_format({'num_format': '#,##0.00;(#,##0.00)'})
        money_bold = workbook.add_format({'num_format': '#,##0.00;(#,##0.00)', 'bold': True, 'top': 1})
        detail = workbook.add_format({'indent': 2, 'font_color': '#555555'})
        detail_money = workbook.add_format({'num_format': '#,##0.00;(#,##0.00)', 'font_color': '#555555'})

        for wizard in wizards:
            st = wizard._statement()
            sheet = workbook.add_worksheet(f'Cash Flows {wizard.date_to}'[:31])
            sheet.set_column(0, 0, 80)
            sheet.set_column(1, 1, 22)
            row = 0
            sheet.write(row, 0, f"{st['company'].name} - Statement of Cash Flows", title)
            row += 1
            sheet.write(row, 0, f"For the period {st['date_from']} to {st['date_to']} "
                                f"({st['currency'].name}){' - includes draft entries' if st['include_draft'] else ''}")
            row += 2
            for section in st['sections']:
                sheet.write(row, 0, section['label'], bold)
                row += 1
                for line in section['lines']:
                    sheet.write(row, 0, line['label'])
                    sheet.write_number(row, 1, line['amount'], money)
                    row += 1
                    if wizard.show_accounts:
                        for item in line['details']:
                            sheet.write(row, 0, item['label'], detail)
                            sheet.write_number(row, 1, item['amount'], detail_money)
                            row += 1
                sheet.write(row, 0, section['total_label'], bold)
                sheet.write_number(row, 1, section['total'], money_bold)
                row += 2
            for label, key in (('Net increase / (decrease) in cash and cash equivalents', 'net_change'),
                               ('Cash and cash equivalents at the beginning of the period', 'opening_cash'),
                               ('Cash and cash equivalents at the end of the period', 'closing_cash')):
                sheet.write(row, 0, label, bold)
                sheet.write_number(row, 1, st[key], money_bold)
                row += 1
            if not st['currency'].is_zero(st['difference']):
                row += 1
                sheet.write(row, 0, 'Unreconciled difference (check account classification)', bold)
                sheet.write_number(row, 1, st['difference'], money_bold)
                row += 1
                for item in st['unclassified']:
                    sheet.write(row, 0, item['label'], detail)
                    sheet.write_number(row, 1, item['amount'], detail_money)
                    row += 1
