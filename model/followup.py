from openerp import models, fields, api, tools
import datetime
from openerp.osv import fields, osv
from openerp.tools.translate import _
import time
import logging
_logger = logging.getLogger(__name__)

class partnerWithManualFollowup(models.Model):
    _inherit = ['res.partner']

    def _get_followup(self, cr, uid, context=None):
        if context is None:
            context = {}
        if context.get('active_model', 'ir.ui.menu') == 'account_followup.followup':
            return context.get('active_id', False)
        company_id = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id.id
        followp_id = self.pool.get('account_followup.followup').search(cr, uid, [('company_id', '=', company_id)], context=context)
        return followp_id and followp_id[0] or False

    def process_partners(self, cr, uid, partner_ids, data, context=None):
        partner_obj = self.pool.get('res.partner')
        partner_ids_to_print = []
        nbmanuals = 0
        manuals = {}
        nbmails = 0
        nbunknownmails = 0
        nbprints = 0
        resulttext = " "
        for partner in self.pool.get('account_followup.stat.by.partner').browse(cr, uid, partner_ids, context=context):
            if partner.max_followup_id.manual_action:
                partner_obj.do_partner_manual_action(cr, uid, [partner.partner_id.id], context=context)
                nbmanuals = nbmanuals + 1
                key = partner.partner_id.payment_responsible_id.name or _("Anybody")
                if not key in manuals.keys():
                    manuals[key]= 1
                else:
                    manuals[key] = manuals[key] + 1
            if partner.max_followup_id.send_email:
                nbunknownmails += partner_obj.do_partner_mail(cr, uid, [partner.partner_id.id], context=context)
                nbmails += 1
            if partner.max_followup_id.send_letter:
                partner_ids_to_print.append(partner.id)
                nbprints += 1
                message = "%s<I> %s </I>%s" % (_("Follow-up letter of "), partner.partner_id.latest_followup_level_id_without_lit.name, _(" will be sent"))
                partner_obj.message_post(cr, uid, [partner.partner_id.id], body=message, context=context)
        if nbunknownmails == 0:
            resulttext += str(nbmails) + _(" email(s) sent")
        else:
            resulttext += str(nbmails) + _(" email(s) should have been sent, but ") + str(nbunknownmails) + _(" had unknown email address(es)") + "\n <BR/> "
        resulttext += "<BR/>" + str(nbprints) + _(" letter(s) in report") + " \n <BR/>" + str(nbmanuals) + _(" manual action(s) assigned:")
        needprinting = False
        if nbprints > 0:
            needprinting = True
        resulttext += "<p align=\"center\">"
        for item in manuals:
            resulttext = resulttext + "<li>" + item + ":" + str(manuals[item]) +  "\n </li>"
        resulttext += "</p>"
        result = {}
        action = partner_obj.do_partner_print(cr, uid, partner_ids_to_print, data, context=context)
        result['needprinting'] = needprinting
        result['resulttext'] = resulttext
        result['action'] = action or {}
        return result

    def do_update_followup_level(self, cr, uid, to_update, partner_list, date, context=None):
        #update the follow-up level on account.move.line
        for id in to_update.keys():
            if to_update[id]['partner_id'] in partner_list:
                self.pool.get('account.move.line').write(cr, uid, [int(id)], {'followup_line_id': to_update[id]['level'], 
                                                                              'followup_date': date})

    def clear_manual_actions(self, cr, uid, partner_list, context=None):
        # Partnerlist is list to exclude
        # Will clear the actions of partners that have no due payments anymore
        partner_list_ids = [partner.partner_id.id for partner in self.pool.get('account_followup.stat.by.partner').browse(cr, uid, partner_list, context=context)]
        ids = self.pool.get('res.partner').search(cr, uid, ['&', ('id', 'not in', partner_list_ids), '|', 
                                                             ('payment_responsible_id', '!=', False), 
                                                             ('payment_next_action_date', '!=', False)], context=context)

        partners_to_clear = []
        for part in self.pool.get('res.partner').browse(cr, uid, ids, context=context): 
            if not part.unreconciled_aml_ids: 
                partners_to_clear.append(part.id)
        self.pool.get('res.partner').action_done(cr, uid, partners_to_clear, context=context)
        return len(partners_to_clear)

    def do_manual_followup(self, cr, uid, ids, context=None):

        context = dict(context or {})

        #Get partners
        tmp = self._get_partners_followp(cr, uid, ids, context=context)
        partner_list = tmp['partner_ids']
        to_update = tmp['to_update']
        date = self.browse(cr, uid, ids, context=context)[0].date
        data = self.read(cr, uid, ids, context=context)[0]
        data['followup_id'] = self._get_followup(cr, uid, context=context)


        #Update partners
        self.do_update_followup_level(cr, uid, to_update, partner_list, date, context=context)
        #process the partners (send mails...)
        restot_context = context.copy()
        restot = self.process_partners(cr, uid, partner_list, data, context=restot_context)
        context.update(restot_context)
        #clear the manual actions if nothing is due anymore
        nbactionscleared = self.clear_manual_actions(cr, uid, partner_list, context=context)
        if nbactionscleared > 0:
            restot['resulttext'] = restot['resulttext'] + "<li>" +  _("%s partners have no credits and as such the action is cleared") %(str(nbactionscleared)) + "</li>" 
        #return the next action
        mod_obj = self.pool.get('ir.model.data')
        model_data_ids = mod_obj.search(cr, uid, [('model','=','ir.ui.view'),('name','=','view_account_followup_sending_results')], context=context)
        resource_id = mod_obj.read(cr, uid, model_data_ids, fields=['res_id'], context=context)[0]['res_id']
        context.update({'description': restot['resulttext'], 'needprinting': restot['needprinting'], 'report_data': restot['action']})
        return {
            'name': _('Send Letters and Emails: Actions Summary'),
            'view_type': 'form',
            'context': context,
            'view_mode': 'tree,form',
            'res_model': 'account_followup.sending.results',
            'views': [(resource_id,'form')],
            'type': 'ir.actions.act_window',
            'target': 'new',
            }

    def _get_msg(self, cr, uid, context=None):
        return self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id.follow_up_msg

    def _get_partners_followp(self, cr, uid, ids, context=None):
        data = {}
        data = self.browse(cr, uid, ids, context=context)[0]
        company_id = data.company_id.id

        cr.execute(
            "SELECT l.partner_id, l.followup_line_id,l.date_maturity, l.date, l.id "\
            "FROM account_move_line AS l "\
                "LEFT JOIN account_account AS a "\
                "ON (l.account_id=a.id) "\
            "WHERE (l.reconcile_id IS NULL) "\
                "AND (a.type='receivable') "\
                "AND (l.state<>'draft') "\
                "AND (l.partner_id is NOT NULL) "\
                "AND (a.active) "\
                "AND (l.debit > 0) "\
                "AND (l.company_id = %s) " \
                "AND (l.blocked = False)" \
                "AND (l.partner_id = %s)" \
            "ORDER BY l.date", (company_id, data.id))  #l.blocked added to take litigation into account and it is not necessary to change follow-up level of account move lines without debit
        move_lines = cr.fetchall()
        old = None
        fups = {}
        followup_id = self._get_followup(cr, uid, context=context)
        fup_id = 'followup_id' in context and context['followup_id'] or followup_id
        date = time.strftime("%Y-%m-%d")

        current_date = datetime.date(*time.strptime(date, '%Y-%m-%d')[:3])
        cr.execute(
            "SELECT * "\
            "FROM account_followup_followup_line "\
            "WHERE followup_id=%s "\
            "ORDER BY delay", (fup_id,))
        
        #Create dictionary of tuples where first element is the date to compare with the due date and second element is the id of the next level
        for result in cr.dictfetchall():
            delay = datetime.timedelta(days=result['delay'])
            fups[old] = (current_date - delay, result['id'])
            old = result['id']

        partner_list = []
        to_update = {}
        
        #Fill dictionary of accountmovelines to_update with the partners that need to be updated
        for partner_id, followup_line_id, date_maturity,date, id in move_lines:
            if not partner_id:
                continue
            if followup_line_id not in fups:
                continue
            stat_line_id = partner_id * 10000 + company_id
            if date_maturity:
                if date_maturity <= fups[followup_line_id][0].strftime('%Y-%m-%d'):
                    if stat_line_id not in partner_list:
                        partner_list.append(stat_line_id)
                    to_update[str(id)]= {'level': fups[followup_line_id][1], 'partner_id': stat_line_id}
            elif date and date <= fups[followup_line_id][0].strftime('%Y-%m-%d'):
                if stat_line_id not in partner_list:
                    partner_list.append(stat_line_id)
                to_update[str(id)]= {'level': fups[followup_line_id][1], 'partner_id': stat_line_id}
        return {'partner_ids': partner_list, 'to_update': to_update}

    
