"""
Patient / partner notifications.

In-app notifications are rows in `Notification` and are created inside the
caller's transaction, so they appear atomically with the status change.

Push notifications go out *after* the transaction commits (never notify about a
change that then rolls back) through a pluggable backend:

    PUSH_NOTIFICATION_BACKEND = 'bookings.notifications.LoggingPushBackend'   # default

A real backend (FCM, OneSignal, Expo …) implements `send(user, title, body, data)`
and returns True (sent) / False (no device registered). Raising marks the
notification FAILED; it never breaks the request.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils.module_loading import import_string

from .models import Notification

logger = logging.getLogger(__name__)


class LoggingPushBackend:
    """Default backend: no push provider configured — log and mark SKIPPED."""

    def send(self, user, title, body, data=None):
        logger.info("push(skipped, no provider) user=%s title=%r data=%s", getattr(user, 'pk', None), title, data)
        return False


def _backend():
    path = getattr(settings, 'PUSH_NOTIFICATION_BACKEND', 'bookings.notifications.LoggingPushBackend')
    return import_string(path)()


def _deliver_push(notification_id):
    notif = Notification.objects.select_related('user', 'booking').filter(pk=notification_id).first()
    if not notif:
        return
    data = {'type': notif.notification_type, 'notification_id': notif.pk}
    if notif.booking_id:
        data.update(booking_id=notif.booking.booking_id, booking_pk=notif.booking_id)
    try:
        sent = _backend().send(notif.user, notif.title, notif.message, data)
        notif.push_status = 'SENT' if sent else 'SKIPPED'
        notif.push_error = ''
    except Exception as exc:  # a push outage must never fail the booking update
        logger.exception("push delivery failed for notification %s", notification_id)
        notif.push_status = 'FAILED'
        notif.push_error = str(exc)[:255]
    notif.save(update_fields=['push_status', 'push_error'])


def notify(user, notification_type, title, message, booking=None, push=False):
    """Create an in-app notification; optionally queue a push for after commit."""
    if user is None:
        return None
    notif = Notification.objects.create(
        user=user,
        booking=booking,
        notification_type=notification_type,
        title=title,
        message=message,
        push_status='PENDING' if push else 'NOT_REQUESTED',
    )
    if push:
        transaction.on_commit(lambda: _deliver_push(notif.pk))
    return notif
