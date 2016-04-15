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
import superdesk
from flask import current_app as app

from datetime import timedelta
from superdesk.celery_app import celery
from superdesk.utc import utcnow
import superdesk.publish
from eve.utils import config, ParsedRequest
from superdesk.lock import lock, unlock
from superdesk.celery_task_utils import get_lock_id
from superdesk import get_resource_service
from .publish_queue import QueueState

logger = logging.getLogger(__name__)

UPDATE_SCHEDULE_DEFAULT = {'seconds': 10}
PUBLISH_QUEUE = 'publish_queue'
STATE_PENDING = 'pending'


class PublishContent(superdesk.Command):
    """Runs deliveries"""

    def run(self, provider_type=None):
        publish.apply_async(expires=10)


@celery.task(soft_time_limit=1800)
def publish():
    """
    Fetches items from publish queue as per the configuration,
    calls the transmit function.
    """
    lock_name = get_lock_id("Transmit", "Articles")
    if not lock(lock_name, '', expire=1800):
        logger.info('Task: {} is already running.'.format(lock_name))
        return

    try:
        items = list(get_queue_items())

        if len(items) > 0:
            transmit_items(items)

    except:
        logger.exception('Task: {} failed.'.format(lock_name))
    finally:
        unlock(lock_name, '')


def get_queue_items():
    lookup = {
        '$and': [
            {'$or': [
                {'state': QueueState.PENDING.value},
                {'state': QueueState.RETRYING.value, 'next_retry_attempt_at': {'$lte': utcnow()}}
            ]},
            {'destination.delivery_type': {'$ne': 'pull'}}
        ]
    }
    request = ParsedRequest()
    request.max_results = app.config.get('MAX_TRANSMIT_QUERY_LIMIT', 500)
    # ensure we publish in the correct sequence
    request.sort = '[("_created", 1), ("subscriber_id", 1), ("published_seq_num", 1)]'
    return get_resource_service(PUBLISH_QUEUE).get(req=request, lookup=lookup)


@celery.task(soft_time_limit=1800, bind=True)
def transmit_subscriber_items(self, queue_items, subscriber):
    # Attempt to obtain a lock for transmissions to the subscriber
    lock_name = get_lock_id("Subscriber", "Transmit", subscriber)
    if not lock(lock_name, expire=300):
        return

    for queue_item in queue_items:
        publish_queue_service = get_resource_service(PUBLISH_QUEUE)
        log_msg = '_id: {_id}  item_id: {item_id}  state: {state} ' \
                  'item_version: {item_version} headline: {headline}'.format(**queue_item)
        try:
            # update the status of the item to in-progress
            logger.info('Transmitting queue item {}'.format(log_msg))
            queue_update = {'state': 'in-progress', 'transmit_started_at': utcnow()}
            publish_queue_service.patch(queue_item.get(config.ID_FIELD), queue_update)
            destination = queue_item['destination']
            transmitter = superdesk.publish.registered_transmitters[destination.get('delivery_type')]
            transmitter.transmit(queue_item)
            logger.info('Transmitted queue item {}'.format(log_msg))
        except:
            logger.exception('Failed to transmit queue item {}'.format(log_msg))
            max_retry_attempt = app.config.get('MAX_TRANSMIT_RETRY_ATTEMPT')
            retry_attempt_delay = app.config.get('TRANSMIT_RETRY_ATTEMPT_DELAY_MINUTES')
            try:
                orig_item = publish_queue_service.find_one(req=None, _id=queue_item['_id'])
                updates = {config.LAST_UPDATED: utcnow()}
                if orig_item.get('retry_attempt', 0) < max_retry_attempt:
                    updates['retry_attempt'] = orig_item.get('retry_attempt', 0) + 1
                    updates['state'] = QueueState.RETRYING.value
                    updates['next_retry_attempt_at'] = utcnow() + timedelta(minutes=retry_attempt_delay)
                else:
                    # all retry attempts exhausted marking the item as failed.
                    updates['state'] = QueueState.FAILED.value

                publish_queue_service.system_update(orig_item.get(config.ID_FIELD), updates, orig_item)
            except:
                logger.error('Failed to set the state for failed publish queue item {}.'.format(queue_item['_id']))

    # Release the lock for the subscriber
    lock_name = get_lock_id("Subscriber", "Transmit", subscriber)
    unlock(lock_name)


def transmit_items(queue_items):
    # get a distinct list of the subscribers that have queued items
    subscribers = list(set([q['subscriber_id'] for q in queue_items]))
    # extract the queued items for each subscriber and transmit them
    for subscriber in subscribers:
        sub_queue_items = [item for item in queue_items if item['subscriber_id'] == subscriber]
        transmit_subscriber_items.apply_async(kwargs={'queue_items': sub_queue_items, 'subscriber': str(subscriber)})


superdesk.command('publish:transmit', PublishContent())
