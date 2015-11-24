# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2013, 2014 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license

import logging
from flask import g
from superdesk.notification import push_notification
from superdesk.resource import Resource
from superdesk.services import BaseService
from superdesk.errors import SuperdeskApiError, add_notifier
import superdesk
from bson.objectid import ObjectId
from superdesk.emails import send_activity_emails

log = logging.getLogger(__name__)


def init_app(app):
    endpoint_name = 'activity'
    service = ActivityService(endpoint_name, backend=superdesk.get_backend())
    ActivityResource(endpoint_name, app=app, service=service)

    endpoint_name = 'audit'
    service = AuditService(endpoint_name, backend=superdesk.get_backend())
    AuditResource(endpoint_name, app=app, service=service)

    app.on_inserted += service.on_generic_inserted
    app.on_updated += service.on_generic_updated
    app.on_deleted_item += service.on_generic_deleted

    # Registering with intrinsic privileges because: A user should be able to mark as read their own notifications.
    superdesk.intrinsic_privilege(resource_name='activity', method=['PATCH'])


class AuditResource(Resource):
    endpoint_name = 'audit'
    resource_methods = ['GET']
    item_methods = ['GET']
    schema = {
        'resource': {'type': 'string'},
        'action': {'type': 'string'},
        'extra': {'type': 'dict'},
        'user': Resource.rel('users', False)
    }
    exclude = {endpoint_name, 'activity', 'dictionaries'}


class AuditService(BaseService):
    def on_generic_inserted(self, resource, docs):
        if resource in AuditResource.exclude:
            return

        user = getattr(g, 'user', None)
        if not user:
            return

        if not len(docs):
            return

        audit = {
            'user': user.get('_id'),
            'resource': resource,
            'action': 'created',
            'extra': docs[0]
        }

        self.post([audit])

    def on_generic_updated(self, resource, doc, original):
        if resource in AuditResource.exclude:
            return

        user = getattr(g, 'user', None)
        if not user:
            return

        audit = {
            'user': user.get('_id'),
            'resource': resource,
            'action': 'updated',
            'extra': doc
        }
        self.post([audit])

    def on_generic_deleted(self, resource, doc):
        if resource in AuditResource.exclude:
            return

        user = getattr(g, 'user', None)
        if not user:
            return

        audit = {
            'user': user.get('_id'),
            'resource': resource,
            'action': 'deleted',
            'extra': doc
        }
        self.post([audit])


class ActivityResource(Resource):
    endpoint_name = 'activity'
    resource_methods = ['GET']
    item_methods = ['GET', 'PATCH']
    schema = {
        'name': {'type': 'string'},
        'message': {'type': 'string'},
        'data': {'type': 'dict'},
        'recipients': {
            'type': 'list',
            'schema': {
                'type': 'dict',
                'schema': {
                    'user_id': {'type': 'string',
                                'required': False,
                                'nullable': True},
                    'read': {'type': 'boolean', 'default': False},
                    'desk_id': {'type': 'string',
                                'required': False,
                                'nullable': True}
                }
            }
        },
        'item': Resource.rel('archive', type='string'),
        'user': Resource.rel('users'),
        'desk': Resource.rel('desks'),
        'resource': {'type': 'string'}
    }
    exclude = {endpoint_name, 'notification'}
    datasource = {
        'default_sort': [('_created', -1)]
    }
    superdesk.register_default_user_preference('email:notification', {
        'type': 'bool',
        'enabled': True,
        'default': True,
        'label': 'Send notifications via email',
        'category': 'notifications',
    })


class ActivityService(BaseService):

    def on_update(self, updates, original):
        """ Called on the patch request to mark a activity/notification/comment as having been read and
        nothing else
        :param updates:
        :param original:
        :return:
        """
        user = getattr(g, 'user', None)
        if not user:
            raise SuperdeskApiError.notFoundError('Can not determine user')
        user_id = str(user.get('_id'))

        # make sure that the user making the read notification is in the notification list
        if not self.is_recipient(updates, user_id):
            raise SuperdeskApiError.forbiddenError('User is not in the notification list')

        # make sure the transition is from not read to read
        if not self.is_read(updates, user_id) and self.is_read(original, user_id):
            raise SuperdeskApiError.forbiddenError('Can not set notification as read')

        # make sure that no other users are being marked as read
        for recipient in updates.get('recipients', []):
            if recipient['user_id'] != user_id:
                if self.is_read(updates, recipient['user_id']) != self.is_read(original, recipient['user_id']):
                    raise SuperdeskApiError.forbiddenError('Can not set other users notification as read')

        # make sure that no other fields are being up dated just read and _updated
        if len(updates) != 2:
            raise SuperdeskApiError.forbiddenError('Can not update')

    def is_recipient(self, activity, user_id):
        """
        Checks if the given user is in the list of recipients
        """
        return any(r for r in activity.get('recipients', []) if r['user_id'] == user_id)

    def is_read(self, activity, user_id):
        """
        Returns the read value for the given user
        """
        return next((r['read'] for r in activity.get('recipients', []) if r['user_id'] == user_id), False)


ACTIVITY_CREATE = 'create'
ACTIVITY_UPDATE = 'update'
ACTIVITY_DELETE = 'delete'
ACTIVITY_EVENT = 'event'
ACTIVITY_ERROR = 'error'


def add_activity(activity_name, msg, resource=None, item=None, notify=None,
                 notify_desks=None, **data):
    """Add an activity into activity log.

    This will became part of current user activity log.

    If there is someone set to be notified it will make it into his notifications box.
    """
    activity = {
        'name': activity_name,
        'message': msg,
        'data': data,
        'resource': resource
    }

    user = getattr(g, 'user', None)
    if user:
        activity['user'] = user.get('_id')

    activity['recipients'] = []

    if notify:
        activity['recipients'] = [{'user_id': str(_id), 'read': False} for _id in notify]

    if notify_desks:
        activity['recipients'].extend([{'desk_id': str(_id), 'read': False} for _id in notify_desks])

    if item:
        activity['item'] = str(item.get('guid', item.get('_id')))
        if item.get('task') and item['task'].get('desk'):
            activity['desk'] = ObjectId(item['task']['desk'])

    superdesk.get_resource_service(ActivityResource.endpoint_name).post([activity])
    push_notification(ActivityResource.endpoint_name, _dest=activity['recipients'])


def notify_and_add_activity(activity_name, msg, resource=None, item=None, user_list=None, **data):
    """
    Adds the activity and notify enabled and active users via email.
    """

    if not user_list and activity_name == ACTIVITY_ERROR:
        user_list = superdesk.get_resource_service('users').get_users_by_user_type('administrator')

    add_activity(activity_name, msg=msg, resource=resource, item=item,
                 notify=[user.get("_id") for user in user_list] if user_list else None, **data)
    if user_list:
        recipients = [user.get('email') for user in user_list
                      if user.get('is_enabled', False) and user.get('is_active', False) and
                      superdesk.get_resource_service('preferences').email_notification_is_enabled(
                          preferences=user.get('user_preferences', {}))]

        if activity_name != ACTIVITY_ERROR:
            current_user = getattr(g, 'user', None)
            activity = {
                'name': activity_name,
                'message': current_user.get('display_name') + ' ' + msg if current_user else msg,
                'data': data,
                'resource': resource
            }
        else:
            activity = {
                'name': activity_name,
                'message': 'System ' + msg,
                'data': data,
                'resource': resource
            }

        if recipients:
            send_activity_emails(activity=activity, recipients=recipients)

add_notifier(notify_and_add_activity)
