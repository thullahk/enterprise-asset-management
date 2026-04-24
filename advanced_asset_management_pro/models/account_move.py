# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Asset link
    asset_id = fields.Many2one(
        'account.asset', string='Asset', index=True,
        ondelete='cascade', copy=False,
        check_company=True)
    # Depreciation values
    depreciation_value = fields.Monetary(
        string='Depreciation Value', compute='_compute_depreciation_value', store=True)
    asset_remaining_value = fields.Monetary(
        string='Remaining Value',
        compute='_compute_depreciation_cumulative_value', store=True)
    asset_depreciated_value = fields.Monetary(
        string='Cumulative Depreciation',
        compute='_compute_depreciation_cumulative_value', store=True)
    asset_manually_modified = fields.Boolean(
        string='Manually Modified')
    asset_value_change = fields.Boolean(
        string='Value Change')
    # Dates / days
    asset_depreciation_beginning_date = fields.Date(
        string='Depreciation Begin Date')
    asset_number_days = fields.Integer(
        string='Number of Days')
        
    # Dummy fields to prevent "Missing field string inside JS" crashes if account_reports views are loaded
    tax_closing_end_date = fields.Date(string="Tax Closing End Date")
    tax_report_control_error = fields.Boolean(string="Tax Report Control Error")
    
    # Ghost fields from dgz_nazme to bypass UI crashes
    delivery_date = fields.Date("Delivery date")
    delivery_ref = fields.Char("Delivery Reference")
    payment_term_id = fields.Many2one('account.payment.term', string="Payment Terms")
    source_ref = fields.Char("Source ref")
    supplier_copy = fields.Many2many('ir.attachment', string="Vendor Bill", relation="dummy_supplier_copy_rel_move")
    paid_by_employee = fields.Boolean(string="Paid by Employee")
    employee_id = fields.Many2one('hr.employee', string="Employee")
    employee_entry_created = fields.Boolean(default=False)
    sale_order_id = fields.Many2one('sale.order', string="Sale Order")
    sale_ref = fields.Char("Sale ref")

    # Ghost fields from dgz_invoice_recurring, account_accountant & dgz_payment_term_recurring
    schedule_next_month = fields.Boolean(string="Schedule Next Month")
    url = fields.Char(string="URL")
    payment_state_before_switch = fields.Char(string="Payment State Before Switch")
    payment_due_date = fields.Date(string="Payment Due Date")
    
    # Display helpers
    asset_ids = fields.One2many(
        'account.asset', string='Assets',
        compute="_compute_asset_ids")
    asset_id_display_name = fields.Char(
        compute="_compute_asset_ids")
    count_asset = fields.Integer(compute="_compute_asset_ids")
    draft_asset_exists = fields.Boolean(compute="_compute_asset_ids")

    @api.depends('line_ids.debit', 'line_ids.credit', 'asset_id')
    def _compute_depreciation_value(self):
        for move in self:
            asset = move.asset_id
            if not asset:
                move.depreciation_value = 0
                continue
            depr_account = asset.account_depreciation_id
            depreciation_line = move.line_ids.filtered(
                lambda l: l.account_id == depr_account)
            if depreciation_line:
                move.depreciation_value = sum(depreciation_line.mapped('credit')) - sum(depreciation_line.mapped('debit'))
            else:
                move.depreciation_value = 0

    @api.depends('asset_id', 'depreciation_value',
                 'asset_id.total_depreciable_value',
                 'asset_id.already_depreciated_amount_import')
    def _compute_depreciation_cumulative_value(self):
        assets = self.mapped('asset_id')
        for asset in assets:
            depreciable_value = asset.total_depreciable_value
            imported_amount = asset.already_depreciated_amount_import
            cumulative = 0
            # Sequence calculation for the board: Draft and Posted moves must both be included
            for mv in asset.depreciation_move_ids.sorted(key=lambda r: (r.date or fields.Date.today(), r.id)):
                cumulative += mv.depreciation_value
                mv.asset_depreciated_value = cumulative
                mv.asset_remaining_value = depreciable_value - (cumulative + imported_amount)
        
        for mv in self:
            if not mv.asset_id:
                mv.asset_depreciated_value = 0
                mv.asset_remaining_value = 0

    def _compute_asset_ids(self):
        for record in self:
            record.asset_ids = record.line_ids.asset_ids
            record.count_asset = len(record.asset_ids)
            record.asset_id_display_name = _('Asset')
            record.draft_asset_exists = bool(
                record.asset_ids.filtered(
                    lambda x: x.state == "draft"))

    def action_open_asset_id(self):
        return self.asset_id.open_asset(['form'])

    def action_open_asset_ids(self):
        return self.asset_ids.open_asset(['tree', 'form'])

    def _reverse_moves(self, default_values_list=None, cancel=False):
        if default_values_list is None:
            default_values_list = [{}] * len(self)
        for move, default_values in zip(self, default_values_list):
            if move.asset_id:
                first_draft = min(
                    move.asset_id.depreciation_move_ids.filtered(
                        lambda m: m.state == 'draft'),
                    key=lambda m: m.date, default=None)
                if first_draft:
                    raise UserError(_(
                        "You cannot reverse a depreciation entry when "
                        "there are still unposted depreciation entries "
                        "with a prior date. Please post or delete the "
                        "following entry first: %s", first_draft.name))
                elif move.asset_id.state != 'close':
                    msg = _(
                        'Depreciation entry %s reversed', move.name)
                    last_date = max(
                        move.asset_id.depreciation_move_ids.mapped('date'))
                    method_period = move.asset_id.method_period
                    new_entry = self.env['account.move']._prepare_move_for_asset_depreciation({
                        'asset_id': move.asset_id,
                        'date': last_date + relativedelta(
                            months=int(method_period)),
                        'depreciation_beginning_date':
                            last_date + relativedelta(days=1),
                        'amount': abs(move.depreciation_value),
                        'asset_number_days': (
                            int(method_period) * 30),
                    })
                    move.asset_id.message_post(body=msg)
                    default_values['asset_id'] = move.asset_id.id
        return super()._reverse_moves(default_values_list, cancel)

    def button_cancel(self):
        for move in self:
            if move.asset_ids:
                if any(asset.state != 'draft'
                       for asset in move.asset_ids):
                    raise UserError(_(
                        'You cannot cancel a move linked to '
                        'an asset that is not a draft'))
                move.asset_ids.filtered(
                    lambda x: x.state == 'draft').unlink()
        return super().button_cancel()

    def _unlink_or_reverse(self):
        draft = self.filtered(lambda mv: mv.state == 'draft')
        non_draft = self - draft
        draft.with_context(force_delete=True).unlink()
        non_draft._reverse_moves()

    @staticmethod
    def _prepare_move_for_asset_depreciation(vals):
        missing = (
            {'asset_id', 'amount', 'depreciation_beginning_date',
             'date', 'asset_number_days'} - set(vals))
        if missing:
            raise UserError(_(
                'Some fields are missing {}'.format(', '.join(missing))))
        asset = vals['asset_id']
        amount = vals['amount']
        depreciation_beginning_date = vals['depreciation_beginning_date']
        date = vals['date']
        asset_number_days = vals['asset_number_days']

        move_ref = _(
            '%(name)s: Depreciation',
            name=asset.name)

        depreciation_line = (0, 0, {
            'name': move_ref,
            'account_id': asset.account_depreciation_id.id,
            'debit': 0.0 if float_compare(
                amount, 0.0, precision_rounding=asset.currency_id.rounding
            ) > 0 else -amount,
            'credit': amount if float_compare(
                amount, 0.0, precision_rounding=asset.currency_id.rounding
            ) > 0 else 0.0,
            'currency_id': asset.currency_id.id,
        })
        expense_line = (0, 0, {
            'name': move_ref,
            'account_id': asset.account_depreciation_expense_id.id,
            'debit': amount if float_compare(
                amount, 0.0, precision_rounding=asset.currency_id.rounding
            ) > 0 else 0.0,
            'credit': 0.0 if float_compare(
                amount, 0.0, precision_rounding=asset.currency_id.rounding
            ) > 0 else -amount,
            'currency_id': asset.currency_id.id,
        })

        return {
            'ref': move_ref,
            'date': date,
            'journal_id': asset.journal_id.id,
            'line_ids': [depreciation_line, expense_line],
            'auto_post': 'no',
            'asset_id': asset.id,
            'asset_depreciation_beginning_date': depreciation_beginning_date,
            'asset_number_days': asset_number_days,
            'depreciation_value': amount,
            'move_type': 'entry',
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    asset_ids = fields.Many2many(
        'account.asset', 'asset_move_line_rel', 'line_id', 'asset_id',
        string='Related Assets', copy=False)

    # Ghost fields to bypass UI crashes caused by uninstalled accounting_only_erp-master modules
    image_128 = fields.Image(string="Image")
    expected_pay_date = fields.Date(string='Expected Date')
    followup_line_id = fields.Many2one('res.partner', string='Follow-up Level', copy=False) # Dummy relation to avoid missing models
    last_followup_date = fields.Date(string='Latest Follow-up', copy=False)
    next_action_date = fields.Date(string='Next Action Date')
    # Note: invoice_date and invoice_origin might natively exist or not depending on Odoo version length, safe to re-declare as simple fields
    invoice_date = fields.Date(string='Invoice Date')
    invoice_origin = fields.Char(string='Invoice Origin')
    filter_cash_basis = fields.Boolean(string="Filter Cash Basis")
    filter_analytic_groupby = fields.Boolean(string="Filter Analytic Groupby")
    move_attachment_ids = fields.Many2many('ir.attachment', string="Move Attachments", relation="dummy_move_attachment_rel_line")

