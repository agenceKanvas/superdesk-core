# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2015 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license

import superdesk
from superdesk import register_jinja_filter
from superdesk.etree import get_text
from .content_templates import ContentTemplatesResource, ContentTemplatesService, CONTENT_TEMPLATE_PRIVILEGE
from .content_templates import ContentTemplatesApplyResource, ContentTemplatesApplyService
from .content_templates import create_scheduled_content  # noqa
from .filters import format_datetime_filter, first_paragraph_filter


def init_app(app):
    endpoint_name = 'content_templates'
    service = ContentTemplatesService(endpoint_name, backend=superdesk.get_backend())
    ContentTemplatesResource(endpoint_name, app=app, service=service)
    superdesk.privilege(name=CONTENT_TEMPLATE_PRIVILEGE, label='Templates', description='Create templates')

    endpoint_name = 'content_templates_apply'
    service = ContentTemplatesApplyService(endpoint_name, backend=superdesk.get_backend())
    ContentTemplatesApplyResource(endpoint_name, app=app, service=service)

    register_jinja_filter('format_datetime', format_datetime_filter)
    register_jinja_filter('first_paragraph', first_paragraph_filter)
    register_jinja_filter('get_text', get_text)
