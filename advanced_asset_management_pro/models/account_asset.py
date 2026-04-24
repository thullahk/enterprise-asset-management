# -*- coding: utf-8 -*-
import datetime
from dateutil.relativedelta import relativedelta
from markupsafe import Markup
from math import copysign

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, formatLang
from odoo.tools.date_utils import end_of

from collections import defaultdict

DAYS_PER_MONTH = 30
DAYS_PER_YEAR = DAYS_PER_MONTH * 12
MAX_NAME_LENGTH = 50


class AccountAsset(models.Model):
    _name = 'account.asset'
    _description = 'Asset/Revenue Recognition'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    depreciation_entries_count = fields.Integer(
        compute='_compute_counts', string='# Posted Depreciation Entries')
    gross_increase_count = fields.Integer(
        compute='_compute_counts', string='# Gross Increases',
        help="Number of assets made to increase the value of the asset")
    total_depreciation_entries_count = fields.Integer(
        compute='_compute_counts', string='# Depreciation Entries',
        help="Number of depreciation entries (posted or not)")

    name = fields.Char(
        string='Asset Name', compute='_compute_name', store=True,
        required=True, readonly=False, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', store=True)
    state = fields.Selection(
        selection=[
            ('model', 'Model'),
            ('draft', 'Draft'),
            ('open', 'Running'),
            ('paused', 'On Hold'),
            ('close', 'Closed'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', copy=False, default='draft', readonly=True,
        help="When an asset is created, the status is 'Draft'.\n"
             "If the asset is confirmed, the status goes in 'Running' "
             "and the depreciation lines can be posted in the accounting.\n"
             "The 'On Hold' status can be set manually when you want to "
             "pause the depreciation of an asset for some time.\n"
             "You can manually close an asset when the depreciation is over.\n"
             "By cancelling an asset, all depreciation entries will be reversed")
    active = fields.Boolean(default=True)

    # Depreciation params
    method = fields.Selection(
        selection=[
            ('linear', 'Straight Line'),
            ('degressive', 'Declining'),
            ('degressive_then_linear', 'Declining then Straight Line'),
        ],
        string='Method', default='linear',
        help="Choose the method to use to compute the amount of depreciation lines.\n"
             "  * Straight Line: Calculated on basis of: Gross Value / Duration\n"
             "  * Declining: Calculated on basis of: Residual Value * Declining Factor\n"
             "  * Declining then Straight Line: Like Declining but with a minimum "
             "depreciation value equal to the straight line value.")
    method_number = fields.Integer(
        string='Duration', default=5,
        help="The number of depreciations needed to depreciate your asset")
    method_period = fields.Selection(
        [('1', 'Months'), ('12', 'Years')],
        string='Number of Months in a Period', default='12',
        help="The amount of time between two depreciations")
    method_progress_factor = fields.Float(
        string='Declining Factor', default=0.3)
    prorata_computation_type = fields.Selection(
        selection=[
            ('none', 'No Prorata'),
            ('constant_periods', 'Constant Periods'),
            ('daily_computation', 'Based on days per period'),
        ],
        string="Computation", required=True, default='constant_periods')
    prorata_date = fields.Date(
        string='Prorata Date',
        compute='_compute_prorata_date', store=True, readonly=False,
        help='Starting date of the period used in the prorata calculation',
        required=True, copy=True)
    paused_prorata_date = fields.Date(compute='_compute_paused_prorata_date')
    account_asset_id = fields.Many2one(
        'account.account', string='Fixed Asset Account',
        compute='_compute_account_asset_id',
        help="Account used to record the purchase of the asset at its original price.",
        store=True, readonly=False,
        domain="[('account_type', '!=', 'off_balance'), ('company_id', '=', company_id)]")
    account_asset_counterpart_id = fields.Many2one(
        'account.account', string='Asset Counterpart Account',
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]",
        help="Counterpart account used when recording a gross increase.")
    asset_group_id = fields.Many2one(
        'account.asset.group', string='Asset Group',
        tracking=True, index=True)
    account_depreciation_id = fields.Many2one(
        comodel_name='account.account',
        string='Depreciation Account',
        domain="[('account_type', 'not in', "
               "('asset_receivable', 'liability_payable', 'asset_cash', "
               "'liability_credit_card', 'off_balance')), "
               "('deprecated', '=', False), ('company_id', '=', company_id)]",
        help="Account used in the depreciation entries, to decrease the asset value.")
    account_depreciation_expense_id = fields.Many2one(
        comodel_name='account.account',
        string='Expense Account',
        domain="[('account_type', 'not in', "
               "('asset_receivable', 'liability_payable', 'asset_cash', "
               "'liability_credit_card', 'off_balance')), "
               "('deprecated', '=', False), ('company_id', '=', company_id)]",
        help="Account used in the periodical entries, to record a part of the asset as expense.")
    journal_id = fields.Many2one(
        'account.journal', string='Journal',
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
        compute='_compute_journal_id', store=True, readonly=False)

    # Values
    original_value = fields.Monetary(
        string="Original Value", compute='_compute_value',
        store=True, readonly=False)
    book_value = fields.Monetary(
        string='Book Value', readonly=True,
        compute='_compute_book_value', recursive=True, store=True,
        help="Sum of the depreciable value, the salvage value and "
             "the book value of all value increase items")
    value_residual = fields.Monetary(
        string='Depreciable Value', compute='_compute_value_residual')
    salvage_value = fields.Monetary(
        string='Not Depreciable Value',
        help="It is the amount you plan to have that you cannot depreciate.",
        compute="_compute_salvage_value", store=True, readonly=False)
    salvage_value_pct = fields.Float(
        string='Not Depreciable Value Percent',
        help="It is the amount you plan to have that you cannot depreciate.")
    total_depreciable_value = fields.Monetary(
        compute='_compute_total_depreciable_value')
    gross_increase_value = fields.Monetary(
        string="Gross Increase Value",
        compute="_compute_gross_increase_value", compute_sudo=True)
    non_deductible_tax_value = fields.Monetary(
        string="Non Deductible Tax Value",
        compute="_compute_non_deductible_tax_value",
        store=True, readonly=True)
    related_purchase_value = fields.Monetary(
        compute='_compute_related_purchase_value')

    # Links with entries
    depreciation_move_ids = fields.One2many(
        'account.move', 'asset_id', string='Depreciation Lines')
    original_move_line_ids = fields.Many2many(
        'account.move.line', 'asset_move_line_rel', 'asset_id', 'line_id',
        string='Journal Items', copy=False)

    # Dates
    acquisition_date = fields.Date(
        compute='_compute_acquisition_date', store=True, readonly=False, copy=True)
    disposal_date = fields.Date(
        readonly=False, compute="_compute_disposal_date", store=True)

    # model-related fields
    model_id = fields.Many2one(
        'account.asset', string='Model', change_default=True,
        domain="[('company_id', '=', company_id)]")
    account_type = fields.Selection(
        string="Type of the account", related='account_asset_id.account_type')
    display_account_asset_id = fields.Boolean(
        compute="_compute_display_account_asset_id")

    # Capital gain
    parent_id = fields.Many2one(
        'account.asset',
        help="An asset has a parent when it is the result of gaining value")
    children_ids = fields.One2many(
        'account.asset', 'parent_id',
        help="The children are the gains in value of this asset")

    # Adapt for import fields
    already_depreciated_amount_import = fields.Monetary(
        help="In case of an import from another software, you might need to "
             "use this field to have the right depreciation table report.")

    asset_lifetime_days = fields.Float(
        compute="_compute_lifetime_days", recursive=True)
    asset_paused_days = fields.Float(copy=False)

    net_gain_on_sale = fields.Monetary(
        string="Net gain on sale",
        help="Net value of gain or loss on sale of an asset", copy=False)

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------
    @api.depends('company_id')
    def _compute_journal_id(self):
        for asset in self:
            if asset.journal_id and asset.journal_id.company_id == asset.company_id:
                asset.journal_id = asset.journal_id
            else:
                asset.journal_id = self.env['account.journal'].search([
                    ('company_id', '=', asset.company_id.id),
                    ('type', '=', 'general'),
                ], limit=1)

    @api.depends('salvage_value', 'original_value')
    def _compute_total_depreciable_value(self):
        for asset in self:
            asset.total_depreciable_value = asset.original_value - asset.salvage_value

    @api.depends('original_value', 'model_id')
    def _compute_salvage_value(self):
        for asset in self:
            if asset.model_id.salvage_value_pct != 0.0:
                asset.salvage_value = asset.original_value * asset.model_id.salvage_value_pct

    @api.depends('depreciation_move_ids.date', 'state')
    def _compute_disposal_date(self):
        for asset in self:
            if asset.state == 'close':
                dates = asset.depreciation_move_ids.filtered(
                    lambda m: m.date).mapped('date')
                asset.disposal_date = dates and max(dates)
            else:
                asset.disposal_date = False

    @api.depends('original_move_line_ids', 'original_move_line_ids.account_id',
                 'non_deductible_tax_value')
    def _compute_value(self):
        for record in self:
            if not record.original_move_line_ids:
                record.original_value = record.original_value or False
                continue
            if any(line.move_id.state == 'draft'
                   for line in record.original_move_line_ids):
                raise UserError(_("All the lines should be posted"))
            record.original_value = record.related_purchase_value
            if record.non_deductible_tax_value:
                record.original_value += record.non_deductible_tax_value

    @api.depends('original_move_line_ids')
    @api.depends_context('form_view_ref')
    def _compute_display_account_asset_id(self):
        for record in self:
            model_from_coa = (self.env.context.get('form_view_ref')
                              and record.state == 'model')
            record.display_account_asset_id = (
                not record.original_move_line_ids and not model_from_coa)

    @api.depends('account_depreciation_id', 'account_depreciation_expense_id',
                 'original_move_line_ids')
    def _compute_account_asset_id(self):
        for record in self:
            if record.original_move_line_ids:
                if len(record.original_move_line_ids.account_id) > 1:
                    raise UserError(_(
                        "All the lines should be from the same account"))
                record.account_asset_id = record.original_move_line_ids.account_id
            if not record.account_asset_id:
                record._onchange_account_depreciation_id()

    @api.depends('method_number', 'method_period', 'prorata_computation_type')
    def _compute_lifetime_days(self):
        for asset in self:
            if not asset.parent_id:
                if asset.prorata_computation_type == 'daily_computation':
                    asset.asset_lifetime_days = (
                        asset.prorata_date
                        + relativedelta(
                            months=int(asset.method_period) * asset.method_number)
                        - asset.prorata_date
                    ).days
                else:
                    asset.asset_lifetime_days = (
                        int(asset.method_period) * asset.method_number * DAYS_PER_MONTH)
            else:
                if asset.prorata_computation_type == 'daily_computation':
                    parent_end_date = (
                        asset.parent_id.paused_prorata_date
                        + relativedelta(
                            days=int(asset.parent_id.asset_lifetime_days - 1)))
                else:
                    parent_end_date = (
                        asset.parent_id.paused_prorata_date
                        + relativedelta(
                            months=int(
                                asset.parent_id.asset_lifetime_days / DAYS_PER_MONTH),
                            days=int(
                                asset.parent_id.asset_lifetime_days % DAYS_PER_MONTH) - 1))
                asset.asset_lifetime_days = asset._get_delta_days(
                    asset.prorata_date, parent_end_date)

    @api.depends('acquisition_date', 'company_id', 'prorata_computation_type')
    def _compute_prorata_date(self):
        for asset in self:
            if (asset.prorata_computation_type == 'none'
                    and asset.acquisition_date):
                fiscalyear_date = asset.company_id.compute_fiscalyear_dates(
                    asset.acquisition_date).get('date_from')
                asset.prorata_date = fiscalyear_date
            else:
                asset.prorata_date = asset.acquisition_date

    @api.depends('prorata_date', 'prorata_computation_type', 'asset_paused_days')
    def _compute_paused_prorata_date(self):
        for asset in self:
            if asset.prorata_computation_type == 'daily_computation':
                asset.paused_prorata_date = (
                    asset.prorata_date
                    + relativedelta(days=asset.asset_paused_days))
            else:
                asset.paused_prorata_date = (
                    asset.prorata_date
                    + relativedelta(
                        months=int(asset.asset_paused_days / DAYS_PER_MONTH),
                        days=asset.asset_paused_days % DAYS_PER_MONTH))

    @api.depends('original_move_line_ids')
    def _compute_related_purchase_value(self):
        for asset in self:
            related_purchase_value = sum(
                asset.original_move_line_ids.mapped('balance'))
            asset.related_purchase_value = related_purchase_value

    @api.depends('original_move_line_ids')
    def _compute_acquisition_date(self):
        for asset in self:
            asset.acquisition_date = asset.acquisition_date or min(
                [(aml.date) for aml in asset.original_move_line_ids]
                + [fields.Date.today()])

    @api.depends('original_move_line_ids')
    def _compute_name(self):
        for record in self:
            record.name = record.name or (
                record.original_move_line_ids
                and record.original_move_line_ids[0].name or '')

    @api.depends(
        'original_value', 'salvage_value', 'already_depreciated_amount_import',
        'depreciation_move_ids.state',
        'depreciation_move_ids.depreciation_value',
    )
    def _compute_value_residual(self):
        for record in self:
            posted = record.depreciation_move_ids.filtered(
                lambda mv: mv.state == 'posted')
            record.value_residual = (
                record.original_value
                - record.salvage_value
                - record.already_depreciated_amount_import
                - sum(posted.mapped('depreciation_value')))

    @api.depends('value_residual', 'salvage_value', 'children_ids.book_value')
    def _compute_book_value(self):
        for record in self:
            record.book_value = (
                record.value_residual + record.salvage_value
                + sum(record.children_ids.mapped('book_value')))
            if (record.state == 'close'
                    and all(move.state == 'posted'
                            for move in record.depreciation_move_ids)):
                record.book_value -= record.salvage_value

    @api.depends('children_ids.original_value')
    def _compute_gross_increase_value(self):
        for record in self:
            record.gross_increase_value = sum(
                record.children_ids.mapped('original_value'))

    @api.depends('original_move_line_ids')
    def _compute_non_deductible_tax_value(self):
        for record in self:
            record.non_deductible_tax_value = 0.0

    @api.depends('depreciation_move_ids.state', 'parent_id', 'children_ids')
    def _compute_counts(self):
        for asset in self:
            posted = asset.depreciation_move_ids.filtered(
                lambda mv: mv.state == 'posted')
            asset.depreciation_entries_count = len(posted)
            asset.total_depreciation_entries_count = len(
                asset.depreciation_move_ids)
            asset.gross_increase_count = len(asset.children_ids)

    # -------------------------------------------------------------------------
    # ONCHANGE METHODS
    # -------------------------------------------------------------------------
    @api.onchange('account_depreciation_id')
    def _onchange_account_depreciation_id(self):
        if not self.original_move_line_ids:
            if not self.account_asset_id and self.state != 'model':
                self.account_asset_id = self.account_depreciation_id

    @api.onchange('original_value', 'original_move_line_ids')
    def _display_original_value_warning(self):
        if self.original_move_line_ids:
            computed = self.related_purchase_value + self.non_deductible_tax_value
            if self.original_value != computed:
                warning = {
                    'title': _("Warning for the Original Value of %s",
                               self.name),
                    'message': _(
                        "The amount you have entered (%(entered_amount)s) "
                        "does not match the Related Purchase's value "
                        "(%(purchase_value)s). Please make sure this is "
                        "what you want.",
                        entered_amount=formatLang(
                            self.env, self.original_value,
                            currency_obj=self.currency_id),
                        purchase_value=formatLang(
                            self.env, computed,
                            currency_obj=self.currency_id))
                }
                return {'warning': warning}

    @api.onchange('original_move_line_ids')
    def _onchange_original_move_line_ids(self):
        self.acquisition_date = False
        self._compute_acquisition_date()

    @api.onchange('account_asset_id')
    def _onchange_account_asset_id(self):
        self.account_depreciation_id = (
            self.account_depreciation_id or self.account_asset_id)

    @api.onchange('model_id')
    def _onchange_model_id(self):
        model = self.model_id
        if model:
            self.method = model.method
            self.method_number = model.method_number
            self.method_period = model.method_period
            self.method_progress_factor = model.method_progress_factor
            self.prorata_computation_type = model.prorata_computation_type
            self.account_asset_id = model.account_asset_id
            self.account_depreciation_id = model.account_depreciation_id
            self.account_depreciation_expense_id = model.account_depreciation_expense_id
            self.journal_id = model.journal_id

    @api.onchange('original_value', 'salvage_value', 'acquisition_date',
                  'method', 'method_progress_factor', 'method_period',
                  'method_number', 'prorata_computation_type',
                  'already_depreciated_amount_import', 'prorata_date')
    def onchange_consistent_board(self):
        self.depreciation_move_ids = [(5, 0, 0)]

    # -------------------------------------------------------------------------
    # CONSTRAINT METHODS
    # -------------------------------------------------------------------------
    @api.constrains('active', 'state')
    def _check_active(self):
        for record in self:
            if not record.active and record.state not in ('close', 'model'):
                raise UserError(_(
                    'You cannot archive a record that is not closed'))

    @api.constrains('depreciation_move_ids')
    def _check_depreciations(self):
        for asset in self:
            if (asset.state == 'open'
                    and asset.depreciation_move_ids
                    and not asset.currency_id.is_zero(
                        asset.depreciation_move_ids.sorted(
                            lambda x: (x.date, x.id)
                        )[-1].asset_remaining_value)):
                raise UserError(_(
                    "The remaining value on the last depreciation line must be 0"))

    @api.constrains('original_move_line_ids')
    def _check_related_purchase(self):
        for asset in self:
            if (asset.original_move_line_ids
                    and asset.related_purchase_value == 0):
                raise UserError(_(
                    "You cannot create an asset from lines containing "
                    "credit and debit on the account or with a null amount"))
            if asset.state not in ('model', 'draft'):
                raise UserError(_(
                    "You cannot add or remove bills when the asset is "
                    "already running or closed."))

    # -------------------------------------------------------------------------
    # LOW-LEVEL METHODS
    # -------------------------------------------------------------------------
    def unlink(self):
        for asset in self:
            if asset.state in ['open', 'paused', 'close']:
                raise UserError(_(
                    'You cannot delete a document that is in %s state.',
                    dict(self._fields['state']._description_selection(
                        self.env)).get(asset.state)))
            posted_amount = len(asset.depreciation_move_ids.filtered(
                lambda x: x.state == 'posted'))
            if posted_amount > 0:
                raise UserError(_(
                    'You cannot delete an asset linked to posted entries.'
                    '\nYou should either confirm the asset, then, sell or '
                    'dispose of it, or cancel the linked journal entries.'))
        for asset in self:
            for line in asset.original_move_line_ids:
                if line.name:
                    body = _(
                        'A document linked to %s has been deleted: %s',
                        line.name, asset.name)
                else:
                    body = _(
                        'A document linked to this move has been deleted: %s',
                        asset.name)
                line.move_id.message_post(body=body)
        return super(AccountAsset, self).unlink()

    def copy(self, default=None):
        default = dict(default or {})
        if self.state == 'model':
            default['state'] = 'model'
        default['name'] = _('%s (copy)', self.name)
        default['account_asset_id'] = self.account_asset_id.id
        return super(AccountAsset, self).copy(default)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if (self._context.get('default_state') != 'model'
                    and vals.get('state') != 'model'):
                vals['state'] = 'draft'
        new_recs = super(
            AccountAsset, self.with_context(mail_create_nolog=True)
        ).create(vals_list)
        for i, vals in enumerate(vals_list):
            if 'original_value' in vals:
                new_recs[i].original_value = vals['original_value']
        if self.env.context.get('original_asset'):
            original_asset = self.env['account.asset'].browse(
                self.env.context.get('original_asset'))
            original_asset.model_id = new_recs
        return new_recs

    def write(self, vals):
        result = super().write(vals)
        for asset in self:
            for move in asset.depreciation_move_ids:
                if move.state == 'draft':
                    if 'account_depreciation_id' in vals:
                        move.line_ids[::2].account_id = vals[
                            'account_depreciation_id']
                    if 'account_depreciation_expense_id' in vals:
                        move.line_ids[1::2].account_id = vals[
                            'account_depreciation_expense_id']
                    if 'journal_id' in vals:
                        move.journal_id = vals['journal_id']
        return result

    # -------------------------------------------------------------------------
    # BOARD COMPUTATION
    # -------------------------------------------------------------------------
    def _get_linear_amount(self, days_before_period, days_until_period_end,
                           total_depreciable_value):
        amount_expected_previous_period = (
            total_depreciable_value * days_before_period
            / self.asset_lifetime_days)
        amount_after_expected = (
            total_depreciable_value * days_until_period_end
            / self.asset_lifetime_days)
        number_days_for_period = days_until_period_end - days_before_period
        amount_of_decrease_spread_over_period = [
            number_days_for_period * mv.depreciation_value / (
                self.asset_lifetime_days
                - self._get_delta_days(
                    self.paused_prorata_date,
                    mv.asset_depreciation_beginning_date))
            for mv in self.depreciation_move_ids.filtered(
                lambda mv: mv.asset_value_change)
        ]
        computed_linear_amount = self.currency_id.round(
            amount_after_expected
            - self.currency_id.round(amount_expected_previous_period)
            - sum(amount_of_decrease_spread_over_period))
        return computed_linear_amount

    def _compute_board_amount(self, residual_amount, period_start_date,
                              period_end_date, days_already_depreciated,
                              days_left_to_depreciated, residual_declining,
                              start_yearly_period=None,
                              total_lifetime_left=None,
                              residual_at_compute=None,
                              start_recompute_date=None):

        def _get_max_between_linear_and_degressive(
                linear_amount, effective_start_date=start_yearly_period):
            fiscalyear_dates = self.company_id.compute_fiscalyear_dates(
                period_end_date)
            days_in_fiscalyear = self._get_delta_days(
                fiscalyear_dates['date_from'], fiscalyear_dates['date_to'])
            degressive_total_value = (
                residual_declining * (
                    1 - self.method_progress_factor
                    * self._get_delta_days(
                        effective_start_date, period_end_date)
                    / days_in_fiscalyear))
            degressive_amount = residual_amount - degressive_total_value
            return self._degressive_linear_amount(
                residual_amount, degressive_amount, linear_amount)

        if (float_is_zero(self.asset_lifetime_days, 2)
                or float_is_zero(residual_amount, 2)):
            return 0, 0

        days_until_period_end = self._get_delta_days(
            self.paused_prorata_date, period_end_date)
        days_before_period = self._get_delta_days(
            self.paused_prorata_date,
            period_start_date + relativedelta(days=-1))
        days_before_period = max(days_before_period, 0)
        number_days = days_until_period_end - days_before_period

        if self.method == 'linear':
            if (total_lifetime_left
                    and float_compare(total_lifetime_left, 0, 2) > 0):
                computed_linear_amount = (
                    residual_amount - residual_at_compute * (
                        1 - self._get_delta_days(
                            start_recompute_date, period_end_date)
                        / total_lifetime_left))
            else:
                computed_linear_amount = self._get_linear_amount(
                    days_before_period, days_until_period_end,
                    self.total_depreciable_value)
            amount = min(computed_linear_amount, residual_amount, key=abs)
        elif self.method == 'degressive':
            effective_start_date = (
                max(start_yearly_period, self.paused_prorata_date)
                if start_yearly_period else self.paused_prorata_date)
            days_left_from_beginning_of_year = (
                self._get_delta_days(
                    effective_start_date,
                    period_start_date - relativedelta(days=1))
                + days_left_to_depreciated)
            expected_remaining_value_with_linear = (
                residual_declining - residual_declining
                * self._get_delta_days(
                    effective_start_date, period_end_date)
                / days_left_from_beginning_of_year)
            linear_amount = (
                residual_amount - expected_remaining_value_with_linear)
            amount = _get_max_between_linear_and_degressive(
                linear_amount, effective_start_date)
        elif self.method == 'degressive_then_linear':
            if not self.parent_id:
                linear_amount = self._get_linear_amount(
                    days_before_period, days_until_period_end,
                    self.total_depreciable_value)
            else:
                parent_moves = self.parent_id.depreciation_move_ids.filtered(
                    lambda mv: mv.date <= self.prorata_date
                ).sorted(key=lambda mv: (mv.date, mv.id))
                parent_cumulative_depreciation = (
                    parent_moves[-1].asset_depreciated_value
                    if parent_moves
                    else self.parent_id.already_depreciated_amount_import)
                parent_depreciable_value = (
                    parent_moves[-1].asset_remaining_value
                    if parent_moves
                    else self.parent_id.total_depreciable_value)
                if self.currency_id.is_zero(parent_depreciable_value):
                    linear_amount = self._get_linear_amount(
                        days_before_period, days_until_period_end,
                        self.total_depreciable_value)
                else:
                    depreciable_value = (
                        self.total_depreciable_value * (
                            1 + parent_cumulative_depreciation
                            / parent_depreciable_value))
                    linear_amount = (
                        self._get_linear_amount(
                            days_before_period, days_until_period_end,
                            depreciable_value)
                        * self.asset_lifetime_days
                        / self.parent_id.asset_lifetime_days)
            amount = _get_max_between_linear_and_degressive(linear_amount)

        amount = (max(amount, 0)
                  if self.currency_id.compare_amounts(residual_amount, 0) > 0
                  else min(amount, 0))
        amount = self._get_depreciation_amount_end_of_lifetime(
            residual_amount, amount, days_until_period_end)

        return number_days, self.currency_id.round(amount)

    def compute_depreciation_board(self, date=False):
        self.depreciation_move_ids.filtered(
            lambda mv: mv.state == 'draft' and (
                mv.date >= date if date else True)
        ).unlink()

        new_depreciation_moves_data = []
        for asset in self:
            new_depreciation_moves_data.extend(asset._recompute_board(date))

        new_moves = self.env['account.move'].create(
            new_depreciation_moves_data)
        new_moves_to_post = new_moves.filtered(
            lambda move: move.asset_id.state == 'open')
        new_moves_to_post._post()

    def _recompute_board(self, start_depreciation_date=False):
        self.ensure_one()
        posted = self.depreciation_move_ids.filtered(
            lambda mv: mv.state == 'posted' and not mv.asset_value_change
        ).sorted(key=lambda mv: (mv.date, mv.id))

        imported_amount = self.already_depreciated_amount_import
        residual_amount = (
            self.value_residual
            - sum(self.depreciation_move_ids.filtered(
                lambda mv: mv.state == 'draft').mapped('depreciation_value')))
        if not posted:
            residual_amount += imported_amount
        residual_declining = residual_at_compute = residual_amount

        if not start_depreciation_date:
            if posted:
                start_depreciation_date = posted[-1].date + relativedelta(days=1)
            else:
                start_depreciation_date = self.paused_prorata_date

        start_recompute_date = start_yearly_period = start_depreciation_date

        last_day_asset = self._get_last_day_asset()
        final_depreciation_date = self._get_end_period_date(last_day_asset)
        total_lifetime_left = self._get_delta_days(
            start_depreciation_date, last_day_asset)

        depreciation_move_values = []
        if not float_is_zero(
                self.value_residual,
                precision_rounding=self.currency_id.rounding):
            while (not self.currency_id.is_zero(residual_amount)
                   and start_depreciation_date < final_depreciation_date):
                period_end = self._get_end_period_date(
                    start_depreciation_date)
                period_end_fy = (
                    self.company_id.compute_fiscalyear_dates(
                        period_end).get('date_to'))
                lifetime_left = self._get_delta_days(
                    start_depreciation_date, last_day_asset)

                days, amount = self._compute_board_amount(
                    residual_amount, start_depreciation_date, period_end,
                    False, lifetime_left, residual_declining,
                    start_yearly_period, total_lifetime_left,
                    residual_at_compute, start_recompute_date)
                residual_amount -= amount

                if not posted:
                    if abs(imported_amount) <= abs(amount):
                        amount -= imported_amount
                        imported_amount = 0
                    else:
                        imported_amount -= amount
                        amount = 0

                if (self.method == 'degressive_then_linear'
                        and final_depreciation_date < period_end):
                    period_end = final_depreciation_date

                if not float_is_zero(
                        amount,
                        precision_rounding=self.currency_id.rounding):
                    depreciation_move_values.append(
                        self.env['account.move']
                        ._prepare_move_for_asset_depreciation({
                            'amount': amount,
                            'asset_id': self,
                            'depreciation_beginning_date':
                                start_depreciation_date,
                            'date': period_end,
                            'asset_number_days': days,
                        }))

                if period_end == period_end_fy:
                    start_yearly_period = (
                        self.company_id.compute_fiscalyear_dates(
                            period_end).get('date_from')
                        + relativedelta(years=1))
                    residual_declining = residual_amount

                start_depreciation_date = period_end + relativedelta(days=1)

        return depreciation_move_values

    def _get_end_period_date(self, start_depreciation_date):
        self.ensure_one()

        fiscalyear_date = self.company_id.compute_fiscalyear_dates(
            start_depreciation_date).get('date_to')
        period_end = (
            fiscalyear_date
            if start_depreciation_date <= fiscalyear_date
            else fiscalyear_date + relativedelta(years=1))
        if self.method_period == '1':
            max_day = end_of(
                datetime.date(
                    start_depreciation_date.year,
                    start_depreciation_date.month, 1),
                'month').day
            period_end = min(
                start_depreciation_date.replace(day=max_day), period_end)
        return period_end

    def _get_delta_days(self, start_date, end_date):
        self.ensure_one()
        if self.prorata_computation_type == 'daily_computation':
            return (end_date - start_date).days + 1
        else:
            start_date_days_month = end_of(start_date, 'month').day
            start_prorata = (
                (start_date_days_month - start_date.day + 1)
                / start_date_days_month)
            end_prorata = end_date.day / end_of(end_date, 'month').day
            return sum((
                start_prorata * DAYS_PER_MONTH,
                end_prorata * DAYS_PER_MONTH,
                (end_date.year - start_date.year) * DAYS_PER_YEAR,
                (end_date.month - start_date.month - 1) * DAYS_PER_MONTH,
            ))

    def _get_last_day_asset(self):
        this = self.parent_id if self.parent_id else self
        return (this.paused_prorata_date
                + relativedelta(
                    months=int(this.method_period) * this.method_number,
                    days=-1))

    # -------------------------------------------------------------------------
    # PUBLIC ACTIONS
    # -------------------------------------------------------------------------
    def action_asset_modify(self):
        self.ensure_one()
        is_resume = self.env.context.get('resume_after_pause', False)
        new_wizard = self.env['asset.modify'].create({
            'asset_id': self.id,
            'modify_action': 'dispose',
            'is_resume': is_resume,
        })
        return {
            'name': _('Resume Depreciation') if is_resume else _('Modify Asset'),
            'view_mode': 'form',
            'res_model': 'asset.modify',
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': new_wizard.id,
            'context': self.env.context,
        }

    def action_save_model(self):
        return {
            'name': _('Save model'),
            'views': [[self.env.ref(
                'advanced_asset_management_pro.view_account_asset_form').id, "form"]],
            'res_model': 'account.asset',
            'type': 'ir.actions.act_window',
            'context': {
                'default_state': 'model',
                'default_account_asset_id': self.account_asset_id.id,
                'default_account_depreciation_id':
                    self.account_depreciation_id.id,
                'default_account_depreciation_expense_id':
                    self.account_depreciation_expense_id.id,
                'default_journal_id': self.journal_id.id,
                'default_method': self.method,
                'default_method_number': self.method_number,
                'default_method_period': self.method_period,
                'default_method_progress_factor': self.method_progress_factor,
                'default_prorata_date': self.prorata_date,
                'default_prorata_computation_type':
                    self.prorata_computation_type,
                'original_asset': self.id,
            }
        }

    def open_entries(self):
        return {
            'name': _('Journal Entries'),
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'search_view_id': self.env.ref('account.view_account_move_filter').id,
            'views': [
                (self.env.ref('account.view_move_tree').id, 'tree'),
                (False, 'form')],
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.depreciation_move_ids.ids)],
            'context': dict(self.env.context, create=False, default_move_type='entry'),
        }

    def open_related_entries(self):
        return {
            'name': _('Journal Items'),
            'view_mode': 'tree,form',
            'res_model': 'account.move.line',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.original_move_line_ids.ids)],
        }

    def open_increase(self):
        result = {
            'name': _('Gross Increase'),
            'view_mode': 'tree,form',
            'res_model': 'account.asset',
            'context': {**self.env.context, 'create': False},
            'view_id': False,
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.children_ids.ids)],
            'views': [(False, 'tree'), (False, 'form')],
        }
        if len(self.children_ids) == 1:
            result['views'] = [(False, 'form')]
            result['res_id'] = self.children_ids.id
        return result

    def open_parent_id(self):
        return {
            'name': _('Parent Asset'),
            'view_mode': 'form',
            'res_model': 'account.asset',
            'type': 'ir.actions.act_window',
            'res_id': self.parent_id.id,
            'views': [(False, 'form')],
        }

    def validate(self):
        flds = [
            'method', 'method_number', 'method_period',
            'method_progress_factor', 'salvage_value',
            'original_move_line_ids',
        ]
        ref_tracked_fields = self.env['account.asset'].fields_get(flds)
        self.write({'state': 'open'})
        for asset in self:
            tracked_fields = ref_tracked_fields.copy()
            if asset.method == 'linear':
                del tracked_fields['method_progress_factor']
            dummy, tracking_value_ids = asset._mail_track(
                tracked_fields, dict.fromkeys(flds))
            asset_name = (
                _('Asset created'),
                _('An asset has been created for this move:'))
            msg = asset_name[1] + ' ' + asset.name
            asset.message_post(
                body=asset_name[0],
                tracking_value_ids=tracking_value_ids)
            for move_id in asset.original_move_line_ids.mapped('move_id'):
                move_id.message_post(body=msg)
            if not asset.depreciation_move_ids:
                asset.compute_depreciation_board()
            asset._check_depreciations()
            asset.depreciation_move_ids.filtered(
                lambda move: move.state != 'posted')._post()

    def set_to_close(self, invoice_line_ids, date=None, message=None, gain_account_id=None, loss_account_id=None):
        self.ensure_one()
        disposal_date = date or fields.Date.today()
        if invoice_line_ids and self.children_ids.filtered(
                lambda a: a.state in ('draft', 'open')
                or a.value_residual > 0):
            raise UserError(_(
                "You cannot automate the journal entry for an asset that "
                "has a running gross increase. Please use 'Dispose' on "
                "the increase(s)."))
        full_asset = self + self.children_ids
        full_asset.write({'state': 'close'})
        move_ids = full_asset._get_disposal_moves(
            [invoice_line_ids] * len(full_asset), disposal_date,
            gain_account_id=gain_account_id, loss_account_id=loss_account_id)
        for asset in full_asset:
            asset.message_post(
                body=(_('Asset sold. %s', message if message else "")
                      if invoice_line_ids
                      else _('Asset disposed. %s',
                             message if message else "")))

        selling_price = abs(sum(
            invoice_line.balance for invoice_line in invoice_line_ids))
        self.net_gain_on_sale = self.currency_id.round(
            selling_price - self.book_value)

        if move_ids:
            name = _('Disposal Move')
            view_mode = 'form'
            if len(move_ids) > 1:
                name = _('Disposal Moves')
                view_mode = 'tree,form'
            return {
                'name': name,
                'view_mode': view_mode,
                'res_model': 'account.move',
                'type': 'ir.actions.act_window',
                'target': 'current',
                'res_id': move_ids[0],
                'domain': [('id', 'in', move_ids)],
            }

    def set_to_cancelled(self):
        for asset in self:
            posted_moves = asset.depreciation_move_ids.filtered(
                lambda m: (
                    not m.reversal_move_id
                    and not m.reversed_entry_id
                    and m.state == 'posted'))
            if posted_moves:
                asset._cancel_future_moves(datetime.date.min)
                asset._message_log(body=_('Asset Cancelled'))
            else:
                asset._message_log(body=_('Asset Cancelled'))
            asset.depreciation_move_ids.filtered(
                lambda m: m.state == 'draft'
            ).with_context(force_delete=True).unlink()
            asset.asset_paused_days = 0
            asset.write({'state': 'cancelled'})

    def set_to_draft(self):
        self.write({'state': 'draft'})

    def set_to_running(self):
        if (self.depreciation_move_ids
                and not max(
                    self.depreciation_move_ids,
                    key=lambda m: (m.date, m.id)
                ).asset_remaining_value == 0):
            self.env['asset.modify'].create({
                'asset_id': self.id,
                'name': _('Reset to running'),
            }).modify()
        self.write({'state': 'open', 'net_gain_on_sale': 0})

    def resume_after_pause(self):
        self.ensure_one()
        return self.with_context(
            resume_after_pause=True).action_asset_modify()

    def pause(self, pause_date, message=None):
        self.ensure_one()
        self._create_move_before_date(pause_date)
        self.write({'state': 'paused'})
        self.message_post(
            body=_("Asset paused. %s", message if message else ""))

    def open_asset(self, view_mode):
        if len(self) == 1:
            view_mode = ['form']
        views = [v for v in [(False, 'tree'), (False, 'form')]
                 if v[1] in view_mode]
        ctx = dict(self._context)
        ctx.pop('default_move_type', None)
        return {
            'name': _('Asset'),
            'view_mode': ','.join(view_mode),
            'type': 'ir.actions.act_window',
            'res_id': self.id if 'tree' not in view_mode else False,
            'res_model': 'account.asset',
            'views': views,
            'domain': [('id', 'in', self.ids)],
            'context': ctx,
        }

    # -------------------------------------------------------------------------
    # HELPER METHODS
    # -------------------------------------------------------------------------
    def _insert_depreciation_line(self, amount, beginning_depreciation_date,
                                  depreciation_date, days_depreciated):
        self.ensure_one()
        AccountMove = self.env['account.move']
        return AccountMove.create(
            AccountMove._prepare_move_for_asset_depreciation({
                'amount': amount,
                'asset_id': self,
                'depreciation_beginning_date': beginning_depreciation_date,
                'date': depreciation_date,
                'asset_number_days': days_depreciated,
            }))

    def _post_non_deductible_tax_value(self):
        if self.non_deductible_tax_value:
            currency = self.env.company.currency_id
            msg = _(
                'A non deductible tax value of %(tax_value)s was added to '
                '%(name)s\'s initial value of %(purchase_value)s',
                tax_value=formatLang(
                    self.env, self.non_deductible_tax_value,
                    currency_obj=currency),
                name=self.name,
                purchase_value=formatLang(
                    self.env, self.related_purchase_value,
                    currency_obj=currency))
            self.message_post(body=msg)

    def _create_move_before_date(self, date):
        all_move_dates_before_date = (
            self.depreciation_move_ids.filtered(
                lambda x: (
                    x.date <= date
                    and not x.reversal_move_id
                    and not x.reversed_entry_id
                    and x.state == 'posted')
            ).sorted('date')).mapped('date')

        beginning_fiscal_year = (
            self.company_id.compute_fiscalyear_dates(date).get('date_from')
            if self.method != 'linear' else False)
        first_fiscalyear_move = self.env['account.move']

        if all_move_dates_before_date:
            last_move_date_not_reversed = max(all_move_dates_before_date)
            future_moves_beginning_date = (
                self.depreciation_move_ids.filtered(
                    lambda m: m.date > last_move_date_not_reversed and (
                        not m.reversal_move_id
                        and not m.reversed_entry_id
                        and m.state == 'posted'
                        or m.state == 'draft')
                ).mapped('asset_depreciation_beginning_date'))
            beginning_depreciation_date = (
                min(future_moves_beginning_date)
                if future_moves_beginning_date
                else self.paused_prorata_date)

            if self.method != 'linear':
                first_moves = self.depreciation_move_ids.filtered(
                    lambda m: (
                        m.asset_depreciation_beginning_date
                        >= beginning_fiscal_year and (
                            not m.reversal_move_id
                            and not m.reversed_entry_id
                            and m.state == 'posted'
                            or m.state == 'draft'))
                ).sorted(
                    lambda m: (m.asset_depreciation_beginning_date, m.id))
                first_fiscalyear_move = next(
                    iter(first_moves), first_fiscalyear_move)
        else:
            beginning_depreciation_date = self.paused_prorata_date

        residual_declining = (
            first_fiscalyear_move.asset_remaining_value
            + first_fiscalyear_move.depreciation_value)
        self._cancel_future_moves(date)

        imported_amount = (
            self.already_depreciated_amount_import
            if not all_move_dates_before_date else 0)
        value_residual = (
            self.value_residual + self.already_depreciated_amount_import
            if not all_move_dates_before_date else self.value_residual)
        residual_declining = residual_declining or value_residual

        last_day_asset = self._get_last_day_asset()
        lifetime_left = self._get_delta_days(
            beginning_depreciation_date, last_day_asset)
        days_depreciated, amount = self._compute_board_amount(
            self.value_residual, beginning_depreciation_date, date,
            False, lifetime_left, residual_declining, beginning_fiscal_year,
            lifetime_left, value_residual, beginning_depreciation_date)

        if abs(imported_amount) <= abs(amount):
            amount -= imported_amount
        if not float_is_zero(
                amount, precision_rounding=self.currency_id.rounding):
            new_line = self._insert_depreciation_line(
                amount, beginning_depreciation_date, date, days_depreciated)
            new_line._post()

    def _cancel_future_moves(self, date):
        for asset in self:
            obsolete_moves = asset.depreciation_move_ids.filtered(
                lambda m: m.state == 'draft' or (
                    not m.reversal_move_id
                    and not m.reversed_entry_id
                    and m.state == 'posted'
                    and m.date > date))
            obsolete_moves._unlink_or_reverse()

    def _get_disposal_moves(self, invoice_lines_list, disposal_date, gain_account_id=None, loss_account_id=None):
        def get_line(name, asset, amount, account):
            return (0, 0, {
                'name': name,
                'account_id': account.id,
                'balance': -amount,
                'currency_id': asset.currency_id.id,
            })

        move_ids = []
        assert len(self) == len(invoice_lines_list)
        for asset, invoice_line_ids in zip(self, invoice_lines_list):
            asset._create_move_before_date(disposal_date)

            dict_invoice = {}
            invoice_amount = 0
            initial_amount = asset.original_value
            initial_account = (
                asset.original_move_line_ids.account_id
                if len(asset.original_move_line_ids.account_id) == 1
                else asset.account_asset_id)

            all_lines_before_disposal = asset.depreciation_move_ids.filtered(
                lambda x: x.date <= disposal_date)
            depreciated_amount = asset.currency_id.round(copysign(
                sum(all_lines_before_disposal.mapped('depreciation_value'))
                + asset.already_depreciated_amount_import,
                -initial_amount))
            depreciation_account = asset.account_depreciation_id
            for invoice_line in invoice_line_ids:
                dict_invoice[invoice_line.account_id] = (
                    copysign(invoice_line.balance, -initial_amount)
                    + dict_invoice.get(invoice_line.account_id, 0))
                invoice_amount += copysign(
                    invoice_line.balance, -initial_amount)
            list_accounts = [
                (amount, account)
                for account, amount in dict_invoice.items()]
            difference = -initial_amount - depreciated_amount - invoice_amount
            if difference > 0:
                difference_account = gain_account_id if gain_account_id else asset.company_id.income_currency_exchange_account_id
            else:
                difference_account = loss_account_id if loss_account_id else asset.company_id.expense_currency_exchange_account_id
            line_datas = (
                [(initial_amount, initial_account),
                 (depreciated_amount, depreciation_account)]
                + list_accounts
                + [(difference, difference_account)])
            name = (
                _("%(asset)s: Disposal", asset=asset.name)
                if not invoice_line_ids
                else _("%(asset)s: Sale", asset=asset.name))
            vals = {
                'asset_id': asset.id,
                'ref': name,
                'asset_depreciation_beginning_date': disposal_date,
                'date': disposal_date,
                'journal_id': asset.journal_id.id,
                'move_type': 'entry',
                'line_ids': [
                    get_line(name, asset, amt, acc)
                    for amt, acc in line_datas if acc],
            }
            asset.write({'depreciation_move_ids': [(0, 0, vals)]})
            move_ids += self.env['account.move'].search([
                ('asset_id', '=', asset.id),
                ('state', '=', 'draft'),
            ]).ids
        return move_ids

    def _degressive_linear_amount(self, residual_amount,
                                  degressive_amount, linear_amount):
        if self.currency_id.compare_amounts(residual_amount, 0) > 0:
            return max(degressive_amount, linear_amount)
        else:
            return min(degressive_amount, linear_amount)

    def _get_depreciation_amount_end_of_lifetime(
            self, residual_amount, amount, days_until_period_end):
        if (abs(residual_amount) < abs(amount)
                or days_until_period_end >= self.asset_lifetime_days):
            amount = residual_amount
        return amount

    def _get_own_book_value(self, date=None):
        self.ensure_one()
        return ((self._get_residual_value_at_date(date)
                 if date else self.value_residual)
                + self.salvage_value)

    def _get_residual_value_at_date(self, date):
        current_and_previous = self.depreciation_move_ids.filtered(
            lambda mv: (
                mv.asset_depreciation_beginning_date < date
                and not mv.reversed_entry_id)
        ).sorted('asset_depreciation_beginning_date', reverse=True)
        if not current_and_previous:
            return 0

        if len(current_and_previous) > 1:
            previous_value_residual = (
                current_and_previous[1].asset_remaining_value)
        else:
            previous_value_residual = (
                self.original_value - self.salvage_value
                - self.already_depreciated_amount_import)

        cur_depr_end_date = self._get_end_period_date(date)
        current_depreciation = current_and_previous[0]
        cur_depr_beg_date = (
            current_depreciation.asset_depreciation_beginning_date)

        rate = (self._get_delta_days(cur_depr_beg_date, date)
                / self._get_delta_days(cur_depr_beg_date, cur_depr_end_date))
        lost_value_at_date = (
            (previous_value_residual
             - current_depreciation.asset_remaining_value) * rate)
        residual_value_at_date = self.currency_id.round(
            previous_value_residual - lost_value_at_date)
        if self.currency_id.compare_amounts(self.original_value, 0) > 0:
            return max(residual_value_at_date, 0)
        else:
            return min(residual_value_at_date, 0)


class AccountAssetGroup(models.Model):
    _name = 'account.asset.group'
    _description = 'Asset Group'
    _order = 'name'

    name = fields.Char("Name", index=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company)
    linked_asset_ids = fields.One2many(
        'account.asset', 'asset_group_id', string='Related Assets')
    count_linked_assets = fields.Integer(
        compute='_compute_count_linked_asset')

    @api.depends('linked_asset_ids')
    def _compute_count_linked_asset(self):
        for asset_group in self:
            asset_group.count_linked_assets = len(
                asset_group.linked_asset_ids)

    def action_open_linked_assets(self):
        self.ensure_one()
        return {
            'name': self.name,
            'view_mode': 'tree,form',
            'res_model': 'account.asset',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.linked_asset_ids.ids)],
        }
