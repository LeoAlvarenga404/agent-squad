"""The CRM: shared state and rules. Agents coordinate through it, never by talking to each other."""
from .approvals import decide, pending, request_approval, request_send_preview
from .core import CUSTOMER_STAGES, ROLE_STAGES, STAGES, check_scope, role
from .deals import (get_deal, identify_customer, list_deals, memory_add, memory_recent, memory_search, move_stage,
                    update_customer)
from .leads import create_campaign, list_campaigns, purge, save_lead
from .ops import active_directives, all_directives, dashboard, delegate, end_directive, prioritize, set_param
from .tickets import (add_hours, builder_mode, claim_next_ticket, create_ticket, dispatch, get_ticket, list_tickets,
                      report, start_ticket, update_ticket)

from ..builder.assets import save_photo, search_photos  # noqa: E402

ROLE_TOOLS = {
    "hunter": [active_directives, create_campaign, list_campaigns, save_lead, list_deals],
    "sales": [active_directives, identify_customer, get_deal, list_deals, move_stage, request_approval,
              update_customer, memory_add, memory_search, memory_recent],
    "account": [active_directives, identify_customer, get_deal, list_deals, move_stage, request_approval,
                update_customer, create_ticket, list_tickets, start_ticket, memory_add, memory_search, memory_recent],
    "builder": [active_directives, get_ticket, report, search_photos, save_photo],  # the runner deploys and pulls the queue
    "coordinator": [dashboard, all_directives, delegate, end_directive, prioritize, set_param, list_deals,
                    list_campaigns, get_deal],
    "runner": [],
    "human": [],
}
